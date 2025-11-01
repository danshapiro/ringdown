#!/usr/bin/env python3
"""Automation wrapper that ensures a managed session is ready before running the handset harness."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from tests.live.handset_audio_loop import run_handset_audio_loop


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, help="Backend base URL, e.g. https://example.a.run.app")
    parser.add_argument("--device-id", required=True, help="Registered mobile device identifier")
    parser.add_argument("--control-token", required=True, help="Value of MANAGED_AV_CONTROL_TOKEN")
    parser.add_argument("--device-serial", default="", help="adb serial for the handset (optional)")
    parser.add_argument("--package", default="com.ringdown.mobile.debug", help="Android package name")
    parser.add_argument("--frequency", type=float, default=440.0, help="Sine wave frequency in Hz")
    parser.add_argument("--duration", type=float, default=1.5, help="Prompt duration in seconds")
    parser.add_argument("--output-dir", default="artifacts/handset", help="Directory for captured WAV output")
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds for backend calls (default: 30).",
    )
    parser.add_argument(
        "--fresh-session",
        action="store_true",
        help="Force creation of a new session instead of reusing an existing one.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    summary: Dict[str, Any] = run_handset_audio_loop(
        backend=args.backend,
        device_id=args.device_id,
        control_token=args.control_token,
        device_serial=args.device_serial,
        package=args.package,
        frequency=args.frequency,
        duration=args.duration,
        output_dir=args.output_dir,
        reuse_existing=not args.fresh_session,
        timeout=args.timeout,
    )
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
