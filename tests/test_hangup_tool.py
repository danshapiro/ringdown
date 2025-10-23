"""Tests for the hang_up tool."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tool_framework import TOOL_REGISTRY, execute_tool
from app.tools import hang_up
from app.chat import stream_response


def _stub_env(account_sid: str = "AC123", auth_token: str = "auth") -> SimpleNamespace:
    return SimpleNamespace(
        twilio_account_sid=account_sid,
        twilio_auth_token=auth_token,
    )


def test_hang_up_tool_registered():
    assert "hang_up" in TOOL_REGISTRY


def test_hang_up_invokes_twilio(monkeypatch):
    called = {}

    def fake_complete(account_sid: str, auth_token: str, call_sid: str) -> None:
        called.update({
            "account_sid": account_sid,
            "auth_token": auth_token,
            "call_sid": call_sid,
        })

    monkeypatch.setattr(hang_up, "_complete_call_via_twilio", fake_complete)
    monkeypatch.setattr(hang_up, "get_env", lambda: _stub_env())
    hang_up.set_call_context({"call_sid": "CA123456789"})

    result = execute_tool("hang_up", {"confirm": True})

    assert result["action"] == "hangup_call"
    assert result["status"] == "success"
    assert called["call_sid"] == "CA123456789"


def test_hang_up_missing_call_sid(monkeypatch):
    monkeypatch.setattr(hang_up, "_complete_call_via_twilio", lambda *args: None)
    monkeypatch.setattr(hang_up, "get_env", lambda: _stub_env())
    hang_up.set_call_context(None)

    result = execute_tool("hang_up", {"confirm": True})

    assert result["status"] == "missing_call_sid"


def test_hang_up_twilio_failure(monkeypatch):
    def failing_complete(*_args):
        raise RuntimeError("twilio down")

    monkeypatch.setattr(hang_up, "_complete_call_via_twilio", failing_complete)
    monkeypatch.setattr(hang_up, "get_env", lambda: _stub_env())
    hang_up.set_call_context({"call_sid": "CAFAIL"})

    result = execute_tool("hang_up", {"confirm": True})

    assert result["status"] == "twilio_failure"


@pytest.mark.asyncio
async def test_stream_response_emits_hangup_marker(monkeypatch):
    monkeypatch.setattr(hang_up, "_complete_call_via_twilio", lambda *args, **kwargs: None)
    monkeypatch.setattr(hang_up, "get_env", lambda: _stub_env())

    agent = {
        "model": "gpt-5",
        "temperature": 1.0,
        "max_tokens": 128,
        "max_history": 50,
        "prompt": "You are a helpful assistant.",
        "tools": ["hang_up"],
        "max_tool_iterations": 1,
    }

    messages = [
        {"role": "system", "content": agent["prompt"]},
        {"role": "user", "content": "Please hang up."},
    ]

    mock_response = AsyncMock()

    async def async_iterator():
        yield MagicMock(
            choices=[
                MagicMock(
                    delta={
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "hang_up", "arguments": "{}"},
                            }
                        ]
                    },
                    finish_reason=None,
                )
            ]
        )
        yield MagicMock(choices=[MagicMock(delta={}, finish_reason="tool_calls")])

    mock_response.__aiter__.return_value = async_iterator()
    monkeypatch.setattr("litellm.acompletion", lambda *args, **kwargs: mock_response)

    tokens = []
    async for token in stream_response(
        "Please hang up.",
        agent,
        messages,
        call_context={"call_sid": "CA777"},
    ):
        tokens.append(token)

    assert any(isinstance(t, str) and "hang" in t.lower() for t in tokens)
    hangup_tokens = [t for t in tokens if isinstance(t, dict) and t.get("type") == "hangup_call"]
    assert hangup_tokens, "Expected hangup marker from stream_response"
