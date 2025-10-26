"""Tests for the handset approval helper script."""

from __future__ import annotations

from datetime import datetime, timezone
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
    assert first.created_at == datetime(2025, 10, 24, 20, 15, tzinfo=timezone.utc)


def test_approve_device_sets_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _write_config(tmp_path)

    freeze = datetime(2025, 10, 25, 12, 0, tzinfo=timezone.utc)

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
