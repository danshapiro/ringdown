from __future__ import annotations

import json
import os
from typing import Iterable, Sequence

import httpx
import pytest
import websockets

DEFAULT_BACKEND_URL = "https://danbot-twilio-bkvo7niota-uw.a.run.app/"
DEFAULT_DEVICE_ID = "instrumentation-device"


async def _register_device(base_url: str, device_id: str) -> None:
    """Ensure *device_id* is approved on the backend."""

    payload = {
        "deviceId": device_id,
        "label": "automation",
        "platform": "python-test",
        "model": "python-client",
        "appVersion": "test",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(f"{base_url}v1/mobile/devices/register", json=payload)
        response.raise_for_status()
        result = response.json()

    status = result.get("status")
    if status != "APPROVED":
        raise RuntimeError(f"Device {device_id} not approved (status={status})")


def _flatten_urls(entry: dict[str, object]) -> Sequence[str]:
    urls = entry.get("urls")
    if isinstance(urls, str):
        return [urls]
    if isinstance(urls, Iterable):
        return [str(url) for url in urls]
    return []


def _resolve_backend_and_device() -> tuple[str, str]:
    backend = os.environ.get("RINGDOWN_BACKEND_URL", DEFAULT_BACKEND_URL).strip()
    if not backend.endswith("/"):
        backend = f"{backend}/"

    device_id = os.environ.get("RINGDOWN_DEVICE_ID", DEFAULT_DEVICE_ID).strip()
    if not device_id:
        raise RuntimeError("RINGDOWN_DEVICE_ID must not be empty")

    return backend, device_id


@pytest.mark.asyncio
@pytest.mark.live
async def test_mobile_voice_turn_servers_exposed() -> None:
    """Backend should provide TURN credentials so clients can negotiate audio."""

    base_url, device_id = _resolve_backend_and_device()
    await _register_device(base_url, device_id)

    signaling_url = base_url.replace("https://", "wss://") + "ws/mobile/voice"
    signaling_url = f"{signaling_url}?device_id={device_id}"

    async with websockets.connect(signaling_url, ping_interval=None) as websocket:
        payload = json.loads(await websocket.recv())
        assert payload.get("type") == "iceServers", f"Unexpected message: {payload}"

        entries = payload.get("iceServers") or []
        turn_urls = []
        for entry in entries:
            if isinstance(entry, dict):
                turn_urls.extend(
                    url
                    for url in _flatten_urls(entry)
                    if url.lower().startswith("turn:")
                )

        assert turn_urls, f"No TURN servers provided: {entries}"
        await websocket.send(json.dumps({"type": "bye"}))
