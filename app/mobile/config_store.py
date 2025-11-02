"""Helpers for updating mobile device entries in config.yaml."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
import secrets
from typing import Any, Dict, Tuple

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from app import settings

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.preserve_quotes = True

_write_lock = threading.Lock()


def _ensure_commented_map(value: Any) -> CommentedMap:
    if isinstance(value, CommentedMap):
        return value
    data = CommentedMap()
    if isinstance(value, dict):
        for key, item in value.items():
            data[key] = item
    return data


def ensure_device_entry(
    device_id: str,
    *,
    label: str | None,
    metadata: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    """Ensure a mobile device exists in config.yaml.

    Returns a tuple ``(created, entry_dict)`` where ``created`` indicates whether the
    entry was newly added.  The returned dictionary reflects the current entry state.
    """

    device_key = device_id.strip()
    if not device_key:
        raise ValueError("device_id must not be empty")

    config_path = settings._config_path()  # type: ignore[attr-defined]

    with _write_lock:
        with config_path.open("r", encoding="utf-8") as handle:
            raw_data = _yaml.load(handle) or CommentedMap()

        devices = _ensure_commented_map(raw_data.get("mobile_devices"))
        raw_data["mobile_devices"] = devices

        if device_key in devices:
            entry = _ensure_commented_map(devices[device_key])
            mutated = _ensure_security_fields(entry, metadata)
            if mutated:
                with config_path.open("w", encoding="utf-8") as handle:
                    _yaml.dump(raw_data, handle)
                settings.refresh_config_cache()
            result = {k: entry[k] for k in entry}
            return False, result

        entry = CommentedMap()
        entry["label"] = label or device_key
        entry["agent"] = metadata.get("agent") or "unknown-caller"
        entry["enabled"] = False
        entry["created_at"] = datetime.now(timezone.utc).isoformat()

        poll_after = metadata.get("poll_after_seconds")
        if isinstance(poll_after, int) and poll_after > 0:
            entry["poll_after_seconds"] = poll_after

        context: Dict[str, Any] = {}
        for key in ("platform", "model", "app_version", "appVersion"):
            value = metadata.get(key)
            if value:
                context[key] = value
        if context:
            entry["context"] = context

        notes = metadata.get("notes")
        if notes:
            entry["notes"] = str(notes)

        _ensure_security_fields(entry, metadata, is_new=True)

        devices[device_key] = entry

        with config_path.open("w", encoding="utf-8") as handle:
            _yaml.dump(raw_data, handle)

        settings.refresh_config_cache()
        result = {k: entry[k] for k in entry}
        return True, result


def ensure_device_security_fields(
    device_id: str,
    *,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Ensure security fields (auth token, resume TTL, TLS pins) exist for *device_id*."""

    device_key = device_id.strip()
    if not device_key:
        raise ValueError("device_id must not be empty")

    config_path = settings._config_path()  # type: ignore[attr-defined]

    with _write_lock:
        with config_path.open("r", encoding="utf-8") as handle:
            raw_data = _yaml.load(handle) or CommentedMap()

        devices = _ensure_commented_map(raw_data.get("mobile_devices"))
        raw_data["mobile_devices"] = devices

        if device_key not in devices:
            raise KeyError(f"Device '{device_key}' not found in config")

        entry = _ensure_commented_map(devices[device_key])
        mutated = _ensure_security_fields(entry, metadata or {})

        if mutated:
            with config_path.open("w", encoding="utf-8") as handle:
                _yaml.dump(raw_data, handle)
            settings.refresh_config_cache()

        return {k: entry[k] for k in entry}


def _ensure_security_fields(
    entry: CommentedMap,
    metadata: Dict[str, Any],
    *,
    is_new: bool = False,
) -> bool:
    """Ensure auth token and related config fields are present."""

    mutated = False

    if not entry.get("auth_token"):
        entry["auth_token"] = secrets.token_urlsafe(32)
        mutated = True

    resume_ttl = metadata.get("session_resume_ttl_seconds")
    if not isinstance(resume_ttl, int) or resume_ttl < 60:
        resume_ttl = entry.get("session_resume_ttl_seconds")
        if not isinstance(resume_ttl, int) or resume_ttl < 60:
            resume_ttl = 300
    if entry.get("session_resume_ttl_seconds") != resume_ttl:
        entry["session_resume_ttl_seconds"] = resume_ttl
        mutated = True

    tls_pins = metadata.get("tls_pins")
    if tls_pins is None:
        tls_pins = entry.get("tls_pins")
    if tls_pins is None:
        tls_pins = []
    if entry.get("tls_pins") != tls_pins:
        entry["tls_pins"] = list(tls_pins)
        mutated = True

    if is_new:
        entry.setdefault("notes", metadata.get("notes"))

    return mutated
