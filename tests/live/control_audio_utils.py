from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from pydub import AudioSegment


TARGET_SAMPLE_WIDTH_BYTES = 2


@dataclass(slots=True)
class ControlAudioPayload:
    audio_base64: str
    sample_rate_hz: int
    channels: int
    format: str = "wav"


def _normalise_segment(segment: AudioSegment, *, sample_rate: int, channels: int) -> AudioSegment:
    """Match the expected sample rate, width, and channel layout."""

    normalised = segment.set_frame_rate(sample_rate)
    normalised = normalised.set_channels(channels)
    if normalised.sample_width != TARGET_SAMPLE_WIDTH_BYTES:
        normalised = normalised.set_sample_width(TARGET_SAMPLE_WIDTH_BYTES)
    return normalised


def audiosegment_to_base64_wav(segment: AudioSegment, *, sample_rate: int = 16_000, channels: int = 1) -> str:
    """Encode an AudioSegment into a base64 WAV string."""

    normalised = _normalise_segment(segment, sample_rate=sample_rate, channels=channels)
    buffer = io.BytesIO()
    normalised.export(buffer, format="wav")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def base64_wav_to_audiosegment(payload_base64: str) -> AudioSegment:
    """Decode a base64 WAV string into an AudioSegment."""

    raw_bytes = base64.b64decode(payload_base64)
    buffer = io.BytesIO(raw_bytes)
    return AudioSegment.from_file(buffer, format="wav")


def encode_file_for_control_channel(path: Path, *, sample_rate: int = 16_000, channels: int = 1) -> ControlAudioPayload:
    """Load an audio file and produce a control-channel payload."""

    if not path.exists():
        raise FileNotFoundError(path)

    segment = AudioSegment.from_file(path)
    audio_base64 = audiosegment_to_base64_wav(segment, sample_rate=sample_rate, channels=channels)
    return ControlAudioPayload(
        audio_base64=audio_base64,
        sample_rate_hz=sample_rate,
        channels=channels,
        format="wav",
    )
