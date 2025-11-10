import json
import sys
from argparse import ArgumentTypeError
from pathlib import Path

import importlib.util
import types

import pytest


def load_module() -> types.ModuleType:
    module_path = Path("android/scripts/run_local_voice_smoke.py").resolve()
    spec = importlib.util.spec_from_file_location("run_local_voice_smoke", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_coord_success():
    mod = load_module()
    coord = mod._parse_coord("100,200")
    assert coord == (100, 200)


def test_parse_coord_invalid_raises():
    mod = load_module()
    try:
        mod._parse_coord("foo")
    except Exception as exc:  # noqa: BLE001
        assert isinstance(exc, ArgumentTypeError)
    else:
        raise AssertionError("Expected parse error")


def test_launch_harness_includes_expected_flags(tmp_path, monkeypatch):
    mod = load_module()
    harness_path = tmp_path / "manual_voice_harness.py"
    harness_path.write_text("print('stub')", encoding="utf-8")
    monkeypatch.setattr(mod, "HARNESS_PATH", harness_path)

    class Args:
        device = "DEVICE123"
        activity = "com.ringdown.mobile/.MainActivity"
        duration = 120
        log_output = tmp_path / "logs.txt"
        fail_event = ["fail"]
        success_event = ["ok"]
        extra_harness_arg = ["--foo", "bar"]
        reconnect_delay = 1.0
        hangup_delay = 1.0

    cmd = mod._launch_harness(Args())
    assert cmd[0] == sys.executable
    assert "--device" in cmd
    assert any("logs.txt" in item for item in cmd)
    assert "--fail-event" in cmd
    assert "--success-event" in cmd
    assert cmd[-2:] == ["--foo", "bar"]


def test_apply_profile_sets_missing_fields(tmp_path, monkeypatch):
    mod = load_module()
    profile = tmp_path / "pixel.json"
    profile.write_text(
        json.dumps(
            {
                "device": "ABC",
                "activity": "pkg/.Activity",
                "duration": 90,
                "logOutput": str(tmp_path / "out.log"),
                "reconnectTap": [10, 20],
                "reconnectDelay": 2,
                "hangupTap": "30,40",
                "hangupDelay": 1,
                "failEvents": ["x"],
                "successEvents": ["y"],
                "extraHarnessArgs": ["--foo", "bar"],
            },
        ),
        encoding="utf-8",
    )

    args = types.SimpleNamespace(
        profile=profile,
        device=None,
        activity=None,
        duration=None,
        log_output=None,
        reconnect_tap=None,
        reconnect_delay=None,
        hangup_tap=None,
        hangup_delay=None,
        fail_event=None,
        success_event=None,
        extra_harness_arg=None,
    )

    mod._apply_profile(args)

    assert args.device == "ABC"
    assert args.activity == "pkg/.Activity"
    assert args.duration == 90
    assert args.reconnect_tap == (10, 20)
    assert args.hangup_tap == (30, 40)
    assert args.fail_event == ["x"]
    assert args.success_event == ["y"]
    assert args.extra_harness_arg == ["--foo", "bar"]


def test_resolve_profile_uses_default_dir(tmp_path, monkeypatch):
    mod = load_module()
    default_dir = tmp_path / "profiles"
    default_dir.mkdir()
    profile_file = default_dir / "pixel9.json"
    profile_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mod, "DEFAULT_PROFILE_DIR", default_dir)

    resolved = mod._resolve_profile_path(Path("pixel9"))
    assert resolved == profile_file


def test_resolve_adb_binary_raises(monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    with pytest.raises(SystemExit):
        mod._resolve_adb_binary("missing-adb")


def test_verify_device_online_failure(monkeypatch):
    mod = load_module()

    class Result:
        returncode = 0
        stdout = "offline"
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: Result())
    with pytest.raises(SystemExit):
        mod._verify_device_online("adb", "SER123")
