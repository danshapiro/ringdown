from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

import pytest

import android.scripts.install as install_script


class CallRecorder:
    def __init__(self) -> None:
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str], check: bool, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0)


def test_install_runs_build_and_adb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apk_dir = tmp_path / "android" / "app" / "build" / "outputs" / "apk" / "debug"
    apk_dir.mkdir(parents=True)
    apk_path = apk_dir / "app-debug.apk"
    apk_path.write_bytes(b"fake")

    recorder = CallRecorder()
    monkeypatch.setattr(install_script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(subprocess, "run", recorder)  # type: ignore[arg-type]

    install_script.main(["--device", "emulator-5554", "--skip-tests"])

    expected_gradle = ["bash", "android/scripts/gradle.sh", "./gradlew", ":app:assembleDebug"]
    expected_adb = ["adb", "-s", "emulator-5554", "install", "-r", str(apk_path)]

    assert expected_gradle in recorder.calls
    assert expected_adb in recorder.calls


def test_install_requires_device_argument() -> None:
    with pytest.raises(SystemExit):
        install_script.main([])
