"""Shared call-state registry used by Twilio and WebSocket handlers."""

from __future__ import annotations

CallSession = tuple[str, dict, list | None, bool, str | None]


class _CallRegistry:
    """In-memory registry for active ConversationRelay sessions."""

    def __init__(self) -> None:
        self._call_agent_map: dict[str, CallSession] = {}
        self._active_agents: set[str] = set()

    # ------------------------------------------------------------------
    # Call metadata staging (Twilio webhook → WebSocket setup handshake)
    # ------------------------------------------------------------------
    def store(self, call_sid: str, session: CallSession) -> None:
        self._call_agent_map[call_sid] = session

    def pop(self, call_sid: str) -> CallSession | None:
        return self._call_agent_map.pop(call_sid, None)

    # ------------------------------------------------------------------
    # Concurrency guard helpers
    # ------------------------------------------------------------------
    def mark_active(self, agent_name: str) -> None:
        self._active_agents.add(agent_name)

    def release(self, agent_name: str | None) -> None:
        if agent_name is not None:
            self._active_agents.discard(agent_name)

    def is_active(self, agent_name: str) -> bool:
        return agent_name in self._active_agents


_registry = _CallRegistry()


def store_call(call_sid: str, session: CallSession) -> None:
    """Persist metadata for a Twilio call until the WebSocket handshake."""

    _registry.store(call_sid, session)


def pop_call(call_sid: str) -> CallSession | None:
    """Return and remove the staged call session for *call_sid*."""

    return _registry.pop(call_sid)


def mark_agent_active(agent_name: str) -> None:
    """Record that *agent_name* is handling a live call."""

    _registry.mark_active(agent_name)


def release_agent(agent_name: str | None) -> None:
    """Clear the active-call flag for *agent_name* if set."""

    _registry.release(agent_name)


def agent_is_active(agent_name: str) -> bool:
    """Return True if *agent_name* already has a live call."""

    return _registry.is_active(agent_name)
