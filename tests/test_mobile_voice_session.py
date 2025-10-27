from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from typing import Iterator

import pytest
import yaml
from fastapi.testclient import TestClient

from app import settings
from app.main import app
from app.managed_av.client import ManagedAVSession
from app.managed_av.session_store import get_session_store
import app.api.mobile as mobile


@pytest.fixture(autouse=True)
def clear_session_store() -> Iterator[None]:
    store = get_session_store()
    asyncio.run(store.clear())
    yield
    asyncio.run(store.clear())


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
    monkeypatch.setenv("MANAGED_AV_API_KEY", "test-managed-key")
    settings.refresh_config_cache()
    try:
        yield config_path
    finally:
        settings.refresh_config_cache()


class StubManagedClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def start_session(
        self,
        *,
        device_id: str,
        agent_name: str,
        greeting: str | None,
        device_metadata: dict | None,
    ) -> ManagedAVSession:
        self.calls.append((device_id, agent_name))
        return ManagedAVSession(
            session_id="session-abc",
            agent=agent_name,
            room_url="https://example.daily.co/ringdown",
            access_token="token-xyz",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            pipeline_session_id="pipeline-session",
            greeting=greeting,
            metadata=device_metadata or {},
        )

    async def close_session(self, session_id: str) -> None:
        self.calls.append(("close", session_id))


def _patch_client(monkeypatch: pytest.MonkeyPatch, stub: StubManagedClient) -> None:
    monkeypatch.setattr(mobile, "_managed_client", stub)


def test_voice_session_success(
    isolated_mobile_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = StubManagedClient()
    _patch_client(monkeypatch, stub)

    client = TestClient(app)
    response = client.post("/v1/mobile/voice/session", json={"deviceId": "device-123"})

    assert response.status_code == 200
    body = response.json()
    assert body["sessionId"] == "session-abc"
    assert body["accessToken"] == "token-xyz"
    assert body["roomUrl"].startswith("https://example.daily.co")
    assert body["pipelineSessionId"] == "pipeline-session"
    assert stub.calls == [("device-123", "unknown-caller")]


def test_voice_session_requires_enabled_device(
    isolated_mobile_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = yaml.safe_load(Path(isolated_mobile_config).read_text(encoding="utf-8")) or {}
    data["mobile_devices"]["device-123"]["enabled"] = False
    Path(isolated_mobile_config).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    settings.refresh_config_cache()

    client = TestClient(app)
    response = client.post("/v1/mobile/voice/session", json={"deviceId": "device-123"})

    assert response.status_code == 403


def test_voice_session_agent_mismatch(isolated_mobile_config: Path) -> None:
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
    monkeypatch.setenv("MANAGED_AV_API_KEY", "test-managed-key")
    settings.refresh_config_cache()

    client = TestClient(app)
    response = client.post("/v1/mobile/voice/session", json={"deviceId": "missing-device"})

    assert response.status_code == 404
    settings.refresh_config_cache()


def test_managed_av_completion_flow(
    isolated_mobile_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = StubManagedClient()
    _patch_client(monkeypatch, stub)

    client = TestClient(app)
    session_response = client.post("/v1/mobile/voice/session", json={"deviceId": "device-123"})
    assert session_response.status_code == 200
    session_id = session_response.json()["sessionId"]

    async def fake_stream_response(user_text, agent_cfg, messages):  # type: ignore[no-untyped-def]
        yield {"type": "tool_executing"}
        yield "assistant reply"

    logged: list[tuple[str, str, str | None]] = []

    def fake_log_turn(role: str, text: str, *, source: str | None = None) -> None:
        logged.append((role, text, source))

    monkeypatch.setattr(mobile, "stream_response", fake_stream_response)
    monkeypatch.setattr(mobile, "log_turn", fake_log_turn)

    completion = client.post(
        "/v1/mobile/managed-av/completions",
        json={"sessionId": session_id, "text": "hello", "final": True},
    )

    assert completion.status_code == 200
    payload = completion.json()
    assert payload["responseText"] == "assistant reply"
    assert payload["holdText"] == "Give me a moment while I work on that."
    assert payload["reset"] is False
    assert logged == [
        ("user", "hello", "android-managed-av"),
        ("assistant", "assistant reply", "android-managed-av"),
    ]
