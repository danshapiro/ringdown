#!/usr/bin/env python3
"""Automated wrapper for the manual local voice harness.

The script launches manual_voice_harness.py with failure/success detection,
performs optional adb interactions (tap reconnect/hangup buttons), and exits
with the harness status so it can be used in automation.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Sequence

HARNESS_PATH = Path(__file__).resolve().with_name("manual_voice_harness.py")
DEFAULT_PROFILE_DIR = Path(__file__).resolve().parent / "voice_smoke_profiles"
DEFAULT_ACTIVITY = "com.ringdown.mobile.debug/com.ringdown.mobile.MainActivity"
DEFAULT_DURATION = 180
DEFAULT_RECONNECT_DELAY = 5.0
DEFAULT_HANGUP_DELAY = 3.0
_ADB_BIN: str | None = None


def _run_adb(device: Optional[str], *args: str) -> None:
    if _ADB_BIN is None:
        raise SystemExit("ADB binary not initialised; call _set_adb_binary first.")
    cmd = [_ADB_BIN]
    if device:
        cmd.extend(["-s", device])
    cmd.extend(args)
    subprocess.run(cmd, check=True)


def _set_adb_binary(path: str) -> None:
    global _ADB_BIN
    _ADB_BIN = path


def _resolve_adb_binary(candidate: Optional[str]) -> str:
    resolved = shutil.which(candidate or "adb")
    if resolved is None:
        raise SystemExit(
            f"Unable to locate adb binary for '{candidate or 'adb'}'. "
            "Install Android platform-tools or supply --adb-bin.",
        )
    return resolved


def _verify_device_online(adb_bin: str, device: Optional[str]) -> None:
    if not device:
        return
    result = subprocess.run(
        [adb_bin, "-s", device, "get-state"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"Failed to query device '{device}': {result.stderr.strip() or result.stdout.strip()}",
        )
    if result.stdout.strip() != "device":
        raise SystemExit(f"Device '{device}' is not online (state={result.stdout.strip()}).")


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
        "--profile",
        type=Path,
        help="Optional profile JSON providing default coordinates/delays.",
    )
    parser.add_argument(
        "--activity",
        default=None,
        help=f"Activity component launched before harness (default: {DEFAULT_ACTIVITY})",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
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
        default=None,
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
        default=None,
        help=f"Seconds to wait before hangup tap once the harness exits (default: {DEFAULT_HANGUP_DELAY})",
    )
    parser.add_argument(
        "--success-event",
        action="append",
        default=None,
        help="Additional success log substrings (repeatable)",
    )
    parser.add_argument(
        "--fail-event",
        action="append",
        default=None,
        help="Additional failure log substrings (repeatable)",
    )
    parser.add_argument(
        "--extra-harness-arg",
        action="append",
        default=None,
        help="Pass-through argument for manual_voice_harness (repeatable)",
    )
    parser.add_argument(
        "--adb-bin",
        default=None,
        help="Path to adb executable (default: search PATH for 'adb')",
    )
    parser.add_argument(
        "--skip-device-check",
        action="store_true",
        help="Skip verifying that the specified --device is online",
    )
    parser.add_argument(
        "--log-output",
        type=Path,
        help="Optional path to capture harness stdout (defaults to result_json.log when --result-json is set)",
    )
    parser.add_argument(
        "--result-json",
        type=Path,
        help="Optional path to write a JSON summary of the run status",
    )
    return parser.parse_args()


def _resolve_profile_path(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.exists():
        return candidate
    suffix = candidate.suffix or ".json"
    lookup = DEFAULT_PROFILE_DIR / (candidate.name if candidate.suffix else f"{candidate.name}{suffix}")
    if lookup.exists():
        return lookup
    raise SystemExit(f"Profile file '{path}' not found (also checked {lookup}).")


def _to_coord(value) -> tuple[int, int]:
    if isinstance(value, str):
        return _parse_coord(value)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"Invalid coordinate values: {value}") from exc
    raise SystemExit(f"Coordinate value must be 'x,y' or [x, y], got {value!r}")


def _apply_profile(args: argparse.Namespace) -> None:
    if not args.profile:
        return
    profile_path = _resolve_profile_path(args.profile)
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise SystemExit(f"Profile '{profile_path}' is not valid JSON: {exc}") from exc

    def assign(attr: str, key: str, transform=None) -> None:
        if getattr(args, attr, None) is not None:
            return
        if key not in data:
            return
        value = data[key]
        if transform is not None and value is not None:
            value = transform(value)
        setattr(args, attr, value)

    assign("device", "device")
    assign("activity", "activity")
    assign("duration", "duration")
    assign("log_output", "logOutput", lambda v: Path(v).expanduser())
    assign("reconnect_tap", "reconnectTap", _to_coord)
    assign("reconnect_delay", "reconnectDelay", float)
    assign("hangup_tap", "hangupTap", _to_coord)
    assign("hangup_delay", "hangupDelay", float)
    assign("adb_bin", "adbBin", str)

    if args.fail_event is None and isinstance(data.get("failEvents"), list):
        args.fail_event = [str(item) for item in data["failEvents"] if item]
    if args.success_event is None and isinstance(data.get("successEvents"), list):
        args.success_event = [str(item) for item in data["successEvents"] if item]
    if args.extra_harness_arg is None and isinstance(data.get("extraHarnessArgs"), list):
        args.extra_harness_arg = [str(item) for item in data["extraHarnessArgs"] if item]


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
    adb_bin = _resolve_adb_binary(args.adb_bin)
    _set_adb_binary(adb_bin)
    if not args.skip_device_check:
        _verify_device_online(adb_bin, args.device)

    cmd = _launch_harness(args)
    print("Launching harness:", " ".join(cmd))
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log_path = _resolve_log_output_path(args)
    log_file = log_path.open("w", encoding="utf-8") if log_path else None

    def _stream_output() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            sys.stdout.write(line)
            if log_file:
                log_file.write(line)
                log_file.flush()

    output_thread = threading.Thread(target=_stream_output, daemon=True)
    output_thread.start()

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
        output_thread.join(timeout=1.0)
        if log_file:
            log_file.close()
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


def _resolve_log_output_path(args: argparse.Namespace) -> Optional[Path]:
    if args.log_output:
        return args.log_output.expanduser()
    if args.result_json:
        target = args.result_json.with_suffix(args.result_json.suffix + ".log") if args.result_json.suffix else Path(str(args.result_json) + ".log")
        return target.expanduser()
    return None


def main() -> int:
    args = parse_args()
    _apply_profile(args)
    if args.activity is None:
        args.activity = DEFAULT_ACTIVITY
    if args.duration is None:
        args.duration = DEFAULT_DURATION
    if args.reconnect_delay is None:
        args.reconnect_delay = DEFAULT_RECONNECT_DELAY
    if args.hangup_delay is None:
        args.hangup_delay = DEFAULT_HANGUP_DELAY
    if args.fail_event is None:
        args.fail_event = []
    if args.success_event is None:
        args.success_event = []
    if args.extra_harness_arg is None:
        args.extra_harness_arg = []

    log_path = _resolve_log_output_path(args)
    args.log_output = log_path

    return_code = _run_with_taps(args)
    status = "success" if return_code == 0 else "failure"
    if args.result_json:
        payload_path = args.result_json.expanduser()
        data_path = payload_path
        info = {
            "status": status,
            "returnCode": return_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if log_path:
            info["logPath"] = str(log_path)
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        payload_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
    if return_code != 0:
        print(f"Harness exited with status {return_code}", file=sys.stderr)
    else:
        print("Harness run completed successfully.")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
