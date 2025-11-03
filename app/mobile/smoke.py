"""Smoke test helpers for the local and remote text-streaming mobile pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi.testclient import TestClient
import websockets
from websockets.client import WebSocketClientProtocol


class SmokeTestError(RuntimeError):
    """Raised when the mobile text smoke flow fails."""


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
    events: List[Dict[str, Any]]

    @property
    def response_text(self) -> str:
        """Alias retained for historical API compatibility."""

        return self.assistant_text


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestError(message)


def _assert_message_type(message: Dict[str, Any], expected: str) -> None:
    actual = message.get("type")
    _require(actual == expected, f"Expected message type '{expected}', observed '{actual}'")


def run_smoke_test(
    client: TestClient,
    *,
    device_id: str,
    prompt_text: str = "Automated mobile text smoke check.",
) -> SmokeResult:
    """Exercise session bootstrap + websocket streaming against a local TestClient."""

    handshake = client.post(
        "/v1/mobile/text/session",
        json={"deviceId": device_id},
    )
    _require(handshake.status_code == 200, f"Session handshake failed: {handshake.text}")
    body = handshake.json()

    session_id = body.get("sessionId")
    session_token = body.get("sessionToken")
    resume_token = body.get("resumeToken")
    websocket_path = body.get("websocketPath")
    agent = body.get("agent")

    _require(isinstance(session_id, str) and session_id, "sessionId missing from handshake")
    _require(isinstance(session_token, str) and session_token, "sessionToken missing from handshake")
    _require(isinstance(resume_token, str) and resume_token, "resumeToken missing from handshake")
    _require(isinstance(websocket_path, str) and websocket_path, "websocketPath missing from handshake")
    _require(isinstance(agent, str) and agent, "agent missing from handshake")

    events: List[Dict[str, Any]] = []
    assistant_parts: List[str] = []
    greeting_text: str | None = None

    with client.websocket_connect(
        websocket_path,
        headers={"x-ringdown-session-token": session_token},
    ) as websocket:
        ready = websocket.receive_json()
        events.append(ready)
        _assert_message_type(ready, "ready")
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

            if msg_type in {"ack", "heartbeat"}:
                continue

            if msg_type == "tool_event":
                continue

            if msg_type == "error":
                raise SmokeTestError(f"WebSocket error: {json.dumps(message)}")

            # Any other event types (e.g., additional ready) should not terminate the loop.

    assistant_text = "".join(assistant_parts).strip()
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
    parser = argparse.ArgumentParser(description="Run the mobile text smoke test against a local server.")
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
    agent: Optional[str] = None,
    timeout: float = 30.0,
    verify_resume: bool = True,
) -> SmokeResult:
    """Execute a managed handset smoke test against a deployed service."""

    base = base_url.rstrip("/")
    session_url = f"{base}/v1/mobile/text/session"
    payload: Dict[str, Any] = {"deviceId": device_id}
    if agent:
        payload["agent"] = agent

    http_timeout = httpx.Timeout(timeout)
    auth_token: str | None = None
    async with httpx.AsyncClient(timeout=http_timeout) as client:
        response = await client.post(session_url, json=payload)

        if response.status_code != 200:
            detail: str | None = None
            try:
                detail_json = response.json()
                detail = json.dumps(detail_json)
                code = detail_json.get("detail", {}).get("code") if isinstance(detail_json, dict) else None
            except Exception:  # noqa: BLE001
                detail_json = None
            if detail_json and code == "device_not_approved":
                raise SmokeTestError(
                    f"Device {device_id} pending approval; update config.yaml or run approve_new_phone.py."
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
        auth_token = body.get("authToken")

    _require(isinstance(session_id, str) and session_id, "sessionId missing from handshake")
    _require(isinstance(session_token, str) and session_token, "sessionToken missing from handshake")
    _require(isinstance(resume_token, str) and resume_token, "resumeToken missing from handshake")
    _require(isinstance(websocket_path, str) and websocket_path, "websocketPath missing from handshake")
    _require(isinstance(resolved_agent, str) and resolved_agent, "agent missing from handshake")

    ws_url = _build_websocket_url(base, websocket_path)
    events: List[Dict[str, Any]] = []
    assistant_parts: List[str] = []
    greeting_text: str | None = None

    async with _open_websocket(ws_url, session_token, timeout) as websocket:
        ready_raw = await asyncio.wait_for(websocket.recv(), timeout)
        ready = json.loads(ready_raw)
        events.append(ready)
        _assert_message_type(ready, "ready")
        if isinstance(ready.get("greeting"), str):
            greeting_text = ready["greeting"].strip() or None

        await websocket.send(json.dumps({"type": "user_message", "text": prompt_text, "final": True}))

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
                raise SmokeTestError(f"WebSocket error: {json.dumps(message)}")

    assistant_text = "".join(assistant_parts).strip()
    _require(bool(assistant_text), "Assistant produced no response tokens.")

    if verify_resume:
        resume_payload = {
            "deviceId": device_id,
            "resumeToken": resume_token,
        }
        if isinstance(auth_token, str) and auth_token.strip():
            resume_payload["authToken"] = auth_token

        async with httpx.AsyncClient(timeout=http_timeout) as client:
            resume_resp = await client.post(session_url, json=resume_payload)

        if resume_resp.status_code != 200:
            try:
                detail = resume_resp.json()
            except Exception:  # noqa: BLE001
                detail = resume_resp.text
            raise SmokeTestError(
                f"Resume handshake failed ({resume_resp.status_code}) for device {device_id}: {detail}"
            )
        resume_body = resume_resp.json()
        _require(
            isinstance(resume_body.get("sessionToken"), str),
            "Resume handshake missing sessionToken",
        )
        _require(
            isinstance(resume_body.get("resumeToken"), str),
            "Resume handshake missing resumeToken",
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


@asynccontextmanager
async def _open_websocket(url: str, session_token: str, timeout: float) -> WebSocketClientProtocol:
    headers = {"x-ringdown-session-token": session_token}
    try:
        ws = await websockets.connect(
            url,
            additional_headers=headers,
            max_size=1_000_000,
            open_timeout=timeout,
            close_timeout=timeout,
            ping_interval=None,
        )
    except TypeError:
        ws = await websockets.connect(
            url,
            extra_headers=headers,
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
