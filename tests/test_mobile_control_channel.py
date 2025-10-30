from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml
from fastapi.testclient import TestClient
from pydub.generators import Sine

from app import settings
from app.main import app
from app.managed_av.client import ManagedAVSession
from app.managed_av.session_store import get_session_store
import app.api.mobile as mobile
from tests.live.control_audio_utils import audiosegment_to_base64_wav


@pytest.fixture(autouse=True)
def clear_session_store() -> Iterator[None]:
    store = get_session_store()
    asyncio.run(store.clear())
    yield
    asyncio.run(store.clear())


@pytest.fixture
def isolated_mobile_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
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
    monkeypatch.setenv("MANAGED_AV_CONTROL_TOKEN", "test-control-token")
    monkeypatch.setenv("PIPECAT_API_KEY", "test-pipecat-key")
    settings.refresh_config_cache()
    try:
        yield "device-123"
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
        session_metadata: dict | None = None,
    ) -> ManagedAVSession:
        self.calls.append((device_id, agent_name))
        combined_metadata: dict[str, Any] = {}
        if device_metadata:
            combined_metadata.update(device_metadata)
        if session_metadata:
            combined_metadata.update(session_metadata)

        return ManagedAVSession(
            session_id="session-xyz",
            agent=agent_name,
            room_url="https://example.daily.co/ringdown",
            access_token="token-xyz",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            pipeline_session_id="pipeline-session",
            greeting=greeting,
            metadata=combined_metadata,
        )

    async def close_session(self, session_id: str) -> None:
        self.calls.append(("close", session_id))


def _patch_client(monkeypatch: pytest.MonkeyPatch, stub: StubManagedClient) -> None:
    monkeypatch.setattr(mobile, "_managed_client", stub)


def _basic_audio_payload() -> dict[str, Any]:
    tone = Sine(440).to_audio_segment(duration=200).set_frame_rate(16_000).set_sample_width(2).set_channels(1)
    return {
        "promptId": "prompt-1",
        "audioBase64": audiosegment_to_base64_wav(tone),
        "sampleRateHz": 16_000,
        "channels": 1,
        "format": "wav",
        "metadata": {"durationSeconds": 0.2},
    }


def test_control_queue_round_trip(
    isolated_mobile_config: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubManagedClient()
    _patch_client(monkeypatch, stub)

    client = TestClient(app)
    session_response = client.post("/v1/mobile/voice/session", json={"deviceId": isolated_mobile_config})
    assert session_response.status_code == 200
    session_payload = session_response.json()

    control_meta = session_payload["metadata"]["control"]
    control_key = control_meta["key"]
    assert control_meta["pollPath"] == "/v1/mobile/managed-av/control/next"

    headers = {
        "X-Ringdown-Control-Token": "test-control-token",
        "X-Ringdown-Control-Key": control_key,
    }
    enqueue = client.post(
        "/v1/mobile/managed-av/control",
        headers=headers,
        json={"sessionId": session_payload["sessionId"], "message": _basic_audio_payload()},
    )
    assert enqueue.status_code == 202
    enqueue_payload = enqueue.json()
    assert enqueue_payload["queued"] is True
    assert enqueue_payload["messageId"]

    fetch_headers = {"X-Ringdown-Control-Key": control_key}
    fetch = client.post(
        "/v1/mobile/managed-av/control/next",
        headers=fetch_headers,
        json={"sessionId": session_payload["sessionId"]},
    )
    assert fetch.status_code == 200
    fetch_payload = fetch.json()
    assert fetch_payload["message"]["messageId"] == enqueue_payload["messageId"]
    assert fetch_payload["message"]["promptId"] == "prompt-1"
    assert fetch_payload["message"]["sampleRateHz"] == 16000

    # Subsequent poll should be empty.
    empty = client.post(
        "/v1/mobile/managed-av/control/next",
        headers=fetch_headers,
        json={"sessionId": session_payload["sessionId"]},
    )
    assert empty.status_code == 200
    assert empty.json()["message"] is None


def test_enqueue_requires_valid_token(
    isolated_mobile_config: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubManagedClient()
    _patch_client(monkeypatch, stub)

    client = TestClient(app)
    session_response = client.post("/v1/mobile/voice/session", json={"deviceId": isolated_mobile_config})
    assert session_response.status_code == 200
    session_payload = session_response.json()
    control_key = session_payload["metadata"]["control"]["key"]

    response = client.post(
        "/v1/mobile/managed-av/control",
        headers={"X-Ringdown-Control-Key": control_key},
        json={"sessionId": session_payload["sessionId"], "message": _basic_audio_payload()},
    )
    assert response.status_code == 401


def test_fetch_requires_valid_control_key(
    isolated_mobile_config: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubManagedClient()
    _patch_client(monkeypatch, stub)

    client = TestClient(app)
    session_response = client.post("/v1/mobile/voice/session", json={"deviceId": isolated_mobile_config})
    assert session_response.status_code == 200
    session_payload = session_response.json()

    response = client.post(
        "/v1/mobile/managed-av/control/next",
        headers={"X-Ringdown-Control-Key": "invalid"},
        json={"sessionId": session_payload["sessionId"]},
    )
    assert response.status_code == 401
