"""OpenAI speech provider used as the current mobile backend."""

from __future__ import annotations

import asyncio
import io
import os
import wave
from fractions import Fraction
from typing import Any, Dict, Sequence

import httpx
from av import AudioFrame, open as av_open
from av.audio.resampler import AudioResampler
from openai import OpenAI

from app.logging_utils import logger

__all__ = ["OpenAISpeechProvider"]

TTS_ENDPOINT = "https://api.openai.com/v1/audio/speech"
DEFAULT_TTS_MODEL = "tts-1"
DEFAULT_TTS_VOICE = "alloy"
HTTP_TIMEOUT = 60.0
DEFAULT_TRANSCRIPTION_MODEL = os.getenv("VOICE_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
DEFAULT_SAMPLE_RATE_HZ = 48_000


class OpenAISpeechProvider:
    """Concrete speech provider backed by OpenAI APIs."""

    def __init__(self) -> None:
        self._client: OpenAI | None = None

    async def synthesize(
        self,
        text: str,
        *,
        voice: str,
        prosody: Dict[str, Any] | None = None,
        language: str | None = None,  # noqa: ARG002 - OpenAI ignores language override
    ) -> Sequence[AudioFrame]:  # noqa: D401 - interface docs live in base protocol
        if not text.strip():
            return ()

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")

        payload: Dict[str, Any] = {
            "model": os.getenv("VOICE_TTS_MODEL", DEFAULT_TTS_MODEL),
            "input": text,
            "voice": voice or DEFAULT_TTS_VOICE,
            "format": "wav",
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(TTS_ENDPOINT, headers=headers, json=payload)
            response.raise_for_status()

        return _decode_audio(response.content)

    async def transcribe(
        self,
        pcm_bytes: bytes,
        sample_rate_hz: int,
        *,
        language: str | None = None,  # noqa: ARG002 - language currently unused
        model: str | None = None,
    ) -> str:  # noqa: D401
        if not pcm_bytes:
            return ""

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate_hz or DEFAULT_SAMPLE_RATE_HZ)
            handle.writeframes(pcm_bytes)

        buffer.seek(0)
        buffer.name = "utterance.wav"

        client = self._get_client()

        try:
            transcription_model = model or os.getenv(
                "VOICE_TRANSCRIPTION_MODEL",
                DEFAULT_TRANSCRIPTION_MODEL,
            )
            result = await asyncio.to_thread(
                client.audio.transcriptions.create,
                model=transcription_model,
                file=buffer,
            )
        except Exception as exc:  # noqa: BLE001 - surface upstream
            logger.error("Transcription failed via OpenAI: %s", exc)
            return ""

        text = getattr(result, "text", None)
        if not text and isinstance(result, dict):
            text = result.get("text")
        return text.strip() if text else ""

    def _get_client(self) -> OpenAI:
        client = self._client
        if client is None:
            client = OpenAI()
            self._client = client
        return client


def _decode_audio(data: bytes) -> Sequence[AudioFrame]:
    container = av_open(io.BytesIO(data))
    try:
        stream = next(s for s in container.streams if s.type == "audio")
    except StopIteration as exc:  # pragma: no cover - defensive programming
        container.close()
        raise ValueError("No audio stream in synthesized output") from exc

    resampler = AudioResampler(format="s16", layout="mono", rate=DEFAULT_SAMPLE_RATE_HZ)
    frames: list[AudioFrame] = []
    for frame in container.decode(stream):
        resampled_frames = resampler.resample(frame)
        if resampled_frames is None:
            continue
        for resampled in resampled_frames:
            resampled.pts = None
            resampled.sample_rate = DEFAULT_SAMPLE_RATE_HZ
            resampled.time_base = Fraction(1, DEFAULT_SAMPLE_RATE_HZ)
            frames.append(resampled)

    container.close()
    if not frames:
        raise ValueError("Synthesized audio did not produce any frames")
    return frames
