"""In-memory store for mobile text streaming sessions."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple


@dataclass
class TextSessionState:
    """Represents an active or resumable mobile text session."""

    session_id: str
    device_id: str
    agent_name: str
    agent_config: Dict[str, Any]
    created_at: datetime
    expires_at: datetime
    resume_expires_at: datetime
    resume_token: str
    session_ttl_seconds: int
    resume_ttl_seconds: int
    heartbeat_interval_seconds: int
    heartbeat_timeout_seconds: int
    tls_pins: List[str]
    messages: List[Dict[str, Any]] = field(default_factory=list)
    active: bool = False
    greeting_sent: bool = False
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def refresh_expiry(self, session_ttl_seconds: int, resume_ttl_seconds: int) -> None:
        """Extend expiry windows for session and resume windows."""

        now = datetime.now(timezone.utc)
        ttl = max(session_ttl_seconds, 60)
        resume_ttl = max(resume_ttl_seconds, 60)
        self.session_ttl_seconds = ttl
        self.resume_ttl_seconds = resume_ttl
        self.expires_at = now + timedelta(seconds=ttl)
        self.resume_expires_at = now + timedelta(seconds=resume_ttl)
        self.last_seen = now


class TextSessionStore:
    """Manage ephemeral session tokens for mobile text streaming."""

    def __init__(self) -> None:
        self._sessions: Dict[str, TextSessionState] = {}
        self._session_tokens: Dict[str, str] = {}
        self._resume_index: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        *,
        device_id: str,
        agent_name: str,
        agent_config: Dict[str, Any],
        heartbeat_interval_seconds: int,
        heartbeat_timeout_seconds: int,
        tls_pins: List[str],
        session_ttl_seconds: int,
        resume_ttl_seconds: int,
    ) -> Tuple[TextSessionState, str]:
        """Create a brand new session and return (state, session_token)."""

        now = datetime.now(timezone.utc)
        session_id = str(uuid.uuid4())
        session_token = _generate_token()
        resume_token = _generate_token()

        ttl = max(session_ttl_seconds, 60)
        resume_ttl = max(resume_ttl_seconds, ttl)

        expires_at = now + timedelta(seconds=ttl)
        resume_expires_at = now + timedelta(seconds=resume_ttl)

        messages: List[Dict[str, Any]] = []
        prompt = agent_config.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            messages.append({"role": "system", "content": prompt})

        state = TextSessionState(
            session_id=session_id,
            device_id=device_id,
            agent_name=agent_name,
            agent_config=agent_config,
            created_at=now,
            expires_at=expires_at,
            resume_expires_at=resume_expires_at,
            resume_token=resume_token,
            session_ttl_seconds=ttl,
            resume_ttl_seconds=resume_ttl,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            tls_pins=list(tls_pins),
            messages=messages,
        )

        async with self._lock:
            self._prune_expired_locked(now)
            self._sessions[session_id] = state
            self._session_tokens[session_token] = session_id
            self._resume_index[resume_token] = session_id

        return state, session_token

    async def resume_session(
        self,
        *,
        resume_token: str,
        session_ttl_seconds: int,
        resume_ttl_seconds: int,
        heartbeat_interval_seconds: int,
        heartbeat_timeout_seconds: int,
        tls_pins: List[str],
    ) -> Tuple[TextSessionState, str]:
        """Resume an existing session, returning (state, new_session_token)."""

        now = datetime.now(timezone.utc)
        async with self._lock:
            self._prune_expired_locked(now)
            session_id = self._resume_index.get(resume_token)
            if not session_id:
                raise KeyError("resume_token not recognised")

            state = self._sessions.get(session_id)
            if state is None:
                raise KeyError("session not available")

            if state.active:
                raise RuntimeError("session already active")

            if state.resume_expires_at <= now:
                self._delete_locked(session_id)
                raise KeyError("resume window expired")

            new_session_token = _generate_token()
            new_resume_token = _generate_token()

            state.refresh_expiry(session_ttl_seconds, resume_ttl_seconds)
            state.resume_token = new_resume_token
            state.heartbeat_interval_seconds = heartbeat_interval_seconds
            state.heartbeat_timeout_seconds = heartbeat_timeout_seconds
            state.tls_pins = list(tls_pins)
            state.active = False

            self._session_tokens[new_session_token] = session_id
            self._resume_index.pop(resume_token, None)
            self._resume_index[new_resume_token] = session_id

            return state, new_session_token

    async def consume_session_token(self, session_token: str) -> TextSessionState:
        """Consume a one-time session token for WebSocket connection."""

        now = datetime.now(timezone.utc)
        async with self._lock:
            self._prune_expired_locked(now)
            session_id = self._session_tokens.pop(session_token, None)
            if not session_id:
                raise KeyError("session token invalid")

            state = self._sessions.get(session_id)
            if state is None:
                raise KeyError("session unavailable")

            if state.expires_at <= now:
                self._delete_locked(session_id)
                raise KeyError("session expired")

            state.active = True
            state.last_seen = now
            return state

    async def mark_disconnected(self, session_id: str) -> None:
        """Mark a session as disconnected and extend its resume window."""

        now = datetime.now(timezone.utc)
        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return
            state.active = False
            resume_ttl = max(state.resume_ttl_seconds, 60)
            state.resume_expires_at = now + timedelta(seconds=resume_ttl)
            state.last_seen = now

    async def update_messages(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """Persist conversation history for a session."""

        async with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return
            state.messages = list(messages)
            state.last_seen = datetime.now(timezone.utc)

    async def delete_session(self, session_id: str) -> None:
        """Remove a session entirely."""

        async with self._lock:
            self._delete_locked(session_id)

    def _delete_locked(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if not state:
            return

        to_remove = [token for token, sid in self._session_tokens.items() if sid == session_id]
        for token in to_remove:
            self._session_tokens.pop(token, None)

        self._resume_index = {
            token: sid for token, sid in self._resume_index.items() if sid != session_id
        }

    def _prune_expired_locked(self, now: datetime) -> None:
        expired: List[str] = []
        for session_id, state in list(self._sessions.items()):
            if state.active:
                # Active sessions rely on WebSocket to signal closure; do not prune here.
                continue
            if state.expires_at <= now and state.resume_expires_at <= now:
                expired.append(session_id)

        for session_id in expired:
            self._delete_locked(session_id)


_STORE: TextSessionStore | None = None


def get_text_session_store() -> TextSessionStore:
    """Return the singleton text session store."""

    global _STORE
    if _STORE is None:
        _STORE = TextSessionStore()
    return _STORE


def _generate_token() -> str:
    """Return a URL-safe random token."""

    return secrets.token_urlsafe(32)
