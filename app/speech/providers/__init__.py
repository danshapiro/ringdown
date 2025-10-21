"""Speech provider factory and base protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Protocol, Sequence

from app.logging_utils import logger

from .openai import OpenAISpeechProvider
from .google import GoogleSpeechProvider

if TYPE_CHECKING:  # pragma: no cover
    from av.audio.frame import AudioFrame


class SpeechProvider(Protocol):
    """Contract for text-to-speech and speech-to-text backends."""

    async def synthesize(
        self,
        text: str,
        *,
        voice: str,
        prosody: Dict[str, Any] | None = None,
        language: str | None = None,
    ) -> Sequence["AudioFrame"]:
        """Return audio frames containing synthesized speech for *text*."""

    async def transcribe(
        self,
        pcm_bytes: bytes,
        sample_rate_hz: int,
        *,
        language: str | None = None,
        model: str | None = None,
    ) -> str:
        """Return text transcription for PCM audio at *sample_rate_hz*."""


@dataclass(frozen=True)
class _ProviderKey:
    tts: str
    stt: str
    language: str
    speech_model: str

    @classmethod
    def from_agent_cfg(cls, agent_cfg: Dict[str, Any]) -> "_ProviderKey":
        language = agent_cfg.get("language") or "en-US"
        speech_model = agent_cfg.get("speech_model") or ""
        return cls(
            tts=(agent_cfg.get("tts_provider") or "openai").lower(),
            stt=(agent_cfg.get("transcription_provider") or "openai").lower(),
            language=language,
            speech_model=speech_model,
        )


_PROVIDER_CACHE: Dict[_ProviderKey, SpeechProvider] = {}


def _fallback_key(reason: str, requested: _ProviderKey) -> _ProviderKey:
    logger.info("%s; defaulting to OpenAI backend (requested=%s)", reason, requested)
    return _ProviderKey("openai", "openai", requested.language, requested.speech_model)


def get_speech_provider(agent_cfg: Dict[str, Any]) -> SpeechProvider:
    """Return a speech provider matching *agent_cfg*.

    For now we fall back to the OpenAI provider until specific backends are
    implemented. This ensures the mobile client continues to function while we
    wire up Google Cloud parity (tracked in ringdown-14/15).
    """

    requested_key = _ProviderKey.from_agent_cfg(agent_cfg)
    key = requested_key

    if key.tts not in {"openai", "google"} or key.stt not in {"openai", "google"}:
        key = _fallback_key("Unknown speech provider combo", requested_key)

    provider = _PROVIDER_CACHE.get(key)
    if provider is None:
        if key.tts == "google" or key.stt == "google":
            provider = GoogleSpeechProvider(
                default_language=key.language,
                default_model=key.speech_model,
            )
        else:
            provider = OpenAISpeechProvider()
        _PROVIDER_CACHE[key] = provider
    return provider
