from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_run_voice_smoke_hides_auth_token_from_gradle_output(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "android" / "scripts"
    scripts_dir.mkdir(parents=True)
    source = Path("android/scripts/run-voice-smoke.sh").read_text(encoding="utf-8")
    script_path = scripts_dir / "run-voice-smoke.sh"
    _write_executable(script_path, source)

    gradle_args_path = tmp_path / "gradle-args.txt"
    adb_calls_path = tmp_path / "adb-calls.txt"

    _write_executable(
        scripts_dir / "gradle.sh",
        f"""#!/usr/bin/env bash
printf '%s\n' "$@" > "{gradle_args_path}"
""",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "adb",
        f"""#!/usr/bin/env bash
printf '%s\n' "$@" >> "{adb_calls_path}"
if [[ "$*" == *"shell cat > /data/local/tmp/ringdown-live-auth-token.txt"* ]]; then
  cat > "{tmp_path / 'pushed-token.txt'}"
fi
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["ANDROID_SERIAL"] = "SERIAL123"

    result = subprocess.run(
        [
            "/bin/bash",
            str(script_path),
            "--backend",
            "http://127.0.0.1:8000",
            "--auth-token",
            "secret-token",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "secret-token" not in result.stdout
    assert "secret-token" not in result.stderr
    assert "secret-token" not in gradle_args_path.read_text(encoding="utf-8")
    adb_calls = adb_calls_path.read_text(encoding="utf-8")
    assert "secret-token" not in adb_calls
    assert "shell" in adb_calls
    assert "cat > /data/local/tmp/ringdown-live-auth-token.txt" in adb_calls
    assert "/data/local/tmp/ringdown-live-auth-token.txt" in adb_calls
    assert "rm" in adb_calls
    assert (tmp_path / "pushed-token.txt").read_text(encoding="utf-8") == "secret-token"


def test_run_voice_smoke_uses_sdk_root_adb_when_path_lacks_adb(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "android" / "scripts"
    scripts_dir.mkdir(parents=True)
    source = Path("android/scripts/run-voice-smoke.sh").read_text(encoding="utf-8")
    script_path = scripts_dir / "run-voice-smoke.sh"
    _write_executable(script_path, source)

    gradle_args_path = tmp_path / "gradle-args.txt"
    adb_calls_path = tmp_path / "adb-calls.txt"
    sdk_root = tmp_path / "android-sdk"
    platform_tools = sdk_root / "platform-tools"
    platform_tools.mkdir(parents=True)

    _write_executable(
        scripts_dir / "gradle.sh",
        f"""#!/usr/bin/env bash
printf '%s\n' "$@" > "{gradle_args_path}"
""",
    )
    _write_executable(
        platform_tools / "adb",
        f"""#!/usr/bin/env bash
printf '%s\n' "$@" >> "{adb_calls_path}"
if [[ "$*" == *"shell cat > /data/local/tmp/ringdown-live-auth-token.txt"* ]]; then
  cat > "{tmp_path / 'pushed-token.txt'}"
fi
""",
    )

    env = os.environ.copy()
    env["PATH"] = "/usr/bin:/bin"
    env["ANDROID_SDK_ROOT"] = str(sdk_root)

    result = subprocess.run(
        [
            "/bin/bash",
            str(script_path),
            "--backend",
            "http://127.0.0.1:8000",
            "--auth-token",
            "secret-token",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    adb_calls = adb_calls_path.read_text(encoding="utf-8")
    assert "shell" in adb_calls
    assert "secret-token" not in adb_calls
