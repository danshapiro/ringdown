"""Tests for the mobile text streaming websocket."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.mobile.text_session_store import TextSessionState


def _stub_metric():
    counter = MagicMock()
    counter.inc = MagicMock()
    metric = MagicMock()
    metric.labels.return_value = counter
    return metric


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _build_state(session_id: str = "session-1") -> TextSessionState:
    now = datetime.now(timezone.utc)
    return TextSessionState(
        session_id=session_id,
        device_id="device-1",
        agent_name="Agent Alpha",
        agent_config={
            "model": "gpt-5",
            "prompt": "You are helpful.",
            "welcome_greeting": "Hi there!",
        },
        created_at=now,
        expires_at=now + timedelta(seconds=900),
        resume_expires_at=now + timedelta(seconds=900),
        resume_token="resume-token",
        session_ttl_seconds=900,
        resume_ttl_seconds=300,
        heartbeat_interval_seconds=12,
        heartbeat_timeout_seconds=30,
        tls_pins=[],
        messages=[],
    )


def test_mobile_text_session_streams_tokens(client: TestClient) -> None:
    metric = _stub_metric()

    async def fake_stream_response(*args, **kwargs):
        yield "Response ready."

    state = _build_state()
    store = MagicMock()
    store.consume_session_token = AsyncMock(return_value=state)
    store.update_messages = AsyncMock()
    store.mark_disconnected = AsyncMock()

    with patch("app.api.mobile_text.get_text_session_store", return_value=store), patch(
        "app.api.mobile_text.stream_response", fake_stream_response
    ), patch(
        "app.api.mobile_text.log_turn"
    ) as mock_log, patch(
        "app.api.mobile_text.litellm.token_counter", return_value=2
    ), patch(
        "app.api.mobile_text.METRIC_MESSAGES", metric
    ):
        with client.websocket_connect(
            "/v1/mobile/text/session",
            headers={"x-ringdown-session-token": "session-token"},
        ) as websocket:
            ready = websocket.receive_json()
            assert ready["type"] == "ready"
            assert ready["agent"] == "Agent Alpha"
            assert ready["heartbeatIntervalSeconds"] == 12

            greeting = websocket.receive_json()
            assert greeting["type"] == "assistant_token"
            assert greeting["final"] is True
            assert greeting["token"] == "Hi there!"

            websocket.send_json({"type": "user_token", "token": "Hello", "final": True})
            assistant = websocket.receive_json()
            assert assistant["type"] == "assistant_token"
            assert assistant["final"] is True
            assert "Response ready." in assistant["token"]

        store.consume_session_token.assert_awaited_once()
        store.update_messages.assert_awaited()
        store.mark_disconnected.assert_awaited_with("session-1")
        mock_log.assert_any_call("bot", "Hi there!", source="mobile-text")
        mock_log.assert_any_call("user", "Hello", source="mobile-text")
        mock_log.assert_any_call("bot", "Response ready.", source="mobile-text")


def test_mobile_text_session_forwards_tool_markers(client: TestClient) -> None:
    metric = _stub_metric()

    async def fake_stream_response(*args, **kwargs):
        yield {"type": "tool_executing", "detail": "searching"}
        yield "Finished."

    state = _build_state(session_id="session-2")
    state.agent_config["welcome_greeting"] = "Hello!"

    store = MagicMock()
    store.consume_session_token = AsyncMock(return_value=state)
    store.update_messages = AsyncMock()
    store.mark_disconnected = AsyncMock()

    with patch("app.api.mobile_text.get_text_session_store", return_value=store), patch(
        "app.api.mobile_text.stream_response", fake_stream_response
    ), patch("app.api.mobile_text.log_turn"), patch(
        "app.api.mobile_text.litellm.token_counter", return_value=1
    ), patch(
        "app.api.mobile_text.METRIC_MESSAGES", metric
    ):
        with client.websocket_connect(
            "/v1/mobile/text/session",
            headers={"x-ringdown-session-token": "session-token"},
        ) as websocket:
            websocket.receive_json()  # ready
            websocket.receive_json()  # greeting

            websocket.send_json({"type": "user_message", "text": "Run search", "final": True})
            marker = websocket.receive_json()
            assert marker["type"] == "tool_event"
            assert marker["event"] == "tool_executing"

            assistant = websocket.receive_json()
            assert assistant["type"] == "assistant_token"
            assert assistant["final"] is True
            assert assistant["token"] == "Finished."


def test_mobile_text_session_rejects_unknown_session_token(client: TestClient) -> None:
    store = MagicMock()
    store.consume_session_token = AsyncMock(side_effect=KeyError("missing"))

    with patch("app.api.mobile_text.get_text_session_store", return_value=store):
        try:
            with client.websocket_connect(
                "/v1/mobile/text/session", headers={"x-ringdown-session-token": "bad-token"}
            ) as websocket:
                with pytest.raises(WebSocketDisconnect) as exc:
                    websocket.receive_json()
                assert exc.value.code == status.WS_1008_POLICY_VIOLATION
        except WebSocketDisconnect as exc:
            assert exc.code == status.WS_1008_POLICY_VIOLATION

