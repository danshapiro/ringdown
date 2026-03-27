def load_module():
    import importlib.util
    from pathlib import Path

    module_path = Path("android/scripts/manual_voice_harness.py").resolve()
    spec = importlib.util.spec_from_file_location("manual_voice_harness", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_args_defaults():
    mod = load_module()
    args = mod.parse_args([])
    assert args.activity == mod.DEFAULT_ACTIVITY
    assert args.fail_event == []


def test_parse_args_custom_activity():
    mod = load_module()
    args = mod.parse_args(["--activity", "pkg/.Custom"])
    assert args.activity == "pkg/.Custom"
