from __future__ import annotations

import asyncio

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
