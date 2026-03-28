from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from app import settings
from app.main import app
from app.mobile.text_session_store import TextSessionState


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    project_root = Path(__file__).resolve().parents[1]
    source_config = project_root / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(source_config.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("RINGDOWN_CONFIG_PATH", str(config_path))
    settings.refresh_config_cache()
    try:
        yield config_path
    finally:
        settings.refresh_config_cache()


def _load_mobile_devices(config_path: Path) -> dict:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return data.get("mobile_devices", {})


def test_register_new_device_creates_pending_entry(isolated_config: Path) -> None:
    client = TestClient(app)
    payload = {"deviceId": "device-123", "label": "Pixel", "model": "Pixel 9"}

    response = client.post("/v1/mobile/devices/register", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["message"]
    assert body["pollAfterSeconds"] == 5

    devices = _load_mobile_devices(isolated_config)
    assert "device-123" in devices
    entry = devices["device-123"]
    assert entry["enabled"] is False
    assert entry["agent"] == "unknown-caller"


def test_register_enabled_device_returns_approved(isolated_config: Path) -> None:
    client = TestClient(app)
    device_id = "device-456"

    first_response = client.post("/v1/mobile/devices/register", json={"deviceId": device_id})
    assert first_response.status_code == 200

    data = yaml.safe_load(isolated_config.read_text(encoding="utf-8")) or {}
    agents = list((data.get("agents") or {}).keys())
    agent_name = next((name for name in agents if name != "unknown-caller"), "unknown-caller")
    devices = data.setdefault("mobile_devices", {})
    devices[device_id]["enabled"] = True
    devices[device_id]["agent"] = agent_name
    isolated_config.write_text(yaml.safe_dump(data), encoding="utf-8")
    settings.refresh_config_cache()

    second_response = client.post("/v1/mobile/devices/register", json={"deviceId": device_id})
    assert second_response.status_code == 200
    body = second_response.json()
    assert body["status"] == "APPROVED"
    assert body["message"]
    assert body["pollAfterSeconds"] is None
    assert body["agent"] == agent_name


def test_register_denied_device_returns_denied(isolated_config: Path) -> None:
    data = yaml.safe_load(isolated_config.read_text(encoding="utf-8")) or {}
    data.setdefault("mobile_devices", {})["blocked-device"] = {
        "label": "Test",
        "agent": "unknown-caller",
        "enabled": False,
        "blocked_reason": "Device suspended",
    }
    isolated_config.write_text(yaml.safe_dump(data), encoding="utf-8")
    settings.refresh_config_cache()

    client = TestClient(app)
    response = client.post("/v1/mobile/devices/register", json={"deviceId": "blocked-device"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DENIED"
    assert body["pollAfterSeconds"] is None
    assert "suspended" in body["message"].lower()


def test_register_device_reflects_script_approval(isolated_config: Path) -> None:
    client = TestClient(app)
    device_id = "device-approval-cache"

    data = yaml.safe_load(isolated_config.read_text(encoding="utf-8")) or {}
    agents = data.get("agents") or {}
    agent_name = next((name for name in agents if name != "unknown-caller"), "unknown-caller")

    first_response = client.post("/v1/mobile/devices/register", json={"deviceId": device_id})
    assert first_response.status_code == 200
    assert first_response.json()["status"] == "PENDING"

    from approve_new_phone import approve_device

    approve_device(isolated_config, device_id, agent=agent_name)

    second_response = client.post("/v1/mobile/devices/register", json={"deviceId": device_id})
    assert second_response.status_code == 200
    body = second_response.json()
    assert body["status"] == "APPROVED"
    assert body["agent"] == agent_name


def test_register_refreshes_stale_cache_for_followup_text_session(isolated_config: Path) -> None:
    data = yaml.safe_load(isolated_config.read_text(encoding="utf-8")) or {}
    data["mobile_devices"] = {
        "instrumentation-device": {
            "label": "Instrumentation",
            "agent": "unknown-caller",
            "enabled": True,
            "auth_token": "secret-token",
            "session_resume_ttl_seconds": 300,
        }
    }
    isolated_config.write_text(yaml.safe_dump(data), encoding="utf-8")

    settings.get_mobile_devices()

    now = datetime.now(UTC)
    state = TextSessionState(
        session_id="session-abc",
        device_id="instrumentation-device",
        agent_name="unknown-caller",
        agent_config={
            "model": "gpt-5",
            "prompt": "You are helpful.",
            "welcome_greeting": "Hi there!",
        },
        created_at=now,
        expires_at=now + timedelta(seconds=900),
        resume_expires_at=now + timedelta(seconds=300),
        resume_token="resume-abc",
        session_ttl_seconds=900,
        resume_ttl_seconds=300,
        heartbeat_interval_seconds=12,
        heartbeat_timeout_seconds=30,
        tls_pins=[],
        messages=[],
    )
    store = MagicMock()
    store.create_session = AsyncMock(return_value=(state, "session-token"))

    client = TestClient(app)
    with patch("app.api.mobile.get_text_session_store", return_value=store):
        register_response = client.post(
            "/v1/mobile/devices/register",
            json={"deviceId": "instrumentation-device"},
        )
        text_response = client.post(
            "/v1/mobile/text/session",
            json={"deviceId": "instrumentation-device", "authToken": "secret-token"},
        )

    assert register_response.status_code == 200
    assert register_response.json()["status"] == "APPROVED"
    assert text_response.status_code == 200
    store.create_session.assert_awaited_once()
