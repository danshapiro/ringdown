"""Utility for approving pending mobile handset registrations."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from ruamel.yaml import YAML


_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.preserve_quotes = True


@dataclass
class DeviceRequest:
    """Row describing a pending handset approval."""

    device_id: str
    label: str
    agent: str
    created_at: datetime
    notes: Optional[str]


def _default_config_path() -> Path:
    env_value = os.getenv("RINGDOWN_CONFIG_PATH")
    if env_value:
        return Path(env_value)
    return Path.cwd() / "config.yaml"


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        data = _yaml.load(handle) or {}
    return data


def _save_config(config_path: Path, data: dict) -> None:
    with config_path.open("w", encoding="utf-8") as handle:
        _yaml.dump(data, handle)


def _parse_created_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip())
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _is_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"false", "0", "no", "off"}:
            return False
        if lowered in {"true", "1", "yes", "on"}:
            return True
        return bool(lowered)
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _iter_pending_devices(data: dict) -> Iterable[DeviceRequest]:
    devices = data.get("mobile_devices") or {}
    for device_id, payload in devices.items():
        if not isinstance(payload, dict):
            continue
        if _is_enabled(payload.get("enabled")):
            continue
        created_at = _parse_created_at(payload.get("created_at") or payload.get("createdAt"))
        yield DeviceRequest(
            device_id=device_id,
            label=str(payload.get("label") or device_id),
            agent=str(payload.get("agent") or "unknown-caller"),
            created_at=created_at,
            notes=payload.get("notes"),
        )


def list_pending_devices(config_path: Path | None = None) -> List[DeviceRequest]:
    """Return all pending device approvals sorted by creation time."""

    resolved = config_path or _default_config_path()
    data = _load_config(resolved)
    pending = sorted(_iter_pending_devices(data), key=lambda item: item.created_at)
    return list(pending)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def approve_device(config_path: Path | None, device_id: str, *, agent: Optional[str] = None) -> DeviceRequest:
    """Approve *device_id* by setting enabled=true and optional agent."""

    if not device_id:
        raise ValueError("device_id must be provided")

    resolved = config_path or _default_config_path()
    data = _load_config(resolved)
    devices = data.get("mobile_devices")
    if not isinstance(devices, dict) or device_id not in devices:
        raise KeyError(device_id)

    entry = devices[device_id]
    if not isinstance(entry, dict):
        entry = devices[device_id] = dict(entry or {})

    if agent:
        entry["agent"] = agent

    entry["enabled"] = True
    entry["approved_at"] = _now().isoformat()

    _save_config(resolved, data)

    created_at = _parse_created_at(entry.get("created_at") or entry.get("createdAt"))
    return DeviceRequest(
        device_id=device_id,
        label=str(entry.get("label") or device_id),
        agent=str(entry.get("agent") or "unknown-caller"),
        created_at=created_at,
        notes=entry.get("notes"),
    )


def _format_row(row: DeviceRequest) -> str:
    note = f" | {row.notes}" if row.notes else ""
    return (
        f"{row.device_id:<20}  {row.label:<20}  {row.agent:<20}  {row.created_at.isoformat()}{note}"
    )


def _handle_list(args: argparse.Namespace) -> int:
    config_path = Path(args.config) if args.config else None
    pending = list_pending_devices(config_path)
    if not pending:
        print("No pending handset requests.")
        return 0

    print("Pending handset approvals:\n")
    for row in pending:
        print(_format_row(row))
    return 0


def _handle_approve(args: argparse.Namespace) -> int:
    config_path = Path(args.config) if args.config else None
    result = approve_device(config_path, args.device_id, agent=args.agent)
    print(f"Approved {result.device_id} for agent {result.agent}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Approve pending handset registrations")
    parser.add_argument("--config", help="Path to config.yaml (defaults to RINGDOWN_CONFIG_PATH or ./config.yaml)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Show pending handset approvals")
    list_parser.set_defaults(func=_handle_list)

    approve_parser = subparsers.add_parser("approve", help="Approve a handset by device id")
    approve_parser.add_argument("device_id", help="Device identifier to approve")
    approve_parser.add_argument("--agent", help="Override agent mapping during approval")
    approve_parser.set_defaults(func=_handle_approve)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    raise SystemExit(main())
