#!/usr/bin/env python3
"""Manual harness for exercising the Android realtime voice flow.

The harness is intentionally simple (stub) until automated mic loopback is wired.
It ensures an adb-connected device is available, launches the Ringdown app,
and streams relevant logcat output so the engineer can verify audio flowing
in both directions.
"""

import argparse
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional


DEFAULT_ACTIVITY = "com.ringdown.mobile.debug/com.ringdown.mobile.MainActivity"


def _run_adb(device: Optional[str], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["adb"]
    if device:
        cmd.extend(["-s", device])
    cmd.extend(args)
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _ensure_device(device: Optional[str]) -> str:
    try:
        result = _run_adb(None, "devices")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:  # pragma: no cover - defensive
        raise RuntimeError("Failed to invoke adb. Is the Android SDK installed?") from exc

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    connected = [line.split("\t")[0] for line in lines[1:] if "\tdevice" in line]

    if not connected:
        raise RuntimeError("No adb devices connected. Plug in a handset or emulator.")

    if device:
        if device not in connected:
            raise RuntimeError(f"Requested device '{device}' is not connected (found: {connected}).")
        return device

    if len(connected) > 1:
        raise RuntimeError(
            f"Multiple devices detected ({connected}). Pass --device to select one explicitly.",
        )
    return connected[0]


def _wake_device(device: str) -> None:
    _run_adb(device, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    _run_adb(device, "shell", "input", "keyevent", "KEYCODE_MENU")


def _launch_activity(device: str, component: str) -> None:
    _run_adb(
        device,
        "shell",
        "am",
        "start",
        "-n",
        component,
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
    )


def _tail_logcat(device: str) -> subprocess.Popen[str]:
    cmd = [
        "adb",
        "-s",
        device,
        "logcat",
        "-T",
        "1",
        "VoiceSession:D",
        "RingdownApp:D",
        "*:S",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _forward_logs(process: subprocess.Popen[str]) -> threading.Thread:
    def _reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            print(line.rstrip())

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    return thread


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual realtime voice harness")
    parser.add_argument("--device", help="ADB device serial (optional)")
    parser.add_argument(
        "--activity",
        default=DEFAULT_ACTIVITY,
        help=f"Activity component to launch (default: {DEFAULT_ACTIVITY})",
    )
    return parser.parse_args()


def main() -> int:
    if shutil.which("adb") is None:
        print("adb not found on PATH. Install Android platform-tools.", file=sys.stderr)
        return 2

    args = parse_args()

    try:
        device = _ensure_device(args.device)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Using adb device: {device}")
    _wake_device(device)
    _launch_activity(device, args.activity)

    print()
    print("=== Manual Voice Session Harness (stub) ===")
    print("1. Verify the app is visible and approved.")
    print("2. On the device, tap Reconnect to start the realtime session.")
    print("3. Speak into the microphone; expect assistant audio playback.")
    print("4. Press Hang up on the device or Ctrl+C here to stop.")
    print()

    process = _tail_logcat(device)
    thread = _forward_logs(process)

    try:
        thread.join()
    except KeyboardInterrupt:
        print("\nStopping harness...")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive cleanup
            process.kill()

    return 0


if __name__ == "__main__":
    sys.exit(main())
