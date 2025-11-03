from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.mobile.smoke import SmokeResult, SmokeTestError, run_smoke_test
from app.mobile.text_session_store import TextSessionStore, TextSessionState


client = TestClient(app)


def _text_state() -> TextSessionState:
    now = datetime.now(timezone.utc)
    return TextSessionState(
        session_id="session-abc",
        device_id="device-123",
        agent_name="Agent Alpha",
        agent_config={
            "model": "gpt-5",
            "prompt": "You are helpful.",
            "welcome_greeting": "Hi there!",
        },
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        resume_expires_at=now + timedelta(minutes=5),
        resume_token="resume-abc",
        session_ttl_seconds=900,
        resume_ttl_seconds=300,
        heartbeat_interval_seconds=12,
        heartbeat_timeout_seconds=30,
        tls_pins=[],
    )


def _patch_mobile_layers(store: TextSessionStore) -> contextlib.ExitStack:
    device_cfg = {
        "enabled": True,
        "agent": "Agent Alpha",
        "auth_token": "secret-token",
        "session_resume_ttl_seconds": 450,
        "tls_pins": ["pin-device"],
    }
    text_cfg = {
        "websocket_path": "/v1/mobile/text/session",
        "session_ttl_seconds": 900,
        "resume_ttl_seconds": 300,
        "heartbeat_interval_seconds": 12,
        "heartbeat_timeout_seconds": 30,
        "tls_pins": ["pin-global"],
    }
    agent_cfg = {
        "model": "gpt-5",
        "prompt": "You are helpful.",
        "welcome_greeting": "Hi there!",
    }

    stack = contextlib.ExitStack()
    stack.enter_context(patch("app.api.mobile.settings.get_mobile_device", return_value=device_cfg))
    stack.enter_context(patch("app.api.mobile.settings.get_mobile_text_config", return_value=text_cfg))
    stack.enter_context(patch("app.api.mobile.settings.get_agent_config", return_value=agent_cfg))
    stack.enter_context(patch("app.api.mobile.get_text_session_store", return_value=store))
    stack.enter_context(patch("app.api.mobile_text.get_text_session_store", return_value=store))
    stack.enter_context(patch("app.api.mobile_text.METRIC_MESSAGES"))
    stack.enter_context(patch("app.api.mobile_text.litellm.token_counter", return_value=1))
    stack.enter_context(patch("app.api.mobile_text.log_turn"))
    return stack


async def _fake_stream_response(
    user_text: str,
    agent_cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    **kwargs: Any,
):
    yield "assistant reply"


def test_smoke_success() -> None:
    store = TextSessionStore()
    state = _text_state()

    patches = _patch_mobile_layers(store)
    with patches:
        store.create_session = AsyncMock(return_value=(state, "session-token"))  # type: ignore[assignment]
        store.consume_session_token = AsyncMock(return_value=state)  # type: ignore[assignment]
        with patch("app.api.mobile_text.stream_response", _fake_stream_response):
            result = run_smoke_test(client, device_id="device-123", prompt_text="hello")

    assert isinstance(result, SmokeResult)
    assert result.success is True
    assert result.session_id == "session-abc"
    assert result.agent == "Agent Alpha"
    assert "assistant reply" in result.assistant_text
    assert result.resume_token == "resume-abc"
    assert result.websocket_path == "/v1/mobile/text/session"
    assert any(evt.get("type") == "ready" for evt in result.events)


def test_smoke_raises_on_error_event() -> None:
    store = TextSessionStore()
    state = _text_state()

    patches = _patch_mobile_layers(store)
    with patches:
        store.create_session = AsyncMock(return_value=(state, "session-token"))  # type: ignore[assignment]
        store.consume_session_token = AsyncMock(return_value=state)  # type: ignore[assignment]

        async def failing_stream(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")
            yield  # pragma: no cover

        with patch("app.api.mobile_text.stream_response", failing_stream):
            try:
                run_smoke_test(client, device_id="device-123", prompt_text="hello")
            except SmokeTestError as exc:
                assert "WebSocket error" in str(exc)
            else:
                raise AssertionError("Expected SmokeTestError to be raised")
