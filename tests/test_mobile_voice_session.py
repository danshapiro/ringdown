import pytest

import app.api.mobile as mobile_api
from app.api.mobile import MobileVoiceSession


class _StubTrack:
    def __init__(self) -> None:
        self.frames = []
        self.closed = False

    def enqueue(self, frame) -> None:
        self.frames.append(frame)

    def close(self) -> None:
        self.closed = True


class _FakeSpeechProvider:
    def __init__(self) -> None:
        self.synth_calls: list[dict[str, str | None]] = []
        self.transcribe_calls: list[bytes] = []
        self.next_transcript: str = ""

    async def synthesize(self, text: str, *, voice: str, prosody=None, language=None):
        self.synth_calls.append(
            {"text": text, "voice": voice, "prosody": prosody, "language": language}
        )
        return [text]

    async def transcribe(self, pcm_bytes: bytes, sample_rate_hz: int, *, language=None, model=None):
        self.transcribe_calls.append(pcm_bytes)
        return self.next_transcript


@pytest.fixture
def fake_provider(monkeypatch):
    provider = _FakeSpeechProvider()
    monkeypatch.setattr(mobile_api, "get_speech_provider", lambda *_args, **_kwargs: provider)
    return provider


@pytest.mark.asyncio
async def test_voice_session_start_adds_greeting(fake_provider):
    track = _StubTrack()
    session = MobileVoiceSession("device-1", {"agent": "unknown-caller"}, track)

    expected_greeting = session._resolve_greeting()

    await session.start()

    assert fake_provider.synth_calls, "Expected greeting to be spoken"
    assert fake_provider.synth_calls[0]["text"] == expected_greeting
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == fake_provider.synth_calls[-1]["text"]


@pytest.mark.asyncio
async def test_generate_response_handles_tool_marker(monkeypatch, fake_provider):
    track = _StubTrack()
    session = MobileVoiceSession("device-2", {"agent": "unknown-caller"}, track)

    async def fake_stream(user_text, agent_cfg, messages):
        yield {"type": "tool_executing"}
        yield "All set."

    monkeypatch.setattr(mobile_api, "stream_response", fake_stream)

    result = await session._generate_response("status?")

    assert result == "All set."
    assert "moment while I work on that" in fake_provider.synth_calls[0]["text"]


@pytest.mark.asyncio
async def test_process_chunk_updates_conversation(monkeypatch, fake_provider):
    track = _StubTrack()
    session = MobileVoiceSession("device-3", {"agent": "unknown-caller"}, track)

    async def fake_generate(self, user_text: str) -> str:
        assert user_text == "hello bot"
        return "greetings human"

    fake_provider.next_transcript = "hello bot"

    monkeypatch.setattr(MobileVoiceSession, "_generate_response", fake_generate)

    pcm_bytes = b"\x01\x00" * (session._min_samples + 10)

    await session._process_chunk(pcm_bytes)

    assert session.messages[-2]["content"] == "hello bot"
    assert session.messages[-1]["content"] == "greetings human"
    assert track.frames[-1] == "greetings human"
