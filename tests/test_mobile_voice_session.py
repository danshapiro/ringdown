from types import SimpleNamespace

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


@pytest.fixture(autouse=True)
def _stub_openai_client(monkeypatch):
    class _Transcriptions:
        def create(self, *args, **kwargs):
            raise AssertionError("Unexpected transcription call")

    dummy_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=_Transcriptions()))
    monkeypatch.setattr(mobile_api, "_get_openai_client", lambda: dummy_client)


@pytest.mark.asyncio
async def test_voice_session_start_adds_greeting(monkeypatch):
    track = _StubTrack()
    session = MobileVoiceSession("device-1", {"agent": "unknown-caller"}, track)

    captured: list[str] = []

    async def fake_speak(self, text: str) -> None:
        captured.append(text)

    monkeypatch.setattr(MobileVoiceSession, "_speak", fake_speak)

    await session.start()

    assert captured, "Expected greeting to be spoken"
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == captured[-1]


@pytest.mark.asyncio
async def test_generate_response_handles_tool_marker(monkeypatch):
    track = _StubTrack()
    session = MobileVoiceSession("device-2", {"agent": "unknown-caller"}, track)

    spoken: list[str] = []

    async def fake_speak(self, text: str) -> None:
        spoken.append(text)

    monkeypatch.setattr(MobileVoiceSession, "_speak", fake_speak)

    async def fake_stream(user_text, agent_cfg, messages):
        yield {"type": "tool_executing"}
        yield "All set."

    monkeypatch.setattr(mobile_api, "stream_response", fake_stream)

    result = await session._generate_response("status?")

    assert result == "All set."
    assert "moment while I work on that" in spoken[0]


@pytest.mark.asyncio
async def test_process_chunk_updates_conversation(monkeypatch):
    track = _StubTrack()
    session = MobileVoiceSession("device-3", {"agent": "unknown-caller"}, track)

    async def fake_transcribe(self, pcm_bytes: bytes) -> str:
        return "hello bot"

    async def fake_generate(self, user_text: str) -> str:
        assert user_text == "hello bot"
        return "greetings human"

    async def fake_speak(self, text: str) -> None:
        track.enqueue(text)

    monkeypatch.setattr(MobileVoiceSession, "_transcribe", fake_transcribe)
    monkeypatch.setattr(MobileVoiceSession, "_generate_response", fake_generate)
    monkeypatch.setattr(MobileVoiceSession, "_speak", fake_speak)

    pcm_bytes = b"\x01\x00" * (session._min_samples + 10)

    await session._process_chunk(pcm_bytes)

    assert session.messages[-2]["content"] == "hello bot"
    assert session.messages[-1]["content"] == "greetings human"
    assert track.frames[-1] == "greetings human"
