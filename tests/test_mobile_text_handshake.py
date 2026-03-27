"""Tests for the mobile text session REST endpoint."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.mobile.text_session_store import TextSessionState

client = TestClient(app)


def _state() -> TextSessionState:
    now = datetime.now(UTC)
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
        expires_at=now + timedelta(seconds=900),
        resume_expires_at=now + timedelta(seconds=900),
        resume_token="resume-abc",
        session_ttl_seconds=900,
        resume_ttl_seconds=300,
        heartbeat_interval_seconds=12,
        heartbeat_timeout_seconds=30,
        tls_pins=["pin-device"],
        messages=[],
    )


def _patch_config(store: MagicMock) -> dict:
    device_cfg = {
        "enabled": True,
        "agent": "Agent Alpha",
        "auth_token": "secret-token",
        "tls_pins": ["pin-device"],
        "session_resume_ttl_seconds": 450,
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

    patches = [
        patch("app.api.mobile.settings.get_mobile_device", return_value=device_cfg),
        patch("app.api.mobile.settings.get_mobile_text_config", return_value=text_cfg),
        patch("app.api.mobile.settings.get_agent_config", return_value=agent_cfg),
        patch("app.api.mobile.get_text_session_store", return_value=store),
    ]
    return patches


def test_text_session_handshake_creates_session() -> None:
    state = _state()
    store = MagicMock()
    store.create_session = AsyncMock(return_value=(state, "session-token"))

    patches = _patch_config(store)
    with contextlib.ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        response = client.post(
            "/v1/mobile/text/session",
            json={"deviceId": "device-123", "authToken": "secret-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["sessionId"] == "session-abc"
    assert body["sessionToken"] == "session-token"
    assert body["resumeToken"] == "resume-abc"
    assert body["websocketPath"] == "/v1/mobile/text/session"
    assert body["heartbeatIntervalSeconds"] == 12
    assert body["heartbeatTimeoutSeconds"] == 30
    assert body["tlsPins"] == ["pin-global", "pin-device"]
    assert body["authToken"] == "secret-token"
    assert body["history"] == []
    store.create_session.assert_awaited_once()


def test_text_session_handshake_resumes_session() -> None:
    state = _state()
    state.resume_token = "resume-old"
    store = MagicMock()
    store.resume_session = AsyncMock(return_value=(state, "new-session-token"))

    patches = _patch_config(store)
    with contextlib.ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        response = client.post(
            "/v1/mobile/text/session",
            json={
                "deviceId": "device-123",
                "authToken": "secret-token",
                "resumeToken": "resume-old",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["sessionToken"] == "new-session-token"
    assert data["resumeToken"] == state.resume_token
    assert data["authToken"] == "secret-token"
    assert data["history"] == []
    store.resume_session.assert_awaited_once()


def test_text_session_handshake_includes_history_snapshot() -> None:
    state = _state()
    state.messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": " Hi there "},
        {"role": "assistant", "content": [{"type": "text", "text": "Hello!"}]},
        {
            "role": "tool",
            "tool_call_id": "tc-1",
            "content": json.dumps({"action": "lookup", "status": "complete"}),
        },
        {"role": "assistant", "content": ""},
    ]
    store = MagicMock()
    store.create_session = AsyncMock(return_value=(state, "session-token"))

    patches = _patch_config(store)
    with contextlib.ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        response = client.post(
            "/v1/mobile/text/session",
            json={"deviceId": "device-123", "authToken": "secret-token"},
        )

    assert response.status_code == 200
    history = response.json()["history"]
    assert len(history) == 3
    assert history[0]["role"] == "user"
    assert history[0]["text"] == "Hi there"
    assert history[1]["role"] == "assistant"
    assert history[1]["text"] == "Hello!"
    assert history[2]["role"] == "tool"
    assert history[2]["toolPayload"]["action"] == "lookup"
    assert history[2]["toolPayload"]["status"] == "complete"
    assert history[2]["toolPayload"]["tool_call_id"] == "tc-1"


def test_text_session_handshake_backfills_missing_token() -> None:
    state = _state()
    store = MagicMock()
    store.create_session = AsyncMock(return_value=(state, "session-token"))

    device_cfg = {
        "enabled": True,
        "agent": "Agent Alpha",
        # auth_token intentionally missing
    }
    text_cfg = {
        "websocket_path": "/v1/mobile/text/session",
        "session_ttl_seconds": 900,
        "resume_ttl_seconds": 300,
        "heartbeat_interval_seconds": 12,
        "heartbeat_timeout_seconds": 30,
        "tls_pins": [],
    }
    agent_cfg = {
        "model": "gpt-5",
        "prompt": "You are helpful.",
        "welcome_greeting": "Hi there!",
    }

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("app.api.mobile.settings.get_mobile_device", return_value=device_cfg)
        )
        stack.enter_context(
            patch("app.api.mobile.settings.get_mobile_text_config", return_value=text_cfg)
        )
        stack.enter_context(
            patch("app.api.mobile.settings.get_agent_config", return_value=agent_cfg)
        )
        stack.enter_context(patch("app.api.mobile.get_text_session_store", return_value=store))
        ensure_patch = stack.enter_context(
            patch(
                "app.api.mobile.ensure_device_security_fields",
                return_value={**device_cfg, "auth_token": "generated-token"},
            )
        )

        response = client.post(
            "/v1/mobile/text/session",
            json={"deviceId": "device-123"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["authToken"] == "generated-token"
    ensure_patch.assert_called_once_with("device-123", metadata=device_cfg)
    store.create_session.assert_awaited_once()


def test_text_session_handshake_rejects_bad_auth() -> None:
    store = MagicMock()
    patches = _patch_config(store)
    with contextlib.ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        resp = client.post(
            "/v1/mobile/text/session",
            json={"deviceId": "device-123", "authToken": "wrong000"},
        )

    assert resp.status_code == 401
    detail = resp.json()["detail"]
    assert detail["code"] == "invalid_credentials"
    store.create_session.assert_not_called()


def test_text_session_handshake_rejects_unknown_resume() -> None:
    store = MagicMock()
    store.resume_session = AsyncMock(side_effect=KeyError("missing"))
    patches = _patch_config(store)
    with contextlib.ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        resp = client.post(
            "/v1/mobile/text/session",
            json={
                "deviceId": "device-123",
                "authToken": "secret-token",
                "resumeToken": "resume-missing",
            },
        )

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "resume_token_not_recognised"


def test_text_session_handshake_conflict_when_active() -> None:
    store = MagicMock()
    store.resume_session = AsyncMock(side_effect=RuntimeError("active"))
    patches = _patch_config(store)
    with contextlib.ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        resp = client.post(
            "/v1/mobile/text/session",
            json={
                "deviceId": "device-123",
                "authToken": "secret-token",
                "resumeToken": "resume-active",
            },
        )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "session_already_active"


def test_text_session_unknown_device_returns_error_detail() -> None:
    store = MagicMock()
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("app.api.mobile.settings.get_mobile_device", return_value=None))
        stack.enter_context(
            patch("app.api.mobile.settings.get_mobile_text_config", return_value={})
        )
        stack.enter_context(patch("app.api.mobile.settings.get_agent_config", return_value={}))
        stack.enter_context(patch("app.api.mobile.get_text_session_store", return_value=store))
        response = client.post(
            "/v1/mobile/text/session",
            json={"deviceId": "device-999", "authToken": "token"},
        )

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "device_not_registered"
    assert "mobile_devices" in detail["message"]


def test_text_session_denied_when_device_not_enabled() -> None:
    store = MagicMock()
    device_cfg = {"enabled": False, "agent": "Agent Alpha"}
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("app.api.mobile.settings.get_mobile_device", return_value=device_cfg)
        )
        stack.enter_context(
            patch("app.api.mobile.settings.get_mobile_text_config", return_value={})
        )
        stack.enter_context(patch("app.api.mobile.settings.get_agent_config", return_value={}))
        stack.enter_context(patch("app.api.mobile.get_text_session_store", return_value=store))
        response = client.post(
            "/v1/mobile/text/session",
            json={"deviceId": "device-123", "authToken": "token"},
        )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "device_not_approved"
