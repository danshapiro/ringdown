from pathlib import Path
from typing import Iterator

import pytest
import yaml
from fastapi.testclient import TestClient

from app import settings
from app.main import app


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    project_root = Path(__file__).resolve().parents[1]
    source_config = project_root / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(source_config.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("RINGDOWN_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("MANAGED_AV_API_KEY", "test-key")
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
