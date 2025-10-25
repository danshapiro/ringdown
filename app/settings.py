import copy
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import logging
import os
import yaml
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from log_love import setup_logging

from .config_schema import ConfigModel, resolve_config_path


def get_programmatic_tool_prompts() -> Dict[str, str]:
    """Get tool prompts from the tool registry for interpolation."""
    from .tool_framework import TOOL_REGISTRY
    prompts = {}
    for tool_name, tool_spec in TOOL_REGISTRY.items():
        if tool_spec.prompt:
            prompts[tool_name] = tool_spec.prompt
    return prompts


def build_tool_prompts_for_agent(agent_tools: List[str], tool_header: str) -> str:
    """Build combined tool prompts for an agent based on enabled tools."""
    parts = []
    
    # Add the tool header first
    if tool_header:
        parts.append(tool_header.strip())
    
    # Add prompts for each enabled tool
    programmatic_prompts = get_programmatic_tool_prompts()
    for tool_name in agent_tools:
        if tool_name in programmatic_prompts:
            parts.append(programmatic_prompts[tool_name].strip())
    
    return "\n\n".join(parts)


class EnvSettings(BaseSettings):
    """Secrets and environment-specific values.

    Reads from process environment first; falls back to `.env` file if present.

    The default values keep unit tests self-contained by avoiding hard
    dependencies on developer machines exporting real credentials. Runtime
    deployments are expected to override these defaults via the environment.
    """

    openai_api_key: str = Field(
        alias="OPENAI_API_KEY",
        description="API key used for OpenAI requests in production.",
        min_length=1,
    )
    twilio_auth_token: str = Field(
        alias="TWILIO_AUTH_TOKEN",
        description="Signing secret for validating Twilio webhooks.",
        min_length=1,
    )
    twilio_account_sid: str | None = Field(default=None, alias="TWILIO_ACCOUNT_SID")
    live_test_to_number: str | None = Field(default=None, alias="LIVE_TEST_TO_NUMBER")
    sqlite_path: str = "/data/memory.db"
    tavily_api_key: str | None = Field(default=None, alias="TAVILY_API_KEY")

    # Pydantic v2: redact sensitive fields from __repr__, pick env file.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        redact=True,
        extra="ignore",  # Ignore unexpected env vars like GOOGLE_API_KEY during tests
    )


# Module logger
logger = setup_logging()


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


@lru_cache
def get_env() -> EnvSettings:
    """Return cached environment settings with sensitive values redacted in logs."""

    import re

    try:
        env_settings = EnvSettings()
    except ValidationError as exc:  # noqa: BLE001 – fail fast when credentials are absent
        missing_fields: list[str] = []
        for err in exc.errors():
            if err.get("type") in {"missing", "string_too_short"}:
                field = err.get("loc", ("",))[-1]
                if isinstance(field, str):
                    missing_fields.append(field)

        alias_map = {
            "openai_api_key": "OPENAI_API_KEY",
            "twilio_auth_token": "TWILIO_AUTH_TOKEN",
        }
        env_names = sorted({alias_map.get(name, name) for name in missing_fields})
        if env_names:
            logger.critical(
                "Required environment credentials missing: %s",
                ", ".join(env_names),
            )
        raise RuntimeError(
            "Missing required environment credentials; set the necessary environment variables."
        ) from exc

    redacted = {}
    for k, v in env_settings.model_dump().items():
        if re.search(r"(api|key|token)", k, re.IGNORECASE):
            redacted[k] = "REDACTED"
        else:
            redacted[k] = v

    logger.debug("Environment settings loaded: %s", redacted)
    return env_settings


# ---------------------------------------------------------------------------
# Agent configuration loading & routing helpers
# ---------------------------------------------------------------------------


@lru_cache
def _config_path() -> Path:
    """Resolve the configuration file path honouring environment overrides."""

    path = resolve_config_path(os.getenv("RINGDOWN_CONFIG_PATH"))
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found at {path}")

    if path.name == "config.example.yaml":
        logger.info(
            "Using config.example.yaml – copy to config.yaml for customised deployments."
        )
    else:
        logger.debug("Configuration loaded from %s", path)
    return path


