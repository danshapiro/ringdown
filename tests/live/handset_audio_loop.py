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
import time
from array import array
from pathlib import Path
from typing import Optional

import requests
from pydub.generators import Sine
from tests.live.control_audio_utils import (
    audiosegment_to_base64_wav,
    base64_wav_to_audiosegment,
)


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


def list_control_files(serial: str, package: str) -> list[str]:
    proc = run_adb(serial, "shell", "run-as", package, "ls", "files/control-harness", check=False)
    if proc.returncode != 0:
        return []
    files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return [name for name in files if name.endswith(".wav")]


def read_control_file(serial: str, package: str, filename: str) -> bytes:
    proc = run_adb(
        serial,
        "exec-out",
        "run-as",
        package,
        "cat",
        f"files/control-harness/{filename}",
    )
    return proc.stdout.encode("latin1")  # exec-out returns bytes via stdout


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.backend.rstrip("/")

    print("Creating managed session...")
    session_resp = requests.post(
        f"{base_url}/v1/mobile/voice/session",
        json={"deviceId": args.device_id},
        timeout=30,
    )
    session_resp.raise_for_status()
    session_payload = session_resp.json()
    session_id = session_payload["sessionId"]
    control_meta = session_payload.get("metadata", {}).get("control") or {}
    control_key: Optional[str] = control_meta.get("key")
    if not control_key:
        raise RuntimeError("Control channel metadata missing from session response")

    print(f"Session {session_id} established; queuing control prompt...")
    tone_segment = generate_test_tone(duration_seconds=args.duration, frequency_hz=args.frequency)
    audio_b64 = audiosegment_to_base64_wav(tone_segment)
    control_payload = {
        "sessionId": session_id,
        "message": {
            "promptId": "harness-sine",
            "audioBase64": audio_b64,
            "sampleRateHz": 16_000,
            "channels": 1,
            "format": "wav",
            "metadata": {
                "frequencyHz": args.frequency,
                "durationSeconds": args.duration,
                "sampleWidthBytes": 2,
            },
        },
    }
    enqueue_resp = requests.post(
        f"{base_url}/v1/mobile/managed-av/control",
        headers={
            "X-Ringdown-Control-Token": args.control_token,
            "X-Ringdown-Control-Key": control_key,
        },
        json=control_payload,
        timeout=30,
    )
    enqueue_resp.raise_for_status()
    message_id = enqueue_resp.json().get("messageId")
    print(f"Enqueued control message {message_id}")

    print("Waiting for handset to process control prompt...")
    time.sleep(3.0)

    existing_files = set(list_control_files(args.device_serial, args.package))
    deadline = time.time() + 30.0
    discovered: Optional[str] = None
    while time.time() < deadline:
        time.sleep(1.0)
        current = set(list_control_files(args.device_serial, args.package))
        new_files = current - existing_files
        if new_files:
            discovered = sorted(new_files)[-1]
            break
    if not discovered:
        raise RuntimeError("No handset control harness artifact found within timeout")

    print(f"Retrieving handset artifact {discovered} ...")
    wav_bytes = read_control_file(args.device_serial, args.package, discovered)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / discovered
    wav_path.write_bytes(wav_bytes)
    print(f"Saved WAV to {wav_path}")

    captured_segment = base64_wav_to_audiosegment(base64.b64encode(wav_bytes).decode("ascii"))
    metrics = _compute_match_metrics(tone_segment, captured_segment)
    if metrics["normalizedError"] > 0.35:
        raise RuntimeError(
            f"Captured audio diverges from prompt: normalized error {metrics['normalizedError']:.3f}",
        )

    summary = {
        "sessionId": session_id,
        "messageId": message_id,
        "wavFile": str(wav_path),
        "bytesCaptured": len(wav_bytes),
        "frequencyHz": args.frequency,
        "durationSeconds": args.duration,
        "capturedDurationSeconds": captured_segment.duration_seconds,
        "normalizedError": metrics["normalizedError"],
        "promptRms": metrics["promptRms"],
        "capturedRms": metrics["capturedRms"],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
