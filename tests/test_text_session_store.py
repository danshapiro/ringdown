from __future__ import annotations

import asyncio

import pytest

from app.mobile.text_session_store import TextSessionStore


def test_create_session_preserves_shorter_resume_ttl() -> None:
    async def _run() -> None:
        store = TextSessionStore()
        state, _token = await store.create_session(
            device_id="device-1",
            agent_name="Agent Alpha",
            agent_config={},
            heartbeat_interval_seconds=12,
            heartbeat_timeout_seconds=30,
            tls_pins=[],
            session_ttl_seconds=900,
            resume_ttl_seconds=300,
        )

        assert state.session_ttl_seconds == 900
        assert state.resume_ttl_seconds == 300
        resume_delta = int((state.resume_expires_at - state.created_at).total_seconds())
        assert 295 <= resume_delta <= 305

    asyncio.run(_run())


def test_resume_session_preserves_shorter_resume_ttl() -> None:
    async def _run() -> None:
        store = TextSessionStore()
        created, _token = await store.create_session(
            device_id="device-1",
            agent_name="Agent Alpha",
            agent_config={},
            heartbeat_interval_seconds=12,
            heartbeat_timeout_seconds=30,
            tls_pins=[],
            session_ttl_seconds=900,
            resume_ttl_seconds=300,
        )

        resumed, _new_token = await store.resume_session(
            resume_token=created.resume_token,
            session_ttl_seconds=900,
            resume_ttl_seconds=300,
            heartbeat_interval_seconds=12,
            heartbeat_timeout_seconds=30,
            tls_pins=[],
        )

        assert resumed.resume_ttl_seconds == 300
        resume_delta = int((resumed.resume_expires_at - resumed.last_seen).total_seconds())
        assert 295 <= resume_delta <= 305

    asyncio.run(_run())


def test_resume_session_invalidates_previous_session_token() -> None:
    async def _run() -> None:
        store = TextSessionStore()
        created, original_token = await store.create_session(
            device_id="device-1",
            agent_name="Agent Alpha",
            agent_config={},
            heartbeat_interval_seconds=12,
            heartbeat_timeout_seconds=30,
            tls_pins=[],
            session_ttl_seconds=900,
            resume_ttl_seconds=300,
        )

        resumed, replacement_token = await store.resume_session(
            resume_token=created.resume_token,
            session_ttl_seconds=900,
            resume_ttl_seconds=300,
            heartbeat_interval_seconds=12,
            heartbeat_timeout_seconds=30,
            tls_pins=[],
        )

        with pytest.raises(KeyError, match="session token invalid"):
            await store.consume_session_token(original_token)

        active = await store.consume_session_token(replacement_token)
        assert active.session_id == resumed.session_id
        assert active.active is True

    asyncio.run(_run())


def test_consume_session_token_rejects_duplicate_active_session_token() -> None:
    async def _run() -> None:
        store = TextSessionStore()
        created, session_token = await store.create_session(
            device_id="device-1",
            agent_name="Agent Alpha",
            agent_config={},
            heartbeat_interval_seconds=12,
            heartbeat_timeout_seconds=30,
            tls_pins=[],
            session_ttl_seconds=900,
            resume_ttl_seconds=300,
        )
        duplicate_token = "duplicate-session-token"
        store._session_tokens[duplicate_token] = created.session_id

        consumed = await store.consume_session_token(session_token)
        assert consumed.active is True

        with pytest.raises(RuntimeError, match="session already active"):
            await store.consume_session_token(duplicate_token)

    asyncio.run(_run())
