from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mobile.smoke import SmokeResult, SmokeTestError, run_smoke_test
from app.mobile.text_session_store import TextSessionStore

client = TestClient(app)


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
        "temperature": 0.1,
        "max_tokens": 128,
        "max_history": 16,
        "tools": [],
    }

    stack = contextlib.ExitStack()
    stack.enter_context(patch("app.api.mobile.settings.get_mobile_device", return_value=device_cfg))
    stack.enter_context(
        patch("app.api.mobile.settings.get_mobile_text_config", return_value=text_cfg)
    )
    stack.enter_context(patch("app.api.mobile.settings.get_agent_config", return_value=agent_cfg))
    stack.enter_context(patch("app.api.mobile.get_text_session_store", return_value=store))
    stack.enter_context(patch("app.api.mobile_text.get_text_session_store", return_value=store))
    stack.enter_context(patch("app.api.mobile_text.METRIC_MESSAGES"))
    stack.enter_context(patch("app.api.mobile_text.litellm.token_counter", return_value=1))
    stack.enter_context(patch("app.api.mobile_text.log_turn"))
    return stack


def test_smoke_success() -> None:
    store = TextSessionStore()
    stack = _patch_mobile_layers(store)

    class DummyThinkingAudio:
        def __init__(self, source: str) -> None:
            self.source = source

        def start_payload(self) -> None:
            return None

        def stop(self) -> None:
            return None

    async def fake_acompletion(*args: Any, **kwargs: Any) -> Any:
        class _FakeResponse:
            def __aiter__(self_inner):
                async def _generator():
                    yield SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta={"content": "assistant reply"}, finish_reason=None
                            )
                        ]
                    )
                    yield SimpleNamespace(choices=[SimpleNamespace(delta={}, finish_reason="stop")])

                return _generator()

        return _FakeResponse()

    stack.enter_context(patch("app.chat.ThinkingAudioController", DummyThinkingAudio))
    stack.enter_context(patch("app.chat.acompletion", fake_acompletion))
    stack.enter_context(patch("app.chat.tf.get_tools_for_agent", return_value=[]))
    stack.enter_context(patch("app.chat.tf.set_agent_context"))
    stack.enter_context(patch("app.chat.tf.set_call_context"))
    stack.enter_context(patch("app.chat.tf.get_async_result", return_value=None))

    with stack:
        result = run_smoke_test(client, device_id="device-123", prompt_text="hello")

    assert isinstance(result, SmokeResult)
    assert result.success is True
    assert isinstance(result.session_id, str) and result.session_id
    assert result.agent == "Agent Alpha"
    assert result.greeting == "Hi there!"
    assert "assistant reply" in result.assistant_text
    assert isinstance(result.resume_token, str) and result.resume_token
    assert result.websocket_path == "/v1/mobile/text/session"
    assert any(evt.get("type") == "ready" for evt in result.events)


def test_smoke_raises_on_error_event() -> None:
    store = TextSessionStore()
    stack = _patch_mobile_layers(store)
    stack.enter_context(patch("app.chat.tf.get_tools_for_agent", return_value=[]))
    stack.enter_context(patch("app.chat.tf.set_agent_context"))
    stack.enter_context(patch("app.chat.tf.set_call_context"))
    stack.enter_context(patch("app.chat.tf.get_async_result", return_value=None))

    async def failing_stream(*args: Any, **kwargs: Any):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    stack.enter_context(patch("app.api.mobile_text.stream_response", failing_stream))

    with stack, pytest.raises(SmokeTestError) as excinfo:
        run_smoke_test(client, device_id="device-123", prompt_text="hello")

    logs = excinfo.value.logs
    events = excinfo.value.events
    stream_log = next(
        (entry for entry in logs if entry.get("event") == "mobile_text_session.stream_failure"),
        {},
    )
    assert stream_log.get("error") == "boom"
    assert stream_log.get("exception_type") == "RuntimeError"
    assert stream_log.get("exception_repr") == "RuntimeError('boom')"

    assert events and events[-1].get("type") == "error"
    assert events[-1].get("detail") == "boom"
    assert events[-1].get("exceptionType") == "RuntimeError"
    assert events[-1].get("exceptionRepr") == "RuntimeError('boom')"
