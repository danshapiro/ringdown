#!/usr/bin/env python3
"""Handset audio loop harness for ringdown-32.

This script exercises the managed control channel by pushing a deterministic WAV
prompt to the handset, then retrieving the recorded audio artifact that the app
writes when the harness is enabled.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import subprocess
import sys
import time
from array import array
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from pydub.generators import Sine
from tests.live.control_audio_utils import (
    audiosegment_to_base64_wav,
    base64_wav_to_audiosegment,
)
from tests.live.managed_session_helper import create_session, ensure_active_session

DEFAULT_TIMEOUT_SECONDS = 30.0


def log_event(severity: str, event: str, **payload: Any) -> None:
    message: Dict[str, Any] = {"severity": severity, "event": event}
    message.update(payload)
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def generate_test_tone(duration_seconds: float, frequency_hz: float, sample_rate: int = 16_000):
    total_ms = max(int(duration_seconds * 1000), 1)
    segment = Sine(frequency_hz).to_audio_segment(duration=total_ms)
    segment = segment.set_frame_rate(sample_rate).set_sample_width(2).set_channels(1)
    return segment


def run_adb(serial: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    return subprocess.run(cmd, check=check, text=True, capture_output=True, encoding="utf-8", errors="replace")


def ensure_project_media(serial: str, package: str) -> None:
    result = run_adb(
        serial,
        "shell",
        "appops",
        "set",
        package,
        "PROJECT_MEDIA",
        "allow",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to grant PROJECT_MEDIA app-op for {package}: {result.stderr.strip() or result.stdout.strip()}",
        )


def list_control_files(serial: str, package: str) -> list[str]:
    proc = run_adb(serial, "shell", "run-as", package, "ls", "files/control-harness", check=False)
    if proc.returncode != 0:
        return []
    files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return [name for name in files if name.endswith(".wav")]


def read_control_file(serial: str, package: str, filename: str) -> bytes:
    cmd = [
        "adb",
        *(["-s", serial] if serial else []),
        "exec-out",
        "run-as",
        package,
        "cat",
        f"files/control-harness/{filename}",
    ]
    proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE)
    return proc.stdout


def _compute_match_metrics(prompt_segment, captured_segment) -> dict[str, float]:
    """Compare prompt/captured audio and return normalized metrics."""

    if captured_segment.channels != prompt_segment.channels:
        captured_segment = captured_segment.set_channels(prompt_segment.channels)
    captured_segment = captured_segment.set_frame_rate(prompt_segment.frame_rate)

    duration_ms = int(min(len(prompt_segment), len(captured_segment)))
    if duration_ms <= 0:
        raise RuntimeError("Captured audio duration is empty; cannot compare")

    prompt_trimmed = prompt_segment[:duration_ms]
    captured_trimmed = captured_segment[:duration_ms]

    prompt_samples = array("h", prompt_trimmed.get_array_of_samples())
    captured_samples = array("h", captured_trimmed.get_array_of_samples())

    sample_count = min(len(prompt_samples), len(captured_samples))
    if sample_count == 0:
        raise RuntimeError("Audio comparison sample count is zero")

    diff_energy = 0.0
    prompt_energy = 0.0
    captured_energy = 0.0
    for idx in range(sample_count):
        prompt_val = prompt_samples[idx]
        captured_val = captured_samples[idx]
        delta = prompt_val - captured_val
        diff_energy += float(delta * delta)
        prompt_energy += float(prompt_val * prompt_val)
        captured_energy += float(captured_val * captured_val)

    mse = diff_energy / float(sample_count)
    prompt_rms = math.sqrt(prompt_energy / float(sample_count))
    captured_rms = math.sqrt(max(1.0, captured_energy / float(sample_count)))
    normalized = math.sqrt(mse) / max(1.0, prompt_rms)
    return {
        "normalizedError": normalized,
        "promptRms": prompt_rms,
        "capturedRms": captured_rms,
        "sampleCount": float(sample_count),
    }


def run_handset_audio_loop(
    *,
    backend: str,
    device_id: str,
    control_token: str,
    device_serial: str = "",
    package: str = "com.ringdown.mobile.debug",
    frequency: float = 440.0,
    duration: float = 1.5,
    output_dir: str = "artifacts",
    session_id: Optional[str] = None,
    control_key: Optional[str] = None,
    reuse_existing: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Execute the handset audio loop using deterministic playback."""

    base_url = backend.rstrip("/")
    log_event(
        "INFO",
        "handset_harness_begin",
        backend=base_url,
        deviceId=device_id,
        reuseExisting=reuse_existing,
    )

    log_event("INFO", "handset_harness_grant_project_media", package=package, deviceSerial=device_serial or "")
    ensure_project_media(device_serial, package)
    log_event("INFO", "handset_harness_project_media_granted", package=package)

    resolved_session_id = session_id.strip() if session_id else ""
    resolved_control_key = control_key.strip() if control_key else ""
    session_payload: Dict[str, Any] = {}
    if resolved_session_id and resolved_control_key:
        log_event(
            "INFO",
            "handset_harness_using_provided_session",
            sessionId=resolved_session_id,
        )
    else:
        if reuse_existing:
            session_payload = ensure_active_session(
                base_url,
                device_id,
                control_token,
                timeout=timeout,
            )
        else:
            log_event("INFO", "handset_harness_creating_session", deviceId=device_id)
            session_payload = create_session(base_url, device_id, timeout=timeout)
        resolved_session_id = session_payload["sessionId"]
        metadata = session_payload.get("metadata") or {}
        control_meta = metadata.get("control") or {}
        resolved_control_key = control_meta.get("key") or ""
        if not resolved_control_key:
            raise RuntimeError("Control channel metadata missing from session response")
        log_event(
            "INFO",
            "handset_harness_session_ready",
            sessionId=resolved_session_id,
            expiresAt=session_payload.get("expiresAt"),
            reused=reuse_existing,
        )

    existing_files = set(list_control_files(device_serial, package))
    log_event("INFO", "handset_harness_initial_files", count=len(existing_files))

    tone_segment = generate_test_tone(duration_seconds=duration, frequency_hz=frequency)
    audio_b64 = audiosegment_to_base64_wav(tone_segment)
    control_payload = {
        "sessionId": resolved_session_id,
        "message": {
            "promptId": "harness-sine",
            "audioBase64": audio_b64,
            "sampleRateHz": 16_000,
            "channels": 1,
            "format": "wav",
            "metadata": {
                "frequencyHz": frequency,
                "durationSeconds": duration,
                "sampleWidthBytes": 2,
            },
        },
    }

    log_event("INFO", "handset_harness_enqueue", sessionId=resolved_session_id)
    enqueue_resp = requests.post(
        f"{base_url}/v1/mobile/managed-av/control",
        headers={
            "X-Ringdown-Control-Token": control_token,
            "X-Ringdown-Control-Key": resolved_control_key,
        },
        json=control_payload,
        timeout=timeout,
    )
    enqueue_resp.raise_for_status()
    message_id = enqueue_resp.json().get("messageId")
    log_event("INFO", "handset_harness_enqueued", messageId=message_id)

    log_event("INFO", "handset_harness_wait_for_capture", sessionId=resolved_session_id)
    time.sleep(3.0)
    deadline = time.time() + 60.0
    discovered: Optional[str] = None
    while time.time() < deadline:
        time.sleep(1.0)
        current = set(list_control_files(device_serial, package))
        new_files = current - existing_files
        if new_files:
            discovered = sorted(new_files)[-1]
            break
    if not discovered:
        raise RuntimeError("No handset control harness artifact found within timeout")

    log_event("INFO", "handset_harness_fetch_artifact", filename=discovered)
    wav_bytes = read_control_file(device_serial, package, discovered)
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir_path / discovered
    wav_path.write_bytes(wav_bytes)
    log_event("INFO", "handset_harness_artifact_saved", path=str(wav_path))

    captured_segment = base64_wav_to_audiosegment(base64.b64encode(wav_bytes).decode("ascii"))
    metrics = _compute_match_metrics(tone_segment, captured_segment)
    if metrics["normalizedError"] > 0.35:
        raise RuntimeError(
            f"Captured audio diverges from prompt: normalized error {metrics['normalizedError']:.3f}",
        )

    summary = {
        "sessionId": resolved_session_id,
        "messageId": message_id,
        "wavFile": str(wav_path),
        "bytesCaptured": len(wav_bytes),
        "frequencyHz": frequency,
        "durationSeconds": duration,
        "capturedDurationSeconds": captured_segment.duration_seconds,
        "normalizedError": metrics["normalizedError"],
        "promptRms": metrics["promptRms"],
        "capturedRms": metrics["capturedRms"],
    }
    log_event("INFO", "handset_harness_complete", **summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, help="Backend base URL, e.g. https://example.a.run.app")
    parser.add_argument("--device-id", required=True, help="Registered mobile device identifier")
    parser.add_argument("--control-token", required=True, help="Value of MANAGED_AV_CONTROL_TOKEN")
    parser.add_argument("--device-serial", default="", help="adb serial for the handset (optional)")
    parser.add_argument("--package", default="com.ringdown.mobile.debug", help="Android package name")
    parser.add_argument("--frequency", type=float, default=440.0, help="Sine frequency in Hz (default: 440)")
    parser.add_argument("--duration", type=float, default=1.5, help="Prompt duration seconds (default: 1.5)")
    parser.add_argument("--output-dir", default="artifacts", help="Directory to place captured WAV output")
    parser.add_argument("--session-id", default="", help="Reuse existing managed session identifier")
    parser.add_argument("--control-key", default="", help="Control key for the existing session")
    parser.add_argument(
        "--reuse-existing",
        dest="reuse_existing",
        action="store_true",
        default=True,
        help="Reuse an existing managed session via the automation helper (default).",
    )
    parser.add_argument(
        "--no-reuse-existing",
        dest="reuse_existing",
        action="store_false",
        help="Always create a fresh managed session before enqueuing audio.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout in seconds for backend requests (default: 30).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.session_id and not args.control_key:
        raise RuntimeError("Both --session-id and --control-key must be provided together.")
    if args.control_key and not args.session_id:
        raise RuntimeError("Both --session-id and --control-key must be provided together.")

    summary = run_handset_audio_loop(
        backend=args.backend,
        device_id=args.device_id,
        control_token=args.control_token,
        device_serial=args.device_serial,
        package=args.package,
        frequency=args.frequency,
        duration=args.duration,
        output_dir=args.output_dir,
        session_id=args.session_id or None,
        control_key=args.control_key or None,
        reuse_existing=args.reuse_existing,
        timeout=args.timeout,
    )
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
