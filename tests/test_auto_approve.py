from __future__ import annotations

from pathlib import Path

import pytest

from approve_new_phone import auto_approve_single_pending, list_pending_devices


def _write_config(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_auto_approve_single_pending(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    _write_config(
        config,
        """
mobile_devices:
  device-one:
    label: Device One
    agent: Danbot Agent
    enabled: false
    created_at: '2025-11-03T18:00:00+00:00'
""".strip()
    )

    pending = list_pending_devices(config)
    assert len(pending) == 1

    approved = auto_approve_single_pending(config)
    assert approved is not None
    assert approved.device_id == "device-one"

    updated = config.read_text(encoding="utf-8")
    assert "enabled: true" in updated
    assert "approved_at" in updated


def test_auto_approve_multiple_pending_raises(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    _write_config(
        config,
        """
mobile_devices:
  first-device:
    enabled: false
  second-device:
    enabled: false
""".strip()
    )

    with pytest.raises(RuntimeError):
        auto_approve_single_pending(config)


def test_auto_approve_no_pending_returns_none(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    _write_config(
        config,
        """
mobile_devices:
  handset:
    enabled: true
""".strip()
    )

    assert auto_approve_single_pending(config) is None
