import sys
from argparse import ArgumentTypeError
from pathlib import Path

import importlib.util
import types


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

    cmd = mod._launch_harness(Args())
    assert cmd[0] == sys.executable
    assert "--device" in cmd
    assert any("logs.txt" in item for item in cmd)
    assert "--fail-event" in cmd
    assert "--success-event" in cmd
    assert cmd[-2:] == ["--foo", "bar"]
