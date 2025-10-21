#!/usr/bin/env python3
"""
Approve a Ringdown Android client device by ensuring its identifier is enabled
in config.yaml and redeploying the backend.

The helper:
1. Determines the device ID (either supplied via --device-id or read from adb).
2. Runs authorize_new_phone.py with --device-id to mark the device approved.
3. Invokes cloudrun-deploy.py unless --skip-deploy is specified.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ADB_RELATIVE = Path("platform-tools") / "adb"
APP_PACKAGE = "com.ringdown"
DATASTORE_PATH = "files/datastore/ringdown_device.preferences_pb"


def _exe_path(base: Path) -> Path:
    if platform.system() == "Windows":
        candidate = base.with_suffix(".exe")
        if candidate.exists():
            return candidate
    return base


def resolve_adb(explicit: str | None) -> str:
    if explicit:
        return explicit

    android_root = os.getenv("ANDROID_SDK_ROOT") or os.getenv("ANDROID_HOME")
    if android_root:
        candidate = _exe_path(Path(android_root) / ADB_RELATIVE)
        if candidate.exists():
            return str(candidate)

    # Fallback to PATH
    return "adb"


def run_subprocess(args: Sequence[str], *, check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        check=check,
        capture_output=capture_output,
        text=True,
    )


def read_device_id(adb_path: str, serial: str) -> str:
    adb_cmd = [
        adb_path,
        "-s",
        serial,
        "shell",
        "run-as",
        APP_PACKAGE,
        "cat",
        DATASTORE_PATH,
    ]
    result = run_subprocess(adb_cmd, capture_output=True)
    data = result.stdout.strip()
    match = re.search(r"device_id[^\w-]*([0-9a-fA-F-]{36})", data)
    if not match:
        raise RuntimeError(f"Unable to parse device ID from adb output:\n{data}")
    return match.group(1).lower()


def run_authorize(script_path: Path, device_id: str, extra_args: list[str]) -> None:
    cmd = [
        sys.executable,
        str(script_path),
        "--yes",
        "--device-id",
        device_id,
    ]
    cmd.extend(extra_args)
    run_subprocess(cmd)


def run_deploy(script_path: Path, extra_args: list[str]) -> None:
    cmd = [sys.executable, str(script_path)]
    cmd.extend(extra_args)
    run_subprocess(cmd)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Approve Android client device and redeploy backend.")
    parser.add_argument("--device", help="ADB device serial. Defaults to ANDROID_SERIAL or the first attached device.")
    parser.add_argument("--device-id", help="Override the device UUID instead of reading from adb.")
    parser.add_argument("--adb", help="Path to adb. Defaults to ANDROID_SDK_ROOT/platform-tools/adb or PATH.")
    parser.add_argument("--authorize-script", default="authorize_new_phone.py", help="Path to authorize_new_phone.py.")
    parser.add_argument("--cloudrun-script", default="cloudrun-deploy.py", help="Path to cloudrun-deploy.py.")
    parser.add_argument("--skip-deploy", action="store_true", help="Skip running cloudrun-deploy.py.")
    parser.add_argument("--deploy-arg", action="append", default=[], help="Additional argument passed to cloudrun-deploy.py.")
    parser.add_argument("--authorize-arg", action="append", default=[], help="Additional argument passed to authorize_new_phone.py.")
    parser.add_argument("--project-id", help="Override DEPLOY_PROJECT_ID before calling helper scripts.")
    return parser.parse_args()


def detect_device_serial(adb_path: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    env_serial = os.getenv("ANDROID_SERIAL")
    if env_serial:
        return env_serial
    result = run_subprocess([adb_path, "devices"], capture_output=True)
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        if line.endswith("device"):
            return line.split()[0]
    raise RuntimeError("No connected device detected. Supply --device or set ANDROID_SERIAL.")


def main() -> int:
    args = parse_args()
    adb_path = resolve_adb(args.adb)
    serial = detect_device_serial(adb_path, args.device)

    if args.device_id:
        device_id = args.device_id.lower()
        print(f"Using supplied device ID: {device_id}")
    else:
        print(f"Reading device ID from {serial}...")
        device_id = read_device_id(adb_path, serial)
        print(f"Detected device ID: {device_id}")

    repo_root = Path(__file__).resolve().parents[2]
    authorize_path = (repo_root / args.authorize_script).resolve()
    deploy_path = (repo_root / args.cloudrun_script).resolve()

    if not authorize_path.exists():
        raise FileNotFoundError(f"authorize script not found at {authorize_path}")

    if args.project_id:
        os.environ["DEPLOY_PROJECT_ID"] = args.project_id

    print(f"Running authorize_new_phone.py for {device_id}...")
    run_authorize(authorize_path, device_id, args.authorize_arg)

    if args.skip_deploy:
        print("Skipping cloudrun-deploy.py as requested.")
        return 0

    if not deploy_path.exists():
        raise FileNotFoundError(f"cloudrun-deploy.py not found at {deploy_path}")

    print("Running cloudrun-deploy.py...")
    run_deploy(deploy_path, args.deploy_arg)
    print("Device approval complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
