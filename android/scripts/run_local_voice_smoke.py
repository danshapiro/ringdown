#!/usr/bin/env python3
"""Automated wrapper for the manual local voice harness.

The script launches manual_voice_harness.py with failure/success detection,
performs optional adb interactions (tap reconnect/hangup buttons), and exits
with the harness status so it can be used in automation.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Sequence

HARNESS_PATH = Path(__file__).resolve().with_name("manual_voice_harness.py")
DEFAULT_DURATION = 180
DEFAULT_RECONNECT_DELAY = 5.0
DEFAULT_HANGUP_DELAY = 3.0


def _run_adb(device: Optional[str], *args: str) -> None:
    cmd = ["adb"]
    if device:
        cmd.extend(["-s", device])
    cmd.extend(args)
    subprocess.run(cmd, check=True)


def _parse_coord(value: str) -> tuple[int, int]:
    parts = value.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("Coordinate must be formatted as 'x,y'")
    try:
        x = int(parts[0].strip())
        y = int(parts[1].strip())
    except ValueError as exc:  # pragma: no cover - defensive
        raise argparse.ArgumentTypeError("Coordinate values must be integers") from exc
    return x, y


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automated local voice smoke harness")
    parser.add_argument("--device", help="ADB device serial (optional)")
    parser.add_argument(
        "--activity",
        default="com.ringdown.mobile.debug/com.ringdown.mobile.MainActivity",
        help="Activity component launched before harness (default: %(default)s)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"Harness runtime in seconds (default: {DEFAULT_DURATION})",
    )
    parser.add_argument(
        "--log-output",
        type=Path,
        help="Optional path to capture harness log output",
    )
    parser.add_argument(
        "--reconnect-tap",
        type=_parse_coord,
        help="Screen coordinate to tap the Reconnect button (format: x,y)",
    )
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=DEFAULT_RECONNECT_DELAY,
        help=f"Seconds to wait before tapping reconnect (default: {DEFAULT_RECONNECT_DELAY})",
    )
    parser.add_argument(
        "--hangup-tap",
        type=_parse_coord,
        help="Screen coordinate to tap Hang Up after the run (format: x,y)",
    )
    parser.add_argument(
        "--hangup-delay",
        type=float,
        default=DEFAULT_HANGUP_DELAY,
        help=f"Seconds to wait before hangup tap once the harness exits (default: {DEFAULT_HANGUP_DELAY})",
    )
    parser.add_argument(
        "--success-event",
        action="append",
        default=[],
        help="Additional success log substrings (repeatable)",
    )
    parser.add_argument(
        "--fail-event",
        action="append",
        default=[],
        help="Additional failure log substrings (repeatable)",
    )
    parser.add_argument(
        "--extra-harness-arg",
        action="append",
        default=[],
        help="Pass-through argument for manual_voice_harness (repeatable)",
    )
    return parser.parse_args()


def _tap(device: Optional[str], coord: tuple[int, int]) -> None:
    x, y = coord
    _run_adb(device, "shell", "input", "tap", str(x), str(y))


def _launch_harness(args: argparse.Namespace) -> list[str]:
    if not HARNESS_PATH.exists():
        raise SystemExit(f"Harness not found at {HARNESS_PATH}")
    cmd = [sys.executable, str(HARNESS_PATH)]
    if args.device:
        cmd.extend(["--device", args.device])
    if args.activity:
        cmd.extend(["--activity", args.activity])
    if args.duration:
        cmd.extend(["--duration", str(args.duration)])
    if args.log_output:
        cmd.extend(["--log-output", str(args.log_output)])
    for value in args.fail_event or []:
        cmd.extend(["--fail-event", value])
    for value in args.success_event or []:
        cmd.extend(["--success-event", value])
    cmd.extend(args.extra_harness_arg or [])
    return cmd


def _run_with_taps(args: argparse.Namespace) -> int:
    cmd = _launch_harness(args)
    print("Launching harness:", " ".join(cmd))
    process = subprocess.Popen(cmd)

    tap_threads: list[threading.Thread] = []

    def _reconnect_worker() -> None:
        if args.reconnect_tap:
            time.sleep(max(0.0, args.reconnect_delay))
            print("Tapping reconnect at", args.reconnect_tap)
            try:
                _tap(args.device, args.reconnect_tap)
            except subprocess.CalledProcessError as exc:  # pragma: no cover - hardware dependent
                print(f"Failed to tap reconnect: {exc}", file=sys.stderr)

    if args.reconnect_tap:
        thread = threading.Thread(target=_reconnect_worker, daemon=True)
        thread.start()
        tap_threads.append(thread)

    try:
        return_code = process.wait()
    except KeyboardInterrupt:
        print("Keyboard interrupt received; terminating harness...")
        process.terminate()
        return_code = process.wait()
    finally:
        for thread in tap_threads:
            thread.join(timeout=1.0)

    if args.hangup_tap:
        delay = max(0.0, args.hangup_delay)
        if delay:
            time.sleep(delay)
        print("Tapping hangup at", args.hangup_tap)
        try:
            _tap(args.device, args.hangup_tap)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - hardware dependent
            print(f"Failed to tap hangup: {exc}", file=sys.stderr)

    return return_code


def main() -> int:
    if shutil.which("adb") is None:
        print("adb not found on PATH.", file=sys.stderr)
        return 2

    args = parse_args()
    return_code = _run_with_taps(args)
    if return_code != 0:
        print(f"Harness exited with status {return_code}", file=sys.stderr)
    else:
        print("Harness run completed successfully.")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
