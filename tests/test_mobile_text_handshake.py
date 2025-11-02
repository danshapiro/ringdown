"""Tests for the mobile text session REST endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.mobile.text_session_store import TextSessionState

client = TestClient(app)


def _state() -> TextSessionState:
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
    store.resume_session.assert_awaited_once()


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
