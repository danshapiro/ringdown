#!/usr/bin/env python3
"""Helper utilities for retrieving managed session metadata for handset harness runs."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, Optional

import requests


DEFAULT_TIMEOUT_SECONDS = 30.0


def _log_event(severity: str, event: str, **payload: Any) -> None:
    message: Dict[str, Any] = {"severity": severity, "event": event}
    message.update(payload)
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def fetch_active_session(
    base_url: str,
    device_id: str,
    control_token: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[Dict[str, Any]]:
    """Return the active managed session metadata for *device_id*, if available."""

    url = f"{base_url.rstrip('/')}/v1/mobile/managed-av/sessions/active"
    headers = {"X-Ringdown-Control-Token": control_token}
    params = {"deviceId": device_id}

    response = requests.get(url, headers=headers, params=params, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def create_session(
    base_url: str,
    device_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Request a new managed session for *device_id*."""

    url = f"{base_url.rstrip('/')}/v1/mobile/voice/session"
    response = requests.post(url, json={"deviceId": device_id}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def ensure_active_session(
    base_url: str,
    device_id: str,
    control_token: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = 2,
    retry_delay: float = 2.0,
    create_when_missing: bool = True,
) -> Dict[str, Any]:
    """Fetch or create a managed session for automation workflows."""

    attempt = 0
    while True:
        attempt += 1
        session = fetch_active_session(base_url, device_id, control_token, timeout=timeout)
        if session is not None:
            _log_event(
                "INFO",
                "managed_session_helper_reuse",
                deviceId=device_id,
                sessionId=session.get("sessionId"),
                attempt=attempt,
            )
            return session
        if not create_when_missing:
            raise RuntimeError("No active managed session available for device and creation disabled")

        _log_event("INFO", "managed_session_helper_create", deviceId=device_id, attempt=attempt)
        session = create_session(base_url, device_id, timeout=timeout)
        control_meta = (session.get("metadata") or {}).get("control") or {}
        if control_meta.get("key"):
            return session

        if attempt > retries:
            raise RuntimeError("Managed session created without control metadata")
        _log_event(
            "WARNING",
            "managed_session_helper_missing_control_key",
            deviceId=device_id,
            sessionId=session.get("sessionId"),
            attempt=attempt,
        )
        time.sleep(retry_delay)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, help="Backend base URL, e.g. https://example.a.run.app")
    parser.add_argument("--device-id", required=True, help="Registered device identifier")
    parser.add_argument("--control-token", required=True, help="Value of MANAGED_AV_CONTROL_TOKEN")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="HTTP timeout in seconds")
    parser.add_argument(
        "--no-create",
        action="store_true",
        help="Only fetch existing sessions; do not create a new one if missing",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        session = ensure_active_session(
            args.backend,
            args.device_id,
            args.control_token,
            timeout=args.timeout,
            create_when_missing=not args.no_create,
        )
    except Exception as exc:  # noqa: BLE001
        _log_event("ERROR", "managed_session_helper_failure", deviceId=args.device_id, error=str(exc))
        return 1

    _log_event(
        "INFO",
        "managed_session_helper_success",
        deviceId=args.device_id,
        sessionId=session.get("sessionId"),
        expiresAt=session.get("expiresAt"),
    )
    sys.stdout.write(json.dumps(session, indent=2) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
