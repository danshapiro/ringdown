"""Smoke test helpers for the local text-streaming mobile pipeline."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from fastapi.testclient import TestClient


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
