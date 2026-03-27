"""Utility for approving pending mobile handset registrations."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
    notes: str | None


DEVICE_KEY_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


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


def _refresh_runtime_cache() -> None:
    """Best-effort cache invalidation for long-lived backend processes."""

    try:
        from app import settings  # type: ignore
    except Exception:
        return

    try:
        settings.refresh_config_cache()
    except Exception:
        # Ignore cache refresh errors – deployments handle remote cases.
        return


def _parse_created_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip())
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=UTC)


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


def list_pending_devices(config_path: Path | None = None) -> list[DeviceRequest]:
    """Return all pending device approvals sorted by creation time."""

    resolved = config_path or _default_config_path()
    data = _load_config(resolved)
    pending = sorted(_iter_pending_devices(data), key=lambda item: item.created_at)
    return list(pending)


def _now() -> datetime:
    return datetime.now(UTC)


def approve_device(
    config_path: Path | None, device_id: str, *, agent: str | None = None
) -> DeviceRequest:
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
    _refresh_runtime_cache()

    created_at = _parse_created_at(entry.get("created_at") or entry.get("createdAt"))
    return DeviceRequest(
        device_id=device_id,
        label=str(entry.get("label") or device_id),
        agent=str(entry.get("agent") or "unknown-caller"),
        created_at=created_at,
        notes=entry.get("notes"),
    )


def auto_approve_single_pending(
    config_path: Path | None = None,
    *,
    agent: str | None = None,
) -> DeviceRequest | None:
    """Approve the sole pending handset request, if exactly one exists.

    Returns the approved device entry, or ``None`` when no pending devices exist.
    Raises ``RuntimeError`` if multiple pending approvals are present.
    """

    pending = list_pending_devices(config_path)
    if not pending:
        return None
    if len(pending) > 1:
        raise RuntimeError(
            "Multiple pending handset approvals detected; manual resolution required."
        )

    target = pending[0]
    chosen_agent = agent or target.agent
    return approve_device(config_path, target.device_id, agent=chosen_agent)


def _format_row(row: DeviceRequest) -> str:
    note = f" | {row.notes}" if row.notes else ""
    return (
        f"{row.device_id:<20}  {row.label:<20}  {row.agent:<20}  {row.created_at.isoformat()}{note}"
    )


def _resolve_deploy_script(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(__file__).resolve().with_name("cloudrun-deploy.py")


def _run_deploy(script_path: Path) -> None:
    if not script_path.exists():
        raise FileNotFoundError(f"Deploy script not found at {script_path}")

    cmd = [sys.executable, str(script_path)]
    print(f"Triggering deploy via: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


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
    parser: argparse.ArgumentParser | None = getattr(args, "parser", None)

    if args.auto and args.device_id:
        if parser:
            parser.error("Specify either a device id or --auto, not both.")
        raise SystemExit(2)

    if not args.auto and not args.device_id:
        if parser:
            parser.error("device_id is required unless using --auto.")
        raise SystemExit(2)

    if args.auto:
        pending = list_pending_devices(config_path)
        if not pending:
            print("No pending handset requests found.")
            return 1
        if len(pending) > 1:
            print("Multiple pending handset requests found; refusing to auto-approve.\n")
            for row in pending:
                print(_format_row(row))
            return 1

        selected = pending[0]
        print(
            "Auto-approving pending handset "
            f"{selected.device_id} ({selected.label}) requested by {selected.agent}."
        )
        target_device_id = selected.device_id
    else:
        target_device_id = args.device_id

    result = approve_device(config_path, target_device_id, agent=args.agent)
    print(f"Approved {result.device_id} for agent {result.agent}.")
    if args.sync_env:
        env_path = Path(args.env_file).resolve()
        synced = sync_env_device(
            env_path,
            config_path=config_path,
            allow_disabled=True,
            prefer_label=args.prefer_label,
        )
        print(f"Updated {env_path} with LIVE_TEST_MOBILE_DEVICE_ID={synced}.")
    if args.deploy:
        script_path = _resolve_deploy_script(args.deploy_script)
        _run_deploy(script_path)
    return 0


def _load_devices(config_path: Path | None = None) -> dict[str, dict[str, object]]:
    resolved = config_path or _default_config_path()
    data = _load_config(resolved)
    devices = data.get("mobile_devices")
    if not isinstance(devices, dict) or not devices:
        raise ValueError("config.yaml missing mobile_devices section")
    # ensure dictionary of dicts
    result: dict[str, dict[str, object]] = {}
    for key, value in devices.items():
        if isinstance(value, dict):
            result[str(key)] = value
    if not result:
        raise ValueError("No valid mobile device entries found")
    return result


def _select_device(
    devices: dict[str, dict[str, object]],
    *,
    allow_disabled: bool,
    prefer_label: str,
) -> tuple[str, dict[str, object]]:
    prefer_lower = prefer_label.lower()
    candidates: list[tuple[int, float, str, dict[str, object]]] = []
    for device_id, entry in devices.items():
        enabled = _is_enabled(entry.get("enabled"))
        if not allow_disabled and not enabled:
            continue

        created = _parse_created_at(entry.get("created_at") or entry.get("createdAt"))
        created_ts = created.astimezone(UTC).timestamp()
        label = str(entry.get("label") or device_id)
        priority = 1
        if prefer_lower:
            haystack = f"{device_id}|{label}".lower()
            if prefer_lower in haystack:
                priority = 0
        elif DEVICE_KEY_PATTERN.match(device_id):
            priority = 0

        candidates.append((priority, -created_ts, device_id, entry))

    if not candidates:
        raise ValueError("No matching mobile devices found")

    candidates.sort()
    _, _, device_id, entry = candidates[0]
    return device_id, entry


def _rewrite_env_file(env_path: Path, device_id: str) -> None:
    if not env_path.exists():
        raise FileNotFoundError(f".env file not found: {env_path}")

    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    for idx, line in enumerate(lines):
        if line.startswith("LIVE_TEST_MOBILE_DEVICE_ID="):
            lines[idx] = f"LIVE_TEST_MOBILE_DEVICE_ID={device_id}"
            updated = True
            break

    if not updated:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"LIVE_TEST_MOBILE_DEVICE_ID={device_id}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sync_env_device(
    env_path: Path,
    *,
    config_path: Path | None = None,
    allow_disabled: bool = False,
    prefer_label: str = "",
) -> str:
    """Update LIVE_TEST_MOBILE_DEVICE_ID in env file and return the chosen device id."""

    devices = _load_devices(config_path)
    device_id, _ = _select_device(
        devices,
        allow_disabled=allow_disabled,
        prefer_label=prefer_label,
    )
    _rewrite_env_file(env_path, device_id)
    return device_id


def _handle_sync_env(args: argparse.Namespace) -> int:
    config_path = Path(args.config) if args.config else None
    env_path = Path(args.env_file)
    device_id = sync_env_device(
        env_path,
        config_path=config_path,
        allow_disabled=args.allow_disabled,
        prefer_label=args.prefer_label,
    )
    print(f"Updated {env_path} with LIVE_TEST_MOBILE_DEVICE_ID={device_id}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Approve pending handset registrations")
    parser.add_argument(
        "--config", help="Path to config.yaml (defaults to RINGDOWN_CONFIG_PATH or ./config.yaml)"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Show pending handset approvals")
    list_parser.set_defaults(func=_handle_list)

    approve_parser = subparsers.add_parser("approve", help="Approve a handset by device id")
    approve_parser.add_argument("device_id", nargs="?", help="Device identifier to approve")
    approve_parser.add_argument(
        "--auto",
        action="store_true",
        help="Approve the only pending handset request; fails if zero or multiple requests exist.",
    )
    approve_parser.add_argument("--agent", help="Override agent mapping during approval")
    approve_parser.add_argument(
        "--sync-env",
        dest="sync_env",
        action="store_true",
        help="Update LIVE_TEST_MOBILE_DEVICE_ID in .env after approval.",
    )
    approve_parser.add_argument(
        "--no-sync-env",
        dest="sync_env",
        action="store_false",
        help="Do not update LIVE_TEST_MOBILE_DEVICE_ID (default).",
    )
    approve_parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file used when --sync-env is set (default: %(default)s).",
    )
    approve_parser.add_argument(
        "--prefer-label",
        default="",
        help="When syncing env, prefer device IDs/labels containing this substring.",
    )
    approve_parser.add_argument(
        "--no-deploy",
        dest="deploy",
        action="store_false",
        help="Skip the Cloud Run deployment step (deploys by default).",
    )
    approve_parser.add_argument(
        "--deploy-script",
        help="Path to cloudrun-deploy.py (defaults to repo root).",
    )
    approve_parser.set_defaults(
        deploy=True,
        deploy_script=None,
        sync_env=False,
        auto=False,
        func=_handle_approve,
        parser=approve_parser,
    )

    sync_parser = subparsers.add_parser(
        "sync-env", help="Update LIVE_TEST_MOBILE_DEVICE_ID using config.yaml"
    )
    sync_parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file to update (default: %(default)s).",
    )
    sync_parser.add_argument(
        "--allow-disabled",
        action="store_true",
        help="Consider devices that are not yet enabled.",
    )
    sync_parser.add_argument(
        "--prefer-label",
        default="",
        help="Prefer devices whose label or ID contains this substring (case-insensitive).",
    )
    sync_parser.set_defaults(func=_handle_sync_env)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    subcommands = {"list", "approve", "sync-env"}
    has_subcommand = any(arg in subcommands for arg in raw_args if not arg.startswith("-"))
    if not has_subcommand and "--auto" in raw_args:
        insert_at = raw_args.index("--auto")
        raw_args.insert(insert_at, "approve")

    parser = build_parser()
    args = parser.parse_args(raw_args)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    raise SystemExit(main())
