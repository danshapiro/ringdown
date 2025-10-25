from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from typing import Iterator

import pytest
import yaml
from fastapi.testclient import TestClient

from app import settings
from app.main import app
import app.api.mobile as mobile


@pytest.fixture
def isolated_mobile_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    project_root = Path(__file__).resolve().parents[1]
    source_config = project_root / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(source_config.read_text(encoding="utf-8"), encoding="utf-8")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    devices = data.setdefault("mobile_devices", {})
    devices["device-123"] = {
        "label": "Pixel 9",
        "agent": "unknown-caller",
        "enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    monkeypatch.setenv("RINGDOWN_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings.refresh_config_cache()
    try:
        yield config_path
    finally:
        settings.refresh_config_cache()


def _fake_secret() -> SimpleNamespace:
    return SimpleNamespace(
        value="test-secret",
        expires_at=int(datetime.now(timezone.utc).timestamp()) + 600,
    )


def test_voice_session_success(isolated_mobile_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_mint(session_payload, *, expires_after_seconds):  # type: ignore[no-untyped-def]
        assert session_payload["model"] == "gpt-4o-realtime-preview-2024-12-17"
        assert expires_after_seconds == 600
        return _fake_secret()

    monkeypatch.setattr("app.api.mobile._mint_realtime_client_secret", fake_mint)

    client = TestClient(app)
    response = client.post("/v1/mobile/voice/session", json={"deviceId": "device-123"})

    assert response.status_code == 200
    body = response.json()
    assert body["clientSecret"] == "test-secret"
    assert body["agent"] == "unknown-caller"
    assert body["model"] == "gpt-4o-realtime-preview-2024-12-17"
    assert body["transcriptsChannel"] == "ringdown-transcripts"
    assert body["controlChannel"] == "ringdown-control"
    assert body["turnDetection"]["type"] == "server_vad"


def test_voice_session_requires_enabled_device(
    isolated_mobile_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = yaml.safe_load(isolated_mobile_config.read_text(encoding="utf-8")) or {}
    data["mobile_devices"]["device-123"]["enabled"] = False
    isolated_mobile_config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    settings.refresh_config_cache()

    async def fail_mint(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("Should not mint secret for disabled device")

    monkeypatch.setattr("app.api.mobile._mint_realtime_client_secret", fail_mint)

    client = TestClient(app)
    response = client.post("/v1/mobile/voice/session", json={"deviceId": "device-123"})

    assert response.status_code == 403


def test_voice_session_agent_mismatch(isolated_mobile_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_mint(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("Should not mint secret when agent mismatched")

    monkeypatch.setattr("app.api.mobile._mint_realtime_client_secret", fail_mint)

    client = TestClient(app)
    response = client.post(
        "/v1/mobile/voice/session",
        json={"deviceId": "device-123", "agent": "ringdown-demo"},
    )

    assert response.status_code == 400


def test_voice_session_unknown_device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    source_config = project_root / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(source_config.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("RINGDOWN_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings.refresh_config_cache()

    async def fail_mint(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("Should not mint secret for unknown device")

    monkeypatch.setattr("app.api.mobile._mint_realtime_client_secret", fail_mint)

    client = TestClient(app)
    response = client.post("/v1/mobile/voice/session", json={"deviceId": "missing-device"})

    assert response.status_code == 404
    settings.refresh_config_cache()


@pytest.mark.asyncio
async def test_voice_session_streams_transcripts(
    isolated_mobile_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class StubTrack:
        def __init__(self) -> None:
            self.closed = False
            self.frames = []

        def enqueue(self, frame) -> None:  # noqa: ANN001 - aiortc frame type
            self.frames.append(frame)

        def close(self) -> None:
            self.closed = True

    class StubChannel:
        readyState = "open"

        def __init__(self) -> None:
            self.payloads: list[str] = []

        def send(self, data: str) -> None:
            self.payloads.append(data)

    monkeypatch.setattr(mobile, "_get_openai_client", lambda: SimpleNamespace())

    device_cfg = settings.get_mobile_device("device-123")
    assert device_cfg is not None

    track = StubTrack()
    session = mobile.MobileVoiceSession("device-123", device_cfg, track)

    channel = StubChannel()
    session.attach_transcripts_channel(channel)

    logged: list[tuple[str, str, str | None]] = []

    def fake_log_turn(who: str, text: str, *, source: str | None = None) -> None:
        logged.append((who, text, source))

    async def immediate_to_thread(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        return func(*args, **kwargs)

    async def fake_transcribe(pcm_bytes: bytes) -> str:
        return "hello from android"

    async def fake_generate_response(_text: str) -> str:
        return "assistant reply"

    spoken: list[str] = []

    async def fake_speak(text: str) -> None:
        spoken.append(text)

    monkeypatch.setattr(mobile, "log_turn", fake_log_turn)
    monkeypatch.setattr(mobile.asyncio, "to_thread", immediate_to_thread, raising=False)
    monkeypatch.setattr(session, "_transcribe", fake_transcribe)
    monkeypatch.setattr(session, "_generate_response", fake_generate_response)
    monkeypatch.setattr(session, "_speak", fake_speak)

    pcm = b"\x01\x00" * (session._min_samples)  # type: ignore[attr-defined]
    await session._process_chunk(pcm)  # type: ignore[attr-defined]

    assert logged == [
        ("user", "hello from android", "android-realtime"),
        ("assistant", "assistant reply", "android-realtime"),
    ]
    assert channel.payloads, "expected transcript payload to be sent"
    payload = json.loads(channel.payloads[-1])
    assert payload["type"] == "transcript"
    assert payload["speaker"] == "user"
    assert payload["source"] == "android-realtime"
    assert payload["text"] == "hello from android"
    assert payload["final"] is True
    assert spoken == ["assistant reply"]
