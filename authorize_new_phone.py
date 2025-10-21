#!/usr/bin/env python3
"""
Authorize newly registered phones by promoting the pending config to live.

The helper assumes:
* You are somewhere inside the Ringdown git repository (it resolves the repo root).
* The proposed configuration lives in the shared asset bucket created by
  `cloudrun-deploy.py`, under `config/pending/config.yaml`.
* The approved configuration should be written to `config/live/config.yaml`.

Workflow:
1. Download the pending config from GCS and show a unified diff against the local `config.yaml`.
2. On confirmation, enable every `enabled: false` entry, back up the local file (unless `--no-backup`),
   write the updated config locally, upload it to the live location, and remove the pending artifact.
"""

from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Optional, Sequence, Tuple

from google.api_core.exceptions import NotFound  # type: ignore
from google.cloud import storage  # type: ignore
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

_PROJECT_ENV_KEYS = (
    "DEPLOY_PROJECT_ID",
    "LIVE_TEST_PROJECT_ID",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
)
_BUCKET_SUFFIX = "-test-assets"
_PENDING_BLOB = "config/pending/config.yaml"
_LIVE_BLOB = "config/live/config.yaml"

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.preserve_quotes = True


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Promote the pending Ringdown phone config to production."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Approve without prompting for confirmation.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip creating a timestamped backup of the previous local config.",
    )
    parser.add_argument(
        "--device-id",
        action="append",
        dest="device_ids",
        help=(
            "Enable one or more specific device IDs. Required when no pending configuration "
            "is present; may be supplied multiple times."
        ),
    )
    return parser.parse_args(argv)


def _require_repo_root() -> Path:
    """Return the git repository root or terminate if not inside the repo."""

    try:
        result = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print("[error] authorize_new_phone.py must be run inside the git repository.", file=sys.stderr)
        raise SystemExit(1) from exc

    root = Path(result.stdout.strip()).resolve()
    if not root.exists():
        print(f"[error] Reported git root {root} does not exist.", file=sys.stderr)
        raise SystemExit(1)
    return root