@lru_cache
def _load_config_model() -> ConfigModel:
    """Load and validate the configuration using :mod:`pydantic`."""

    path = _config_path()
    with path.open("r", encoding="utf-8") as fp:
        raw_data: Dict[str, Any] = yaml.safe_load(fp) or {}

    try:
        return ConfigModel.model_validate(raw_data)
    except ValidationError as exc:  # pragma: no cover - error formatting
        logger.error("Invalid configuration in %s: %s", path, exc)
        raise ValueError(f"Configuration validation failed for {path}") from exc


@lru_cache
def _load_config() -> Dict[str, Any]:
    """Return the validated configuration as a plain dictionary."""

    return _load_config_model().model_dump(mode="python")


# ---------------------------------------------------------------------------
# Configuration cache management
# ---------------------------------------------------------------------------


def refresh_config_cache() -> None:
    """Clear cached configuration so subsequent calls reload from disk."""

    _config_path.cache_clear()
    _load_config.cache_clear()
    _load_config_model.cache_clear()


# ---------------------------------------------------------------------------
# Convenience helpers used by various modules
# ---------------------------------------------------------------------------


def get_default_bot_name() -> str:
    """Return the default *bot_name* from config.yaml (title-cased)."""

    cfg = _load_config()
    name: str | None = cfg.get("defaults", {}).get("bot_name")
    if not name:
        # Fallback – keep legacy placeholder rather than raising
        return "Botname"

    # Normalise capitalisation for TTS readability (e.g. "ringdown" -> "Ringdown")
    return name.strip().title()


def get_default_email() -> str:
    """Return the default email address from config defaults."""
    cfg = _load_config()
    email = cfg.get("defaults", {}).get("default_email")
    if not email:
        raise ValueError("default_email missing in config.yaml defaults")
    return str(email).strip()


def get_admin_emails() -> list[str]:
    """Return list of green-listed admin email patterns from config defaults."""
    cfg = _load_config()
    return cfg.get("defaults", {}).get("admin_emails", [])


def get_project_name() -> str:
    """Return the project identifier from config defaults."""
    cfg = _load_config()
    project = cfg.get("defaults", {}).get("project_name")
    return str(project).strip() if project else "Project"


def get_mobile_devices() -> Dict[str, Any]:
    """Return mapping of registered mobile devices."""

    cfg = _load_config()
    return cfg.get("mobile_devices", {})


def get_mobile_device(device_id: str) -> Dict[str, Any] | None:
    """Return configuration entry for a single device, if present."""

    devices = get_mobile_devices()
    return devices.get(device_id)


def get_mobile_realtime_config() -> Dict[str, Any]:
    """Return realtime session defaults for Android clients."""

    cfg = _load_config()
    realtime = cfg.get("mobile_realtime")
    if not realtime:
        raise ValueError("mobile_realtime missing in config.yaml")
    return copy.deepcopy(realtime)


def get_calendar_user_name() -> str:
    """Return the friendly user name referenced in calendar prompts."""
    cfg = _load_config()
    return str(cfg.get("defaults", {}).get("calendar_user_name", "User")).strip()


