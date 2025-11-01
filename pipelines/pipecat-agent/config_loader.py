import sys
import yaml
from loguru import logger

from pipecat.services.gemini_multimodal_live.gemini import (
    InputParams,
    GeminiMultimodalModalities,
    GeminiMediaResolution,
    GeminiVADParams,
    ContextWindowCompressionParams,
)
from pipecat.services.gemini_multimodal_live import events as gemini_events
from pipecat.transcriptions.language import Language

CONFIG_FILE_PATH = "../config.yaml"


def _get_required_value(config_dict, key, context="config"):
    value = config_dict.get(key)
    if value is None:
        logger.error(
            f"Missing required key '{key}' in {context} section of {CONFIG_FILE_PATH}"
        )
        sys.exit(1)
    return value


def load_config(config_path: str = CONFIG_FILE_PATH) -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if not isinstance(config, dict):
            raise yaml.YAMLError("Config file is not a valid dictionary.")
        logger.info(f"Loaded configuration from {config_path}")
    except FileNotFoundError:
        logger.error(f"Configuration file not found at {config_path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"Error parsing configuration file {config_path}: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        logger.error(
            f"An unexpected error occurred loading configuration from {config_path}: {e}"
        )
        sys.exit(1)

    system_instruction = _get_required_value(config, "system_instruction")
    proactive_intro_text = _get_required_value(config, "proactive_intro_text")
    proactive_greeting_delay_s = float(
        _get_required_value(config, "proactive_greeting_delay_s")
    )
    output_buffer_flush_delay = float(
        _get_required_value(config, "output_buffer_flush_delay")
    )

    gemini_conf = _get_required_value(config, "gemini_config", "gemini_config section")
    if not isinstance(gemini_conf, dict):
        logger.error(
            f"'gemini_config' section in {config_path} must be a dictionary."
        )
        sys.exit(1)

    gemini_model = _get_required_value(gemini_conf, "model", "gemini_config")
    voice_id = _get_required_value(gemini_conf, "voice_id", "gemini_config")
    transcribe_user_audio = bool(
        _get_required_value(gemini_conf, "transcribe_user_audio", "gemini_config")
    )
    inference_on_context_init = bool(
        _get_required_value(
            gemini_conf, "inference_on_context_initialization", "gemini_config"
        )
    )

    tools_conf = gemini_conf.get("tools", {})
    if not isinstance(tools_conf, dict):
        logger.warning(
            f"'tools' section in gemini_config should be a dictionary. Ignoring value: {tools_conf}"
        )
        tools_conf = {}

    gemini_tools_list = []
    if tools_conf.get("enable_google_search", False):
        gemini_tools_list.append({"google_search": {}})
        logger.info("Google Search grounding enabled.")

    gen_params_conf = _get_required_value(gemini_conf, "generation_params", "gemini_config")
    if not isinstance(gen_params_conf, dict):
        logger.error(
            f"'generation_params' section in {config_path} must be a dictionary."
        )
        sys.exit(1)

    try:
        modalities_str = gen_params_conf.get("modalities", "AUDIO").upper()
        gemini_modalities = GeminiMultimodalModalities[modalities_str]
    except KeyError:
        logger.error(
            f"Invalid modalities value '{modalities_str}' in config. Must be AUDIO or TEXT."
        )
        sys.exit(1)

    try:
        language_str = gen_params_conf.get("language", "EN_US").upper()
        gemini_language = Language[language_str]
    except KeyError:
        logger.error(
            f"Invalid language value '{language_str}' in config. See Language enum in pipecat."
        )
        sys.exit(1)

    try:
        media_res_str = gen_params_conf.get("media_resolution", "UNSPECIFIED").upper()
        gemini_media_resolution = GeminiMediaResolution[media_res_str]
    except KeyError:
        logger.error(
            f"Invalid media_resolution '{media_res_str}' in config. See GeminiMediaResolution enum."
        )
        sys.exit(1)

    gemini_temp = gen_params_conf.get("temperature")
    gemini_top_p = gen_params_conf.get("top_p")
    gemini_top_k = gen_params_conf.get("top_k")
    gemini_max_tokens = gen_params_conf.get("max_tokens", 4096)
    gemini_freq_penalty = gen_params_conf.get("frequency_penalty")
    gemini_pres_penalty = gen_params_conf.get("presence_penalty")

    vad_conf = gen_params_conf.get("vad", {})
    vad_params = None
    if isinstance(vad_conf, dict) and vad_conf.get("enabled", True):
        vad_params_dict = {}
        if "disabled" in vad_conf:
            vad_params_dict["disabled"] = bool(vad_conf["disabled"])
        if "start_sensitivity" in vad_conf:
            try:
                vad_params_dict["start_sensitivity"] = gemini_events.StartSensitivity[
                    vad_conf["start_sensitivity"].upper()
                ]
            except KeyError:
                logger.error(
                    f"Invalid VAD start_sensitivity: {vad_conf['start_sensitivity']}"
                )
                sys.exit(1)
        if "end_sensitivity" in vad_conf:
            try:
                vad_params_dict["end_sensitivity"] = gemini_events.EndSensitivity[
                    vad_conf["end_sensitivity"].upper()
                ]
            except KeyError:
                logger.error(
                    f"Invalid VAD end_sensitivity: {vad_conf['end_sensitivity']}"
                )
                sys.exit(1)
        if "prefix_padding_ms" in vad_conf:
            vad_params_dict["prefix_padding_ms"] = int(vad_conf["prefix_padding_ms"])
        if "silence_duration_ms" in vad_conf:
            vad_params_dict["silence_duration_ms"] = int(
                vad_conf["silence_duration_ms"]
            )

        if vad_params_dict:
            vad_params = GeminiVADParams(**vad_params_dict)

    compression_conf = gen_params_conf.get("context_window_compression", {})
    compression_params = None
    if isinstance(compression_conf, dict) and compression_conf.get("enabled", False):
        compression_params_dict = {"enabled": True}
        if (
            "trigger_tokens" in compression_conf
            and compression_conf["trigger_tokens"] is not None
        ):
            try:
                compression_params_dict["trigger_tokens"] = int(
                    compression_conf["trigger_tokens"]
                )
            except (ValueError, TypeError):
                logger.error(
                    f"Invalid trigger_tokens value: {compression_conf['trigger_tokens']}. Must be an integer."
                )
                sys.exit(1)
        compression_params = ContextWindowCompressionParams(**compression_params_dict)

    gemini_extra_params = gen_params_conf.get("extra", {})
    if not isinstance(gemini_extra_params, dict):
        logger.warning(
            f"'extra' parameters in generation_params should be a dictionary. Ignoring value: {gemini_extra_params}"
        )
        gemini_extra_params = {}

    gemini_input_params = InputParams(
        modalities=gemini_modalities,
        language=gemini_language,
        temperature=gemini_temp,
        top_p=gemini_top_p,
        top_k=gemini_top_k,
        max_tokens=gemini_max_tokens,
        frequency_penalty=gemini_freq_penalty,
        presence_penalty=gemini_pres_penalty,
        media_resolution=gemini_media_resolution,
        vad=vad_params,
        context_window_compression=compression_params,
        extra=gemini_extra_params,
    )

    return {
        "system_instruction": system_instruction,
        "proactive_intro_text": proactive_intro_text,
        "proactive_greeting_delay_s": proactive_greeting_delay_s,
        "output_buffer_flush_delay": output_buffer_flush_delay,
        "gemini_model": gemini_model,
        "voice_id": voice_id,
        "transcribe_user_audio": transcribe_user_audio,
        "inference_on_context_init": inference_on_context_init,
        "gemini_tools_list": gemini_tools_list if gemini_tools_list else None,
        "gemini_input_params": gemini_input_params,
    }