def _resolve_project_id() -> str:
    """Detect the active GCP project, mirroring cloudrun-deploy defaults."""

    for key in _PROJECT_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            return value

    try:
        result = subprocess.run(  # noqa: S603
            ["gcloud", "config", "get-value", "project"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:  # gcloud not installed
        print(
            "[error] Unable to locate gcloud. Install the Google Cloud SDK or set DEPLOY_PROJECT_ID.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except subprocess.CalledProcessError:
        result = None

    if result:
        project = result.stdout.strip()
        if project and project.lower() != "(unset)":
            return project

    print(
        "[error] Could not determine GCP project. "
        "Set DEPLOY_PROJECT_ID/LIVE_TEST_PROJECT_ID or run `gcloud config set project <id>`.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _storage_bucket(client: storage.Client, bucket_name: str) -> storage.Bucket:
    try:
        return client.get_bucket(bucket_name)
    except NotFound as exc:
        print(f"[error] Bucket gs://{bucket_name} not found. Run cloudrun-deploy at least once.", file=sys.stderr)
        raise SystemExit(1) from exc


def _download_blob_text(bucket: storage.Bucket, blob_name: str) -> str:
    blob = bucket.blob(blob_name)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket.name}/{blob_name}")
    return blob.download_as_text(encoding="utf-8")


def _upload_blob_text(bucket: storage.Bucket, blob_name: str, content: str) -> None:
    blob = bucket.blob(blob_name)
    blob.upload_from_string(content, content_type="application/x-yaml")


def _delete_blob(bucket: storage.Bucket, blob_name: str) -> None:
    blob = bucket.blob(blob_name)
    try:
        blob.delete()
    except NotFound:
        pass


def _ensure_commented_map(value: object) -> CommentedMap:
    if isinstance(value, CommentedMap):
        return value
    data = CommentedMap()
    if isinstance(value, dict):
        for key, item in value.items():
            data[key] = item
    return data


def _enable_devices(content: str, device_ids: Optional[Sequence[str]]) -> Tuple[str, list[str], list[str], list[str]]:
    """Enable specific device IDs (or all disabled devices when ``device_ids`` is None)."""

    payload = _yaml.load(content) or CommentedMap()
    payload = _ensure_commented_map(payload)

    devices_raw = payload.get("mobile_devices")
    devices = _ensure_commented_map(devices_raw) if devices_raw is not None else CommentedMap()
    payload["mobile_devices"] = devices

    updated = False
    enabled: list[str] = []
    created: list[str] = []
    missing: list[str] = []

    targets = list(dict.fromkeys(device_ids)) if device_ids else list(devices.keys())

    for device_id in targets:
        entry_raw = devices.get(device_id)
        if entry_raw is None:
            if device_ids:
                entry = CommentedMap()
                entry["label"] = device_id
                entry["agent"] = "unknown-caller"
                entry["enabled"] = True
                entry["created_at"] = datetime.now(timezone.utc).isoformat()
                devices[device_id] = entry
                enabled.append(device_id)
                created.append(device_id)
                updated = True
            continue

        entry = _ensure_commented_map(entry_raw)
        if entry_raw is not entry:
            devices[device_id] = entry

        if bool(entry.get("enabled")):
            continue

        entry["enabled"] = True
        enabled.append(device_id)
        updated = True

    if not updated:
        return content, enabled, missing, created

    buffer = StringIO()
    _yaml.dump(payload, buffer)
    return buffer.getvalue(), enabled, missing, created


def _print_diff(local_text: str, proposed_text: str, local_label: str, proposed_label: str) -> None:
    local_lines = local_text.splitlines(keepends=False)
    proposed_lines = proposed_text.splitlines(keepends=False)
    diff = difflib.unified_diff(
        local_lines,
        proposed_lines,
        fromfile=local_label,
        tofile=proposed_label,
        lineterm="",
    )
    diff_output = list(diff)
    if diff_output:
        print("\n".join(diff_output))
    else:
        print("No differences between local configuration and proposed file.")


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    repo_root = _require_repo_root()
    os.chdir(repo_root)

    local_path = repo_root / "config.yaml"
    if not local_path.exists():
        print(f"[error] Local config file not found at {local_path}", file=sys.stderr)
        return 1

    project_id = _resolve_project_id()
    bucket_name = f"{project_id}{_BUCKET_SUFFIX}"
    pending_uri = f"gs://{bucket_name}/{_PENDING_BLOB}"
    live_uri = f"gs://{bucket_name}/{_LIVE_BLOB}"

    client = storage.Client(project=project_id)
    bucket = _storage_bucket(client, bucket_name)

    device_ids = args.device_ids or []

    used_live_config = False

    try:
        proposed_text = _download_blob_text(bucket, _PENDING_BLOB)
        source_uri = pending_uri
    except FileNotFoundError as exc:
        if not device_ids:
            print(f"[error] Pending configuration not found at {exc}", file=sys.stderr)
            print("Provide --device-id to enable specific devices directly in the live configuration.", file=sys.stderr)
            return 1
        print(f"[warn] Pending configuration not found at {exc}; falling back to live configuration.", file=sys.stderr)
        try:
            proposed_text = _download_blob_text(bucket, _LIVE_BLOB)
        except FileNotFoundError as live_exc:
            print(f"[error] Live configuration not found at {live_exc}", file=sys.stderr)
            return 1
        used_live_config = True
        source_uri = live_uri

    if not proposed_text.strip():
        print("[error] Proposed configuration is empty.", file=sys.stderr)
        return 1

    local_text = local_path.read_text(encoding="utf-8")

    print("=== Diff: local vs proposed ===")
    _print_diff(local_text, proposed_text, str(local_path), source_uri)
    print("=== End diff ===")

    if not args.yes:
        response = input("Apply proposed configuration? [y/N]: ").strip().lower()
        if response not in {"y", "yes"}:
            print("Aborted; leaving files unchanged.")
            return 0

    updated_text, enabled_devices, missing_devices, created_devices = _enable_devices(
        proposed_text,
        device_ids if device_ids else None,
    )

    if missing_devices:
        print(
            "Warning: the following device IDs were not found in the configuration and were skipped: "
            + ", ".join(sorted(missing_devices)),
            file=sys.stderr,
        )

    if enabled_devices:
        print(f"Enabled {len(enabled_devices)} phone entr{'y' if len(enabled_devices) == 1 else 'ies'}: {', '.join(enabled_devices)}.")
        if created_devices:
            print("New device records created for: " + ", ".join(created_devices))
    else:
        print("No disabled phone entries were toggled.")

    if used_live_config:
        print("NOTE: changes were applied directly to the live configuration (no pending proposal was available).")

    if local_path.exists() and not args.no_backup:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = local_path.with_suffix(f".{timestamp}.bak")
        backup_path.write_text(local_text, encoding="utf-8")
        print(f"Created backup of existing local config at {backup_path}")

    local_path.write_text(updated_text, encoding="utf-8")
    print(f"Local configuration updated: {local_path}")

    try:
        _upload_blob_text(bucket, _LIVE_BLOB, updated_text)
        print(f"Uploaded approved configuration to {live_uri}")
        if not used_live_config:
            _delete_blob(bucket, _PENDING_BLOB)
            print(f"Removed pending proposal at {pending_uri}")
    except Exception as exc:  # noqa: BLE001
        print(f"[error] Failed to upload updated configuration: {exc}", file=sys.stderr)
        print("Local file has been updated; remote configuration may need manual attention.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
