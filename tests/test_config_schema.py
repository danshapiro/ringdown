from pathlib import Path

import pytest
import yaml

from app.config_schema import ConfigModel, resolve_config_path


def _write_example(root: Path) -> None:
    (root / "config.example.yaml").write_text("defaults: {}\n", encoding="utf-8")


def test_resolve_config_path_prefers_local_config(tmp_path, monkeypatch):
    monkeypatch.delenv("RINGDOWN_ALLOW_CONFIG_EXAMPLE", raising=False)
    root = tmp_path
    (root / "config.yaml").write_text("defaults: {}\n", encoding="utf-8")
    _write_example(root)

    path = resolve_config_path(None, project_root=root)

    assert path == root / "config.yaml"


def test_resolve_config_path_requires_opt_in_for_example(tmp_path, monkeypatch):
    monkeypatch.delenv("RINGDOWN_ALLOW_CONFIG_EXAMPLE", raising=False)
    root = tmp_path
    _write_example(root)

    with pytest.raises(FileNotFoundError):
        resolve_config_path(None, project_root=root)


def test_resolve_config_path_respects_env_opt_in(tmp_path, monkeypatch):
    root = tmp_path
    _write_example(root)

    monkeypatch.setenv("RINGDOWN_ALLOW_CONFIG_EXAMPLE", "1")

    path = resolve_config_path(None, project_root=root)

    assert path == root / "config.example.yaml"


def test_config_model_accepts_repository_config():
    repo_config = resolve_config_path(
        None,
        allow_example_fallback=True,
        project_root=Path("."),
    )
    data = yaml.safe_load(repo_config.read_text(encoding="utf-8"))

    cfg = ConfigModel.model_validate(data)

    assert "unknown-caller" in cfg.agents


def test_config_model_does_not_own_mobile_device_fields():
    assert "mobile_devices" not in ConfigModel.model_fields
    assert "mobileDevices" not in ConfigModel.model_fields


def test_config_model_accepts_mobile_device_keys_as_extras():
    payload = {
        "defaults": {
            "timezone": "America/Los_Angeles",
            "model": "gpt-4o-mini",
            "max_tokens": 1024,
            "language": "en",
            "bot_name": "Ringdown",
            "default_email": "user@example.com",
            "project_name": "ringdown",
            "calendar_user_name": "Dan",
            "welcome_greeting": "Hello",
            "transcription_provider": "openai",
            "speech_model": "gpt-4o-mini-transcribe",
        },
        "agents": {
            "unknown-caller": {
                "bot_name": "Unknown Caller",
            }
        },
        "mobile_devices": {"device-1": {"agent": "unknown-caller", "label": "Primary"}},
        "mobileDevices": {"device-2": {"agent": "unknown-caller", "label": "Secondary"}},
    }

    cfg = ConfigModel.model_validate(payload)
    extras = cfg.model_extra or {}

    assert extras["mobile_devices"]["device-1"]["agent"] == "unknown-caller"
    assert extras["mobileDevices"]["device-2"]["agent"] == "unknown-caller"
