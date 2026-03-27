"""Smoke test helpers for the local and remote text-streaming mobile pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict, dataclass
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse

import httpx
from fastapi.testclient import TestClient
from websockets.asyncio.client import ClientConnection, connect

from app import settings


class SmokeTestError(RuntimeError):
    """Raised when the mobile text smoke flow fails."""

    def __init__(
        self,
        message: str,
        *,
        events: list[dict[str, Any]] | None = None,
        logs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.events: list[dict[str, Any]] = list(events or [])
        self.logs: list[dict[str, Any]] = list(logs or [])

        detail_parts: list[str] = []
        if self.events:
            try:
                detail_parts.append(f"events_tail={json.dumps(self.events[-3:], default=str)}")
            except Exception:  # pragma: no cover - defensive
                detail_parts.append(f"events_tail={self.events[-3:]!r}")
        if self.logs:
            try:
                detail_parts.append(f"logs_tail={json.dumps(self.logs[-3:], default=str)}")
            except Exception:  # pragma: no cover - defensive
                detail_parts.append(f"logs_tail={self.logs[-3:]!r}")

        suffix = f" ({'; '.join(detail_parts)})" if detail_parts else ""
        super().__init__(message + suffix)


@dataclass(slots=True)
class SmokeResult:
    """Structured result describing a mobile text smoke-test run."""

    success: bool
    session_id: str
    agent: str
    greeting: str | None
    assistant_text: str
    resume_token: str
    websocket_path: str
    events: list[dict[str, Any]]

    @property
    def response_text(self) -> str:
        """Alias retained for historical API compatibility."""

        return self.assistant_text


def _require(
    condition: bool,
    message: str,
    *,
    events: list[dict[str, Any]] | None = None,
    logs: list[dict[str, Any]] | None = None,
) -> None:
    if not condition:
        raise SmokeTestError(message, events=events, logs=logs)


def _assert_message_type(
    message: dict[str, Any],
    expected: str,
    *,
    events: list[dict[str, Any]],
    logs: list[dict[str, Any]],
) -> None:
    actual = message.get("type")
    _require(
        actual == expected,
        f"Expected message type '{expected}', observed '{actual}'",
        events=events,
        logs=logs,
    )


def _capture_mobile_text_logs(buffer: list[dict[str, Any]]):
    """Return a context manager that records structured logs emitted by the mobile text stack."""

    try:
        from app.api import mobile_text as mobile_text_module
    except Exception:  # pragma: no cover - defensive fallback when module missing
        return nullcontext()

    original = getattr(mobile_text_module, "_structured_log", None)
    if original is None:
        return nullcontext()

    def _combined(level: str, event: str, **fields: Any) -> None:
        record = {"level": level, "event": event, **fields}
        buffer.append(record)
        original(level, event, **fields)

    return patch("app.api.mobile_text._structured_log", new=_combined)


def run_smoke_test(
    client: TestClient,
    *,
    device_id: str,
    prompt_text: str = "Automated mobile text smoke check.",
) -> SmokeResult:
    """Exercise session bootstrap + websocket streaming against a local TestClient."""

    events: list[dict[str, Any]] = []
    log_records: list[dict[str, Any]] = []
    configured_auth_token = _get_configured_auth_token(device_id)

    with _capture_mobile_text_logs(log_records):
        handshake_payload: dict[str, Any] = {"deviceId": device_id}
        if configured_auth_token:
            handshake_payload["authToken"] = configured_auth_token
        handshake = client.post(
            "/v1/mobile/text/session",
            json=handshake_payload,
        )
        _require(
            handshake.status_code == 200,
            f"Session handshake failed: {handshake.text}",
            events=events,
            logs=log_records,
        )
        body = handshake.json()

        session_id = body.get("sessionId")
        session_token = body.get("sessionToken")
        resume_token = body.get("resumeToken")
        websocket_path = body.get("websocketPath")
        agent = body.get("agent")
        auth_token = body.get("authToken") or configured_auth_token

        _require(
            isinstance(session_id, str) and session_id,
            "sessionId missing from handshake",
            events=events,
            logs=log_records,
        )
        _require(
            isinstance(session_token, str) and session_token,
            "sessionToken missing from handshake",
            events=events,
            logs=log_records,
        )
        _require(
            isinstance(resume_token, str) and resume_token,
            "resumeToken missing from handshake",
            events=events,
            logs=log_records,
        )
        _require(
            isinstance(websocket_path, str) and websocket_path,
            "websocketPath missing from handshake",
            events=events,
            logs=log_records,
        )
        _require(
            isinstance(agent, str) and agent,
            "agent missing from handshake",
            events=events,
            logs=log_records,
        )

        assistant_parts: list[str] = []
        greeting_text: str | None = None

        with client.websocket_connect(
            websocket_path,
            headers={"x-ringdown-session-token": session_token},
        ) as websocket:
            ready = websocket.receive_json()
            events.append(ready)
            _assert_message_type(ready, "ready", events=events, logs=log_records)
            if isinstance(ready.get("greeting"), str):
                greeting_text = ready["greeting"].strip() or None

            websocket.send_json({"type": "user_message", "text": prompt_text, "final": True})

            while True:
                message = websocket.receive_json()
                events.append(message)
                msg_type = message.get("type")

                if msg_type == "assistant_token":
                    token = message.get("token")
                    if isinstance(token, str):
                        assistant_parts.append(token)
                    message_type = message.get("messageType")
                    if message_type == "greeting" and isinstance(token, str):
                        greeting_text = token.strip() or greeting_text
                        continue
                    if message.get("final") is True:
                        break
                    continue

                if msg_type in {"ack", "heartbeat", "tool_event"}:
                    continue

                if msg_type == "error":
                    raise SmokeTestError(
                        f"WebSocket error: {json.dumps(message)}",
                        events=list(events),
                        logs=list(log_records),
                    )

                # Any other event types (e.g., additional ready) should not terminate the loop.

    assistant_text = "".join(assistant_parts).strip()

    resume_payload: dict[str, Any] = {
        "deviceId": device_id,
        "resumeToken": resume_token,
    }
    if isinstance(auth_token, str) and auth_token.strip():
        resume_payload["authToken"] = auth_token

    resume_resp = client.post(
        "/v1/mobile/text/session",
        json=resume_payload,
    )
    _require(
        resume_resp.status_code == 200,
        f"Resume handshake failed ({resume_resp.status_code}): {resume_resp.text}",
        events=events,
        logs=log_records,
    )
    resume_body = resume_resp.json()
    new_session_token = resume_body.get("sessionToken")
    _require(
        isinstance(new_session_token, str) and new_session_token,
        "Resume handshake missing sessionToken",
        events=events,
        logs=log_records,
    )
    new_resume_token = resume_body.get("resumeToken")
    _require(
        isinstance(new_resume_token, str) and new_resume_token,
        "Resume handshake missing resumeToken",
        events=events,
        logs=log_records,
    )
    resume_token = new_resume_token
    with client.websocket_connect(
        websocket_path,
        headers={"x-ringdown-session-token": new_session_token},
    ) as resume_ws:
        resume_ready = resume_ws.receive_json()
        events.append(resume_ready)
        _assert_message_type(resume_ready, "ready", events=events, logs=log_records)
        resume_ws.send_json({"type": "heartbeat"})
        heartbeat = resume_ws.receive_json()
        events.append(heartbeat)
        _assert_message_type(heartbeat, "heartbeat", events=events, logs=log_records)

    return SmokeResult(
        success=bool(assistant_text),
        session_id=session_id,
        agent=agent,
        greeting=greeting_text,
        assistant_text=assistant_text,
        resume_token=resume_token,
        websocket_path=websocket_path,
        events=events,
    )


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Run the mobile text smoke test against a local server."
    )
    parser.add_argument("--device-id", required=True, help="Registered device identifier")
    parser.add_argument(
        "--prompt",
        default="Automated mobile text smoke check.",
        help="Prompt text to send over the stream.",
    )
    args = parser.parse_args()

    from fastapi.testclient import TestClient  # Imported lazily for CLI usage

    from app.main import app

    client = TestClient(app)
    result = run_smoke_test(client, device_id=args.device_id, prompt_text=args.prompt)
    print(json.dumps(asdict(result), indent=2, sort_keys=True))


__all__ = ["SmokeResult", "SmokeTestError", "run_smoke_test"]


if __name__ == "__main__":  # pragma: no cover - manual invocation
    _cli()


async def run_remote_smoke(
    *,
    base_url: str,
    device_id: str,
    prompt_text: str,
    agent: str | None = None,
    auth_token: str | None = None,
    timeout: float = 30.0,
    verify_resume: bool = True,
) -> SmokeResult:
    """Execute a mobile text smoke test against a deployed service."""

    base = base_url.rstrip("/")
    session_url = f"{base}/v1/mobile/text/session"
    payload: dict[str, Any] = {"deviceId": device_id}
    resolved_auth_token = (
        auth_token or os.environ.get("LIVE_TEST_MOBILE_AUTH_TOKEN") or ""
    ).strip()
    if resolved_auth_token:
        payload["authToken"] = resolved_auth_token
    if agent:
        payload["agent"] = agent

    http_timeout = httpx.Timeout(timeout)
    async with httpx.AsyncClient(timeout=http_timeout) as client:
        response = await client.post(session_url, json=payload)

        if response.status_code != 200:
            detail: str | None = None
            try:
                detail_json = response.json()
                detail = json.dumps(detail_json)
                code = (
                    detail_json.get("detail", {}).get("code")
                    if isinstance(detail_json, dict)
                    else None
                )
            except Exception:  # noqa: BLE001
                detail_json = None
            if detail_json and code == "device_not_approved":
                raise SmokeTestError(
                    f"Device {device_id} pending approval; update config.yaml "
                    "or run approve_new_phone.py."
                )
            raise SmokeTestError(
                f"Session handshake failed ({response.status_code}): {detail or response.text}"
            )

        body = response.json()
        session_id = body.get("sessionId")
        session_token = body.get("sessionToken")
        resume_token = body.get("resumeToken")
        websocket_path = body.get("websocketPath")
        resolved_agent = body.get("agent") or agent
        resolved_auth_token = body.get("authToken") or resolved_auth_token

    events: list[dict[str, Any]] = []

    _require(
        isinstance(session_id, str) and session_id,
        "sessionId missing from handshake",
        events=events,
    )
    _require(
        isinstance(session_token, str) and session_token,
        "sessionToken missing from handshake",
        events=events,
    )
    _require(
        isinstance(resume_token, str) and resume_token,
        "resumeToken missing from handshake",
        events=events,
    )
    _require(
        isinstance(websocket_path, str) and websocket_path,
        "websocketPath missing from handshake",
        events=events,
    )
    _require(
        isinstance(resolved_agent, str) and resolved_agent,
        "agent missing from handshake",
        events=events,
    )

    ws_url = _build_websocket_url(base, websocket_path)
    assistant_parts: list[str] = []
    greeting_text: str | None = None

    async with _open_websocket(ws_url, session_token, timeout) as websocket:
        ready_raw = await asyncio.wait_for(websocket.recv(), timeout)
        ready = json.loads(ready_raw)
        events.append(ready)
        _assert_message_type(ready, "ready", events=events, logs=[])
        if isinstance(ready.get("greeting"), str):
            greeting_text = ready["greeting"].strip() or None

        await websocket.send(
            json.dumps({"type": "user_message", "text": prompt_text, "final": True})
        )

        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout)
            message = json.loads(raw)
            events.append(message)
            msg_type = message.get("type")

            if msg_type == "assistant_token":
                token = message.get("token")
                if isinstance(token, str):
                    assistant_parts.append(token)
                message_type = message.get("messageType")
                if message_type == "greeting" and isinstance(token, str):
                    greeting_text = token.strip() or greeting_text
                    continue
                if message.get("final") is True:
                    break
                continue

            if msg_type in {"ack", "heartbeat", "tool_event"}:
                continue

            if msg_type == "error":
                raise SmokeTestError(
                    f"WebSocket error: {json.dumps(message)}",
                    events=list(events),
                )

    assistant_text = "".join(assistant_parts).strip()
    _require(bool(assistant_text), "Assistant produced no response tokens.", events=events)

    if verify_resume:
        resume_payload = {
            "deviceId": device_id,
            "resumeToken": resume_token,
        }
        if resolved_auth_token:
            resume_payload["authToken"] = resolved_auth_token

        async with httpx.AsyncClient(timeout=http_timeout) as client:
            resume_resp = await client.post(session_url, json=resume_payload)

        if resume_resp.status_code != 200:
            try:
                detail = resume_resp.json()
            except Exception:  # noqa: BLE001
                detail = resume_resp.text
            raise SmokeTestError(
                "Resume handshake failed "
                f"({resume_resp.status_code}) for device {device_id}: {detail}",
                events=list(events),
            )
        resume_body = resume_resp.json()
        _require(
            isinstance(resume_body.get("sessionToken"), str),
            "Resume handshake missing sessionToken",
            events=events,
        )
        _require(
            isinstance(resume_body.get("resumeToken"), str),
            "Resume handshake missing resumeToken",
            events=events,
        )

    return SmokeResult(
        success=True,
        session_id=session_id,
        agent=resolved_agent,
        greeting=greeting_text,
        assistant_text=assistant_text,
        resume_token=resume_token,
        websocket_path=websocket_path,
        events=events,
    )


def _build_websocket_url(base_url: str, websocket_path: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = websocket_path if websocket_path.startswith("/") else f"/{websocket_path}"
    return f"{scheme}://{parsed.netloc}{path}"


def _get_configured_auth_token(device_id: str) -> str | None:
    try:
        raw_device = settings.get_mobile_device(device_id) or {}
    except Exception:  # pragma: no cover - local smoke callers may not have device config
        return None

    if not isinstance(raw_device, dict):
        return None

    candidate = raw_device.get("auth_token") or raw_device.get("authToken")
    if not isinstance(candidate, str):
        return None

    token = candidate.strip()
    return token or None


@asynccontextmanager
async def _open_websocket(url: str, session_token: str, timeout: float) -> ClientConnection:
    headers = {"x-ringdown-session-token": session_token}
    ws = await connect(
        url,
        additional_headers=headers,
        max_size=1_000_000,
        open_timeout=timeout,
        close_timeout=timeout,
        ping_interval=None,
    )
    try:
        yield ws
    finally:
        await ws.close()


__all__ = ["SmokeResult", "SmokeTestError", "run_smoke_test", "run_remote_smoke"]
