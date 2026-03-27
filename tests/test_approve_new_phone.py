"""Tests for the handset approval helper script."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    data = {
        "mobile_devices": {
            "alpha-device": {
                "label": "Alpha",
                "agent": "unknown-caller",
                "enabled": False,
                "created_at": "2025-10-24T20:15:00+00:00",
                "notes": "Needs approval",
            },
            "beta-device": {
                "label": "Beta",
                "agent": "ringdown-demo",
                "enabled": "false",
                "created_at": "2025-10-25T01:00:00+00:00",
            },
            "gamma-device": {
                "label": "Gamma",
                "agent": "ringdown-demo",
                "enabled": True,
                "created_at": "2025-10-20T12:00:00+00:00",
            },
        }
    }
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return config_path


def test_list_pending_devices_sorted(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    from approve_new_phone import list_pending_devices

    pending = list_pending_devices(config_path)

    assert [req.device_id for req in pending] == ["alpha-device", "beta-device"]

    first = pending[0]
    assert first.label == "Alpha"
    assert first.notes == "Needs approval"
    assert first.created_at == datetime(2025, 10, 24, 20, 15, tzinfo=UTC)


def test_approve_device_sets_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _write_config(tmp_path)

    freeze = datetime(2025, 10, 25, 12, 0, tzinfo=UTC)

    from approve_new_phone import approve_device

    monkeypatch.setattr("approve_new_phone._now", lambda: freeze)

    approve_device(config_path, "alpha-device", agent="ringdown-demo")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    entry = data["mobile_devices"]["alpha-device"]
    assert entry["enabled"] is True
    assert entry["agent"] == "ringdown-demo"
    assert entry["approved_at"] == freeze.isoformat()


def test_approve_missing_device_raises(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    from approve_new_phone import approve_device

    with pytest.raises(KeyError):
        approve_device(config_path, "missing-device")


def test_sync_env_device_updates_env(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text("LIVE_TEST_MOBILE_DEVICE_ID=\n", encoding="utf-8")

    from approve_new_phone import sync_env_device

    device_id = sync_env_device(env_path, config_path=config_path)
    assert device_id == "gamma-device"
    contents = env_path.read_text(encoding="utf-8").strip()
    assert contents == "LIVE_TEST_MOBILE_DEVICE_ID=gamma-device"


def test_sync_env_device_honours_prefer_label(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text("LIVE_TEST_MOBILE_DEVICE_ID=previous\n", encoding="utf-8")

    from approve_new_phone import sync_env_device

    device_id = sync_env_device(
        env_path,
        config_path=config_path,
        allow_disabled=True,
        prefer_label="beta",
    )
    assert device_id == "beta-device"
    assert env_path.read_text(encoding="utf-8").strip() == "LIVE_TEST_MOBILE_DEVICE_ID=beta-device"
