from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

import app.mobile.smoke as smoke_module
from app import settings as settings_module
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


def test_resolve_remote_smoke_auth_token_reads_local_backend_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "mobile_devices:\n  instrumentation-device:\n    auth_token: discovered-token\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("LIVE_TEST_MOBILE_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(smoke_module, "_get_configured_auth_token", lambda _device_id: None)
    monkeypatch.setattr(
        smoke_module,
        "_discover_local_backend_config_path",
        lambda _base_url: config_path,
    )

    token = smoke_module.resolve_remote_smoke_auth_token(
        base_url="http://127.0.0.1:8000",
        device_id="instrumentation-device",
    )

    assert token == "discovered-token"


def test_resolve_remote_smoke_auth_token_prefers_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVE_TEST_MOBILE_AUTH_TOKEN", "env-token")
    monkeypatch.setattr(
        smoke_module,
        "_get_configured_auth_token",
        lambda _device_id: pytest.fail("config lookup should not run"),
    )

    token = smoke_module.resolve_remote_smoke_auth_token(
        base_url="http://127.0.0.1:8000",
        device_id="instrumentation-device",
        auth_token="explicit-token",
    )

    assert token == "explicit-token"


def test_prepare_local_smoke_device_creates_approved_entry(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
defaults:
  timezone: America/Los_Angeles
  model: gpt-4o-mini
  max_tokens: 1024
  language: en
  bot_name: Ringdown
  default_email: user@example.com
  project_name: ringdown
  calendar_user_name: Dan
  welcome_greeting: Hello
  transcription_provider: openai
  speech_model: gpt-4o-mini-transcribe
agents:
  unknown-caller:
    bot_name: Unknown Caller
mobile_devices: {}
mobile_text:
  websocket_path: /v1/mobile/text/session
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("RINGDOWN_CONFIG_PATH", str(config_path))
    settings_module.refresh_config_cache()

    token = smoke_module.prepare_local_smoke_device("instrumentation-device")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    entry = data["mobile_devices"]["instrumentation-device"]

    assert entry["enabled"] is True
    assert entry["agent"] == "unknown-caller"
    assert isinstance(token, str) and token == entry["auth_token"]

    settings_module.refresh_config_cache()
