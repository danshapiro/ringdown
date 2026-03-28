from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import android.scripts.install as install_script


class CallRecorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self,
        cmd: list[str],
        check: bool,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0)


def _patch_repo_paths(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(install_script, "REPO_ROOT", root)
    android_dir = root / "android"
    monkeypatch.setattr(install_script, "ANDROID_DIR", android_dir)
    monkeypatch.setattr(install_script, "LOCAL_PROPERTIES", android_dir / "local.properties")


def _write_local_properties(root: Path, sdk_dir: Path) -> None:
    android_dir = root / "android"
    android_dir.mkdir(parents=True, exist_ok=True)
    (android_dir / "local.properties").write_text(
        f"sdk.dir={sdk_dir}\n",
        encoding="utf-8",
    )


def test_install_runs_build_and_adb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apk_dir = tmp_path / "android" / "app" / "build" / "outputs" / "apk" / "debug"
    apk_dir.mkdir(parents=True)
    apk_path = apk_dir / "app-debug.apk"
    apk_path.write_bytes(b"fake")
    sdk_root = tmp_path / "android-sdk"
    adb_path = sdk_root / "platform-tools" / "adb"
    adb_path.parent.mkdir(parents=True)
    adb_path.write_text("", encoding="utf-8")
    _write_local_properties(tmp_path, sdk_root)

    recorder = CallRecorder()
    _patch_repo_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(install_script, "IS_WINDOWS", False)
    monkeypatch.setattr(install_script, "IS_WSL", False)
    monkeypatch.setattr(install_script, "IS_NATIVE_POSIX", True)
    monkeypatch.setattr(subprocess, "run", recorder)  # type: ignore[arg-type]
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    install_script.main(["--device", "emulator-5554", "--skip-tests"])

    gradle_wrapper = str(tmp_path / "android" / "gradlew")
    expected_adb = [str(adb_path), "-s", "emulator-5554", "install", "-r", str(apk_path)]

    gradle_call = recorder.calls[0]
    assert gradle_call[0] == gradle_wrapper
    assert ":app:assembleDebug" in gradle_call
    assert expected_adb in recorder.calls


def test_install_requires_device_argument() -> None:
    with pytest.raises(SystemExit):
        install_script.main([])


def test_install_wsl_invokes_cmd_wrapper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apk_dir = tmp_path / "android" / "app" / "build" / "outputs" / "apk" / "debug"
    apk_dir.mkdir(parents=True)
    apk_path = apk_dir / "app-debug.apk"
    apk_path.write_bytes(b"fake")
    sdk_root = tmp_path / "android-sdk"
    adb_path = sdk_root / "platform-tools" / "adb.exe"
    adb_path.parent.mkdir(parents=True)
    adb_path.write_text("", encoding="utf-8")
    _write_local_properties(tmp_path, sdk_root)

    recorder = CallRecorder()
    _patch_repo_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(install_script, "IS_WINDOWS", False)
    monkeypatch.setattr(install_script, "IS_WSL", True)
    monkeypatch.setattr(install_script, "IS_NATIVE_POSIX", False)

    def fake_wsl_path(path: Path) -> str:  # pragma: no cover - deterministic mapping for tests
        return f"C:\\fake\\{path.name}"

    monkeypatch.setattr(install_script, "_wsl_to_windows_path", fake_wsl_path)
    monkeypatch.setattr(subprocess, "run", recorder)  # type: ignore[arg-type]
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    install_script.main(["--device", "emulator-5554", "--skip-tests"])

    cmd_calls = [cmd for cmd in recorder.calls if cmd and cmd[0].lower().startswith("cmd")]
    assert cmd_calls, "expected cmd.exe invocation on WSL"
    gradle_call = cmd_calls[0]
    assert gradle_call[0:2] == ["cmd.exe", "/c"]
    assert 'cd /d "C:\\fake\\android" && gradlew.bat ' in gradle_call[2]
    assert ":app:assembleDebug" in gradle_call[2]
    expected_adb = [str(adb_path), "-s", "emulator-5554", "install", "-r", str(apk_path)]
    assert expected_adb in recorder.calls


def test_install_raises_clear_error_when_adb_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    apk_dir = tmp_path / "android" / "app" / "build" / "outputs" / "apk" / "debug"
    apk_dir.mkdir(parents=True)
    apk_path = apk_dir / "app-debug.apk"
    apk_path.write_bytes(b"fake")

    recorder = CallRecorder()
    _patch_repo_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(install_script, "IS_WINDOWS", False)
    monkeypatch.setattr(install_script, "IS_WSL", False)
    monkeypatch.setattr(install_script, "IS_NATIVE_POSIX", True)
    monkeypatch.setattr(subprocess, "run", recorder)  # type: ignore[arg-type]
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    with pytest.raises(FileNotFoundError, match="adb not found"):
        install_script.main(["--device", "emulator-5554", "--skip-build", "--skip-tests"])
