from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APK_RELATIVE = Path("android/app/build/outputs/apk/debug/app-debug.apk")
DEFAULT_GRADLE_TASK = ":app:assembleDebug"
CONNECTED_TEST_TASK = ":app:connectedVoiceMvpAndroidTest"


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build, install, and optionally test the Android client.")
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

    if not args.skip_build:
        _run(["bash", "android/scripts/gradle.sh", "./gradlew", args.build_task])

    apk_path = args.apk or (REPO_ROOT / DEFAULT_APK_RELATIVE)
    if not apk_path.is_absolute():
        apk_path = REPO_ROOT / apk_path

    if not apk_path.exists():
        raise FileNotFoundError(f"APK not found at {apk_path}")

    _run(["adb", "-s", args.device, "install", "-r", str(apk_path)])

    if not args.skip_tests:
        _run(["bash", "android/scripts/gradle.sh", "./gradlew", CONNECTED_TEST_TASK])


if __name__ == "__main__":
    main()
