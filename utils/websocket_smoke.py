"""Minimal WebSocket smoke test for the Ringdown ConversationRelay endpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import websockets
from twilio.request_validator import RequestValidator

from app.logging_utils import redact_sensitive_data


@dataclass(slots=True)
class SmokeConfig:
    url: str
    call_sid: str
    prompt: str
    receive_messages: int
    timeout: float


def _json_log(level: str, message: str, **fields: Any) -> None:
    payload = {"severity": level.upper(), "message": message, **fields}
    print(json.dumps(redact_sensitive_data(payload), ensure_ascii=True))


def _compute_signature(full_url: str, params: dict[str, str], auth_token: str) -> str:
    validator = RequestValidator(auth_token)
    return validator.compute_signature(full_url, params)


def _prepare_url(base: str, call_sid: str) -> tuple[str, dict[str, str]]:
    parsed = urlparse(base)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.setdefault("callSid", call_sid)
    final_query = urlencode(params)
    final_url = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/ws",
            parsed.params,
            final_query,
            parsed.fragment,
        )
    )
    return final_url, params


async def _run(config: SmokeConfig) -> int:
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not auth_token:
        _json_log("ERROR", "TWILIO_AUTH_TOKEN missing from environment")
        return 2

    target_url, params = _prepare_url(config.url, config.call_sid)
    signature = _compute_signature(target_url, params, auth_token)

    headers = [
        ("X-Twilio-Signature", signature),
        ("X-Forwarded-Proto", urlparse(target_url).scheme),
    ]

    _json_log(
        "INFO",
        "Connecting to WebSocket",
        url=target_url,
        call_sid=config.call_sid,
        subprotocol="conversationrelay.v1",
    )

    try:
        async with websockets.connect(
            target_url,
            extra_headers=headers,
            subprotocols=["conversationrelay.v1"],
        ) as ws:
            setup_payload = {"type": "setup", "callSid": config.call_sid}
            await ws.send(json.dumps(setup_payload))
            _json_log("INFO", "Sent setup payload", payload=setup_payload)

            prompt_payload = {"type": "prompt", "voicePrompt": config.prompt}
            await ws.send(json.dumps(prompt_payload))
            _json_log("INFO", "Sent prompt payload", payload=prompt_payload)

            for idx in range(config.receive_messages):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=config.timeout)
                except asyncio.TimeoutError:
                    _json_log("WARNING", "Timed out waiting for WebSocket response", index=idx)
                    break
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    _json_log("WARNING", "Non-JSON frame received", index=idx, raw=raw)
                else:
                    _json_log("INFO", "Received frame", index=idx, frame=frame)

    except websockets.exceptions.ConnectionClosedOK as exc:
        _json_log("INFO", "WebSocket closed", code=exc.code, reason=exc.reason)
    except Exception as exc:  # noqa: BLE001
        _json_log("ERROR", "WebSocket smoke test failed", error=str(exc))
        return 1

    _json_log("INFO", "Smoke test complete")
    return 0


def _parse_args() -> SmokeConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="ws://localhost:8000/ws",
        help="Base WebSocket URL to hit (default: %(default)s)",
    )
    parser.add_argument(
        "--call-sid",
        default=None,
        help="Explicit CallSid to send. Defaults to a random value for each run.",
    )
    parser.add_argument(
        "--prompt",
        default="Hello! This is a local smoke test.",
        help="Text payload to send as the first prompt after setup.",
    )
    parser.add_argument(
        "--receive",
        type=int,
        default=5,
        help="Maximum number of frames to read before exiting (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for each response frame (default: %(default)s)",
    )

    args = parser.parse_args()
    call_sid = args.call_sid or f"SMOKE-{secrets.token_hex(4)}"
    return SmokeConfig(
        url=args.url,
        call_sid=call_sid,
        prompt=args.prompt,
        receive_messages=max(args.receive, 1),
        timeout=max(args.timeout, 1.0),
    )


def main() -> int:
    config = _parse_args()
    return asyncio.run(_run(config))


if __name__ == "__main__":
    raise SystemExit(main())
