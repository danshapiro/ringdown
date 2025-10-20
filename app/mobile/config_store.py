"""Helpers for updating mobile device entries in config.yaml."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
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
            entry = devices[device_key]
            if isinstance(entry, CommentedMap):
                result = {k: entry[k] for k in entry}
            else:
                result = dict(entry)
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

        devices[device_key] = entry

        with config_path.open("w", encoding="utf-8") as handle:
            _yaml.dump(raw_data, handle)

        settings.refresh_config_cache()
        result = {k: entry[k] for k in entry}
        return True, result
