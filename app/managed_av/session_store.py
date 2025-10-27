"""In-memory session tracking for managed audio/video conversations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


@dataclass
class ManagedSessionState:
    """Conversation context for a managed audio/video session."""

    session_id: str
    device_id: str
    agent_name: str
    agent_config: Dict[str, Any]
    greeting: Optional[str]
    created_at: datetime
    expires_at: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)
    messages: list[Dict[str, Any]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def extend(self, ttl_seconds: int) -> None:
        """Extend expiry when the managed provider renews the session."""

        if ttl_seconds <= 0:
            return

        new_expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        if new_expiry > self.expires_at:
            self.expires_at = new_expiry


class ManagedAVSessionStore:
    """Lightweight in-memory cache for active managed A/V sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[str, ManagedSessionState] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        *,
        session_id: str,
        device_id: str,
        agent_name: str,
        agent_config: Dict[str, Any],
        greeting: Optional[str],
        expires_at: Optional[datetime],
        ttl_seconds: Optional[int],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ManagedSessionState:
        """Create and store a new session, returning the managed state."""

        if not session_id:
            raise ValueError("session_id is required")

        now = datetime.now(timezone.utc)
        expiry = _coerce_expiry(expires_at, ttl_seconds, now)

        state = ManagedSessionState(
            session_id=session_id,
            device_id=device_id,
            agent_name=agent_name,
            agent_config=agent_config,
            greeting=greeting,
            created_at=now,
            expires_at=expiry,
            metadata=metadata or {},
        )

        prompt = agent_config.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            state.messages.append({"role": "system", "content": prompt})

        async with self._lock:
            self._prune_expired_locked(now)
            self._sessions[session_id] = state

        return state

    async def get_session(self, session_id: str) -> ManagedSessionState | None:
        """Return the session state if active and not expired."""

        now = datetime.now(timezone.utc)
        async with self._lock:
            self._prune_expired_locked(now)
            return self._sessions.get(session_id)

    async def delete_session(self, session_id: str) -> None:
        """Remove a session from the store."""

        async with self._lock:
            self._sessions.pop(session_id, None)

    async def clear(self) -> None:
        """Remove all sessions (primarily for testing)."""

        async with self._lock:
            self._sessions.clear()

    def _prune_expired_locked(self, now: datetime) -> None:
        expired = [sid for sid, state in self._sessions.items() if state.expires_at <= now]
        for sid in expired:
            self._sessions.pop(sid, None)


_STORE: ManagedAVSessionStore | None = None


def get_session_store() -> ManagedAVSessionStore:
    """Return the singleton session store instance."""

    global _STORE
    if _STORE is None:
        _STORE = ManagedAVSessionStore()
    return _STORE


def _coerce_expiry(
    expires_at: Optional[datetime],
    ttl_seconds: Optional[int],
    now: datetime,
) -> datetime:
    """Normalise the expiry timestamp for a session."""

    if expires_at:
        if expires_at.tzinfo is None:
            return expires_at.replace(tzinfo=timezone.utc)
        return expires_at.astimezone(timezone.utc)

    ttl = ttl_seconds or 0
    ttl = max(ttl, 60)
    return now + timedelta(seconds=ttl)