def _merge_with_defaults(agent_cfg: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Return `agent_cfg` overlaid on top of `defaults` (shallow)."""

    merged = defaults.copy()

    # Ignore explicit ``None`` overrides so defaults remain in effect.
    overlay = {k: v for k, v in (agent_cfg or {}).items() if v is not None}
    merged.update(overlay)

    # ------------------------------------------------------------------
    # Tool merging must happen BEFORE prompt interpolation
    # so that {ToolPrompts} uses the correct final tools list
    # ------------------------------------------------------------------

    default_tools = defaults.get("tools") or []
    agent_tools = agent_cfg.get("tools")

    if agent_tools is None:
        # No override – inherit defaults as-is
        merged["tools"] = default_tools
    else:
        if not isinstance(agent_tools, list):
            raise TypeError("`tools` must be list[str] when specified in agent config")

        # Determine if agent wants to purely override (subset of defaults) or extend.
        agent_set = set(agent_tools)
        default_set = set(default_tools)

        if agent_set == default_set:
            # Identical to defaults – no real override
            merged["tools"] = default_tools
        elif agent_set.issubset(default_set):
            # Pure subset – treat as explicit override
            merged["tools"] = agent_tools
        else:
            # Agent adds new tools – union with defaults, preserving order & uniqueness
            combined: list[str] = []
            seen: set[str] = set()

            for t in default_tools + agent_tools:
                if t not in seen:
                    combined.append(t)
                    seen.add(t)

            merged["tools"] = combined

    # ------------------------------------------------------------------
    # Prompt placeholder interpolation: `{ToolName}` -> defaults.tool_prompts[ToolName]
    # Now uses the correct final tools list for {ToolPrompts}
    # ------------------------------------------------------------------

    import re

    prompt_template = merged.get("prompt")

    if prompt_template:
        # Ensure prompt is a string (YAML block scalars already produce str)
        if isinstance(prompt_template, list):
            prompt_template = "\n".join(prompt_template)

        # Build mapping of available placeholder -> text
        tool_prompts: Dict[str, str] = {}
        # First, add programmatic prompts from tool registry
        tool_prompts.update(get_programmatic_tool_prompts())
        # Add the tool header from config
        if "tool_header" in defaults:
            tool_prompts["ToolHeader"] = defaults["tool_header"]
        if "tool_header" in merged:
            tool_prompts["ToolHeader"] = merged["tool_header"]
        
        # Build combined ToolPrompts based on agent's enabled tools
        # Now uses the correct merged tools list!
        final_agent_tools = merged.get("tools", [])
        tool_header = merged.get("tool_header", defaults.get("tool_header", ""))
        if final_agent_tools:
            tool_prompts["ToolPrompts"] = build_tool_prompts_for_agent(final_agent_tools, tool_header)
        
        # Legacy support: Then, add config-based prompts (can override programmatic ones)
        tool_prompts.update(defaults.get("tool_prompts", {}) or {})
        # Agent-specific overrides/extra prompts allowed
        tool_prompts.update(merged.get("tool_prompts", {}) or {})

        pattern = re.compile(r"\{([A-Za-z0-9_-]+)\}")

        def _sub(match: re.Match[str]):
            key = match.group(1)
            if key in tool_prompts:
                return tool_prompts[key].strip()
            # Leave unknown placeholders (e.g., dynamic ones like {time_utc}) intact.
            return match.group(0)

        merged["prompt"] = pattern.sub(_sub, prompt_template)

    return merged


def get_agent_config(agent_name: str) -> Dict[str, Any]:
    """Return fully-merged config for `agent_name` (defaults applied)."""

    cfg = _load_config()
    defaults = cfg["defaults"]
    agents = cfg["agents"]

    if agent_name not in agents:
        raise KeyError(f"Agent '{agent_name}' missing in config.yaml")

    merged = _merge_with_defaults(agents[agent_name], defaults)
    logger.debug("Agent '%s' config resolved: %s", agent_name, {k: (v[:20] + '...' if isinstance(v, str) and len(v) > 20 else v) for k, v in merged.items()})
    return merged


def get_agent_for_number(caller_number: str | None) -> tuple[str, Dict[str, Any]]:
    """Return (`agent_name`, merged_config) for an inbound `caller_number`.

    The first agent whose *phone_numbers* list contains `caller_number` wins.
    If no agent matches, the special 'unknown-caller' agent is used.
    """

    cfg = _load_config()
    defaults = cfg["defaults"]

    agents = cfg["agents"]

    for name, agent_cfg in agents.items():
        if caller_number and caller_number in (agent_cfg.get("phone_numbers") or []):
            return name, _merge_with_defaults(agent_cfg, defaults)

    # Fallback
    if "unknown-caller" not in agents:
        raise KeyError("'unknown-caller' agent must be defined in config.yaml")

    return "unknown-caller", _merge_with_defaults(agents["unknown-caller"], defaults)


def get_tools_list(agent_name: str) -> List[str]:
    """Return final list of tool names enabled for the given agent."""

    return get_agent_config(agent_name).get("tools", []) 
