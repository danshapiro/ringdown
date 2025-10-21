import asyncio
import os
import wave
from io import BytesIO

import pytest

from app.speech.providers import OpenAISpeechProvider, get_speech_provider
from app.speech.providers.google import GoogleSpeechProvider


@pytest.fixture(autouse=True)
def _set_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def _make_wav_bytes(duration_samples: int = 480) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48_000)
        handle.writeframes(b"\x00\x00" * duration_samples)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_openai_synthesize(monkeypatch):
    wav_bytes = _make_wav_bytes()

    class _DummyResponse:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None:
            return None

    class _DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *_args, **_kwargs):
            return _DummyResponse(wav_bytes)

    monkeypatch.setattr("app.speech.providers.openai.httpx.AsyncClient", _DummyClient)

    provider = OpenAISpeechProvider()
    frames = await provider.synthesize("hello", voice="alloy")

    assert frames, "Expected synthesized frames"
    assert frames[0].samples > 0


@pytest.mark.asyncio
async def test_openai_transcribe(monkeypatch):
    provider = OpenAISpeechProvider()

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    class _StubTranscriptions:
        def create(self, *args, **kwargs):
            return {"text": "transcribed text"}

    class _StubClient:
        def __init__(self) -> None:
            self.audio = type("_Audio", (), {"transcriptions": _StubTranscriptions()})()

    monkeypatch.setattr("app.speech.providers.openai.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr(OpenAISpeechProvider, "_get_client", lambda self: _StubClient())

    pcm_bytes = b"\x00\x01" * 480
    text = await provider.transcribe(pcm_bytes, 48_000)

    assert text == "transcribed text"


def test_get_speech_provider_defaults():
    default = get_speech_provider({})
    override = get_speech_provider({"tts_provider": "OpenAI"})
    assert default is override


def test_get_speech_provider_unknown():
    provider = get_speech_provider({"tts_provider": "acme", "transcription_provider": "acme"})
    assert isinstance(provider, OpenAISpeechProvider)


def test_get_speech_provider_google(monkeypatch):
    provider = get_speech_provider({"tts_provider": "google", "language": "en-US"})
    assert isinstance(provider, GoogleSpeechProvider)


@pytest.mark.asyncio
async def test_google_synthesize(monkeypatch):
    provider = GoogleSpeechProvider(default_language="en-US")
    pcm_bytes = b"\x00\x01" * 960

    class _StubTTS:
        def synthesize_speech(self, request):
            self.last_request = request
            return type("Resp", (), {"audio_content": pcm_bytes})()

    provider._tts_client = _StubTTS()

    async def fake_to_thread(func, *args, **kwargs):
        return func()

    monkeypatch.setattr("app.speech.providers.google.asyncio.to_thread", fake_to_thread)

    frames = await provider.synthesize("hello", voice="en-US-Chirp3-HD-Aoede")

    assert frames, "Expected frames from Google TTS"
    assert frames[0].sample_rate == 48_000
    assert provider._tts_client.last_request.voice.name == "en-US-Chirp3-HD-Aoede"
    assert provider._tts_client.last_request.voice.language_code == "en-US"


@pytest.mark.asyncio
async def test_google_transcribe(monkeypatch):
    provider = GoogleSpeechProvider(default_language="en-US")

    class _Alt:
        def __init__(self, transcript: str) -> None:
            self.transcript = transcript

    class _Result:
        def __init__(self, *alts):
            self.alternatives = list(alts)

    class _Response:
        def __init__(self, *results):
            self.results = list(results)

    class _StubSpeech:
        def recognize(self, config, audio):
            self.last_config = config
            return _Response(_Result(_Alt("hello world")))

    provider._stt_client = _StubSpeech()

    async def fake_to_thread(func, *args, **kwargs):
        return func()

    monkeypatch.setattr("app.speech.providers.google.asyncio.to_thread", fake_to_thread)

    text = await provider.transcribe(b"\x00\x01" * 960, 48_000)
    assert text == "hello world"
    assert provider._stt_client.last_config.language_code == "en-US"
    assert provider._stt_client.last_config.model == "chirp"
