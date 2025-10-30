from __future__ import annotations

import base64
import io

from pydub.generators import Sine
from pydub import AudioSegment
import pytest

from tests.live.control_audio_utils import (
    audiosegment_to_base64_wav,
    base64_wav_to_audiosegment,
    encode_file_for_control_channel,
)


def _make_test_tone(duration_ms: int = 500, frequency_hz: int = 440) -> AudioSegment:
    tone = Sine(frequency_hz).to_audio_segment(duration=duration_ms)
    return tone.set_frame_rate(16_000).set_sample_width(2).set_channels(1)


def test_audiosegment_roundtrip_preserves_samples() -> None:
    original = _make_test_tone()

    encoded = audiosegment_to_base64_wav(original)
    decoded = base64_wav_to_audiosegment(encoded)

    assert decoded.frame_rate == original.frame_rate
    assert decoded.channels == original.channels
    assert decoded.sample_width == original.sample_width

    original_samples = original.get_array_of_samples()
    decoded_samples = decoded.get_array_of_samples()
    assert len(decoded_samples) == len(original_samples)
    assert decoded_samples.tolist() == original_samples.tolist()


def test_encode_file_for_control_channel(tmp_path) -> None:
    tone = _make_test_tone()
    wav_path = tmp_path / "tone.wav"
    raw_buffer = io.BytesIO()
    tone.export(raw_buffer, format="wav")
    wav_path.write_bytes(raw_buffer.getvalue())

    payload = encode_file_for_control_channel(wav_path)
    assert payload.sample_rate_hz == 16_000
    assert payload.channels == 1
    assert payload.format == "wav"
    assert len(payload.audio_base64) > 0

    decoded = base64_wav_to_audiosegment(payload.audio_base64)
    assert decoded.frame_rate == 16_000
    assert decoded.channels == 1
    assert decoded.sample_width == 2

    # Sanity check base64 decodes to expected magic for RIFF/WAV.
    wav_bytes = base64.b64decode(payload.audio_base64)
    assert wav_bytes[:4] == b"RIFF"
