"""Managed A/V smoke test helpers for the Daily Pipecat Cloud pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict

import httpx
from fastapi.testclient import TestClient


class SmokeTestError(RuntimeError):
    """Raised when the managed A/V smoke flow fails."""


@dataclass(slots=True)
class SmokeResult:
    """Structured result describing the managed A/V smoke test outcome."""

    success: bool
    session_id: str
    pipeline_session_id: str | None
    agent: str
    greeting: str | None
    response_text: str
    hold_text: str | None
    metadata: Dict[str, Any]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestError(message)


def run_smoke_test(
    client: TestClient,
    *,
    device_id: str,
    prompt_text: str = "Automated managed A/V smoke check.",
) -> SmokeResult:
    """Exercise managed session bootstrap + completion against a local TestClient."""

    start_response = client.post("/v1/mobile/voice/session", json={"deviceId": device_id})
    _require(start_response.status_code == 200, f"Session start failed: {start_response.text}")
    start_body = start_response.json()

    session_id = start_body.get("sessionId")
    agent = start_body.get("agent")
    greeting = start_body.get("greeting")
    pipeline_session_id = start_body.get("pipelineSessionId")
    metadata = start_body.get("metadata") or {}

    _require(isinstance(session_id, str) and session_id, "sessionId missing from response")
    _require(isinstance(agent, str) and agent, "agent missing from response")

    completion_payload = {
        "sessionId": session_id,
        "text": prompt_text,
        "final": True,
        "metadata": {"source": "smoke_test"},
    }
    completion_response = client.post("/v1/mobile/managed-av/completions", json=completion_payload)
    _require(
        completion_response.status_code == 200,
        f"Managed completion failed: {completion_response.text}",
    )
    completion_body = completion_response.json()

    response_text = completion_body.get("responseText", "")
    hold_text = completion_body.get("holdText")

    client.delete(f"/v1/mobile/managed-av/sessions/{session_id}")

    return SmokeResult(
        success=bool(response_text),
        session_id=session_id,
        pipeline_session_id=pipeline_session_id,
        agent=agent,
        greeting=greeting,
        response_text=response_text,
        hold_text=hold_text,
        metadata=metadata if isinstance(metadata, dict) else {},
    )


async def run_remote_smoke(
    *,
    base_url: str,
    device_id: str,
    prompt_text: str = "Automated managed A/V smoke check.",
    timeout: float = 20.0,
) -> SmokeResult:
    """Execute the managed A/V smoke test against a deployed backend."""

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as http:
        start_response = await http.post("/v1/mobile/voice/session", json={"deviceId": device_id})
    _require(start_response.status_code == 200, f"Session start failed: {start_response.text}")
    start_body = start_response.json()

    session_id = start_body.get("sessionId")
    agent = start_body.get("agent")
    greeting = start_body.get("greeting")
    pipeline_session_id = start_body.get("pipelineSessionId")
    metadata = start_body.get("metadata") or {}

    _require(isinstance(session_id, str) and session_id, "sessionId missing from response")
    _require(isinstance(agent, str) and agent, "agent missing from response")

    completion_payload = {
        "sessionId": session_id,
        "text": prompt_text,
        "final": True,
        "metadata": {"source": "smoke_test"},
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as http:
        completion_response = await http.post("/v1/mobile/managed-av/completions", json=completion_payload)
    _require(
        completion_response.status_code == 200,
        f"Managed completion failed: {completion_response.text}",
    )
    completion_body = completion_response.json()

    response_text = completion_body.get("responseText", "")
    hold_text = completion_body.get("holdText")

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as http:
        await http.delete(f"/v1/mobile/managed-av/sessions/{session_id}")

    return SmokeResult(
        success=bool(response_text),
        session_id=session_id,
        pipeline_session_id=pipeline_session_id,
        agent=agent,
        greeting=greeting,
        response_text=response_text,
        hold_text=hold_text,
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Run the managed A/V mobile smoke test.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Backend base URL")
    parser.add_argument("--device-id", required=True, help="Registered device identifier")
    parser.add_argument(
        "--prompt",
        default="Automated managed A/V smoke check.",
        help="Prompt text to feed to the managed pipeline",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="Network timeout seconds")
    args = parser.parse_args()

    result = asyncio.run(
        run_remote_smoke(
            base_url=args.base_url,
            device_id=args.device_id,
            prompt_text=args.prompt,
            timeout=args.timeout,
        )
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))


__all__ = ["SmokeResult", "SmokeTestError", "run_smoke_test", "run_remote_smoke"]


if __name__ == "__main__":  # pragma: no cover - manual invocation
    _cli()
