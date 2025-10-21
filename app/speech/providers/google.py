"""Google Cloud speech provider implementation."""

from __future__ import annotations

import asyncio
from fractions import Fraction
from typing import Any, Dict, Optional, Sequence

import numpy as np
from av import AudioFrame
from google.cloud import speech, texttospeech

from app.logging_utils import logger

SAMPLE_RATE_HZ = 48_000
_SAMPLES_PER_FRAME = int(SAMPLE_RATE_HZ * 0.02)


def _resolve_language(voice: str | None, fallback: str) -> str:
    if voice:
        parts = voice.split("-")
        if len(parts) >= 2:
            return "-".join(parts[:2])
    return fallback


def _prosody_to_rate(prosody: Dict[str, Any] | None) -> float:
    if not prosody:
        return 1.0
    rate_val = prosody.get("rate")
    if rate_val is None:
        return 1.0
    try:
        if isinstance(rate_val, str):
            text = rate_val.strip().lower()
            if text.endswith("%"):
                return max(0.25, min(4.0, float(text.rstrip("%")) / 100.0))
            mapping = {
                "x-slow": 0.7,
                "slow": 0.85,
                "medium": 1.0,
                "fast": 1.15,
                "x-fast": 1.3,
            }
            if text in mapping:
                return mapping[text]
            return float(text)
        return float(rate_val)
    except Exception:  # pragma: no cover - defensive parsing
        logger.warning("Failed to parse speaking rate from prosody: %s", rate_val)
        return 1.0


def _prosody_to_pitch(prosody: Dict[str, Any] | None) -> float:
    if not prosody:
        return 0.0
    value = prosody.get("pitch")
    if value is None:
        return 0.0
    try:
        if isinstance(value, str):
            text = value.strip().lower()
            if text.endswith("st"):
                return float(text.rstrip("st"))
            if text.endswith("%"):
                percent = float(text.rstrip("%"))
                return max(-20.0, min(20.0, percent / 7.0))  # heuristic
            return float(text)
        return float(value)
    except Exception:  # pragma: no cover - defensive parsing
        logger.warning("Failed to parse pitch from prosody: %s", value)
        return 0.0


def _pcm_to_frames(pcm_bytes: bytes) -> Sequence[AudioFrame]:
    if not pcm_bytes:
        return []

    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    frames: list[AudioFrame] = []
    timestamp = 0

    for start in range(0, len(samples), _SAMPLES_PER_FRAME):
        chunk = samples[start : start + _SAMPLES_PER_FRAME]
        if not len(chunk):
            continue
        frame = AudioFrame(format="s16", layout="mono", samples=len(chunk))
        frame.pts = timestamp
        frame.sample_rate = SAMPLE_RATE_HZ
        frame.time_base = Fraction(1, SAMPLE_RATE_HZ)
        frame.planes[0].update(chunk.tobytes())
        timestamp += len(chunk)
        frames.append(frame)

    return frames


def _normalize_model(model: str | None) -> str:
    if not model:
        return "chirp"
    lowered = model.lower()
    if lowered in {"telephony", "default"}:
        return "chirp"
    return model


class GoogleSpeechProvider:
    """Speech provider backed by Google Cloud Text-to-Speech and Speech-to-Text."""

    def __init__(self, *, default_language: str, default_model: str | None = None) -> None:
        self._default_language = default_language or "en-US"
        self._default_model = _normalize_model(default_model)
        self._tts_client: Optional[texttospeech.TextToSpeechClient] = None
        self._stt_client: Optional[speech.SpeechClient] = None

    async def synthesize(
        self,
        text: str,
        *,
        voice: str,
        prosody: Dict[str, Any] | None = None,
        language: str | None = None,
    ) -> Sequence[AudioFrame]:  # noqa: D401
        if not text.strip():
            return []

        language_code = language or _resolve_language(voice, self._default_language)

        speaking_rate = _prosody_to_rate(prosody)
        pitch = _prosody_to_pitch(prosody)

        def _call_tts() -> texttospeech.SynthesizeSpeechResponse:
            client = self._tts_client
            if client is None:
                client = texttospeech.TextToSpeechClient()
                self._tts_client = client

            synthesis_input = texttospeech.SynthesisInput(text=text)
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=SAMPLE_RATE_HZ,
                speaking_rate=speaking_rate,
                pitch=pitch,
            )

            return client.synthesize_speech(
                request=texttospeech.SynthesizeSpeechRequest(
                    input=synthesis_input,
                    voice=voice_params,
                    audio_config=audio_config,
                )
            )

        try:
            response = await asyncio.to_thread(_call_tts)
        except Exception as exc:  # noqa: BLE001 - capture provider failures
            logger.error("Google TTS failed: %s", exc)
            return []

        audio_bytes = response.audio_content
        if audio_bytes:
            amplitudes = np.frombuffer(audio_bytes, dtype=np.int16)
            mean_amp = float(np.abs(amplitudes).mean()) if amplitudes.size else 0.0
            logger.debug(
                "Google TTS returned %d bytes (mean amplitude %.2f) for language=%s voice=%s",
                len(audio_bytes),
                mean_amp,
                language_code,
                voice,
            )

        return _pcm_to_frames(audio_bytes)

    async def transcribe(
        self,
        pcm_bytes: bytes,
        sample_rate_hz: int,
        *,
        language: str | None = None,
        model: str | None = None,
    ) -> str:  # noqa: D401
        if not pcm_bytes:
            return ""

        language_code = language or self._default_language
        model_name = _normalize_model(model) or self._default_model

        def _call_stt() -> speech.RecognizeResponse:
            client = self._stt_client
            if client is None:
                client = speech.SpeechClient()
                self._stt_client = client

            audio = speech.RecognitionAudio(content=pcm_bytes)
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate_hz,
                language_code=language_code,
                model=model_name,
                audio_channel_count=1,
                enable_automatic_punctuation=True,
            )

            return client.recognize(config=config, audio=audio)

        try:
            response = await asyncio.to_thread(_call_stt)
        except Exception as exc:  # noqa: BLE001 - capture provider failures
            logger.error("Google STT failed: %s", exc)
            return ""

        for result in response.results:
            if result.alternatives:
                transcript = result.alternatives[0].transcript.strip()
                if transcript:
                    return transcript
        return ""
