from __future__ import annotations

import argparse
import os
import platform
import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ANDROID_DIR = REPO_ROOT / "android"
LOCAL_PROPERTIES = ANDROID_DIR / "local.properties"
DEFAULT_APK_RELATIVE = Path("android/app/build/outputs/apk/debug/app-debug.apk")
DEFAULT_GRADLE_TASK = ":app:assembleDebug"
CONNECTED_TEST_TASK = ":app:connectedVoiceMvpAndroidTest"
SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_WSL = SYSTEM == "Linux" and "microsoft" in platform.release().lower()
IS_NATIVE_POSIX = not (IS_WINDOWS or IS_WSL)


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, check=True, cwd=cwd or REPO_ROOT, env=env)


def _wsl_to_windows_path(path: Path) -> str:
    result = subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _load_local_properties() -> dict[str, str]:
    properties: dict[str, str] = {}
    if not LOCAL_PROPERTIES.exists():
        return properties

    for raw_line in LOCAL_PROPERTIES.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        properties[key.strip()] = value.strip()
    return properties


def _to_platform_path(path: str) -> str:
    path = path.strip()
    if not path:
        return path

    if IS_NATIVE_POSIX and len(path) >= 2 and path[1] == ":":
        drive = path[0].lower()
        remainder = path[2:].lstrip("\\/")
        remainder = remainder.replace("\\", "/")
        return f"/mnt/{drive}/{remainder}"

    if IS_WINDOWS or IS_WSL:
        return path.replace("/", "\\")

    return path.replace("\\", "/")


def _gradle_env_and_flags() -> tuple[dict[str, str], list[str]]:
    env = os.environ.copy()
    extra_flags: list[str] = []
    props = _load_local_properties()

    sdk_dir = props.get("sdk.dir")
    if sdk_dir:
        sdk_path = _to_platform_path(sdk_dir)
        if IS_NATIVE_POSIX:
            extra_flags.append(f"-Dsdk.dir={sdk_path}")
        env.setdefault("ANDROID_HOME", sdk_path)
        env.setdefault("ANDROID_SDK_ROOT", sdk_path)

    jdk_dir = props.get("jdk.dir")
    if jdk_dir:
        jdk_path = _to_platform_path(jdk_dir)
        env.setdefault("JAVA_HOME", jdk_path)
        if IS_NATIVE_POSIX:
            extra_flags.append(f"-Dorg.gradle.java.home={jdk_path}")

    return env, extra_flags


def _run_gradle(task_spec: str, env: dict[str, str], extra_flags: list[str]) -> None:
    gradle_args = extra_flags + shlex.split(task_spec)
    if IS_WINDOWS:
        wrapper = str(ANDROID_DIR / "gradlew.bat")
        cmd = ["cmd", "/c", wrapper, *gradle_args]
        _run(cmd, cwd=ANDROID_DIR, env=env)
    elif IS_WSL:
        wrapper = _wsl_to_windows_path(ANDROID_DIR / "gradlew.bat")
        android_windows = _wsl_to_windows_path(ANDROID_DIR)
        joined_args = " ".join(gradle_args)
        command = f'cd /d "{android_windows}" && gradlew.bat {joined_args}'
        cmd = ["cmd.exe", "/c", command]
        subprocess.run(cmd, check=True, env=env)
    else:
        wrapper = str(ANDROID_DIR / "gradlew")
        cmd = [wrapper, *gradle_args]
        _run(cmd, cwd=ANDROID_DIR, env=env)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build, install, and optionally test the Android client."
    )
    parser.add_argument("--device", required=True, help="ADB device serial to target.")
    parser.add_argument(
        "--build-task",
        default=DEFAULT_GRADLE_TASK,
        help="Gradle task to build before installing (default: %(default)s).",
    )
    parser.add_argument(
        "--apk",
        type=Path,
        default=None,
        help="Path to the APK to install; defaults to the debug APK after building.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip running the Gradle build step.",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip running connected tests after installation.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    gradle_env, gradle_flags = _gradle_env_and_flags()

    if not args.skip_build:
        _run_gradle(args.build_task, gradle_env, gradle_flags)

    apk_path = args.apk or (REPO_ROOT / DEFAULT_APK_RELATIVE)
    if not apk_path.is_absolute():
        apk_path = REPO_ROOT / apk_path

    if not apk_path.exists():
        raise FileNotFoundError(f"APK not found at {apk_path}")

    _run(["adb", "-s", args.device, "install", "-r", str(apk_path)])

    if not args.skip_tests:
        _run_gradle(CONNECTED_TEST_TASK, gradle_env, gradle_flags)


if __name__ == "__main__":
    main()
