"""Realtime session management and bridging helpers for Android voice."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import threading
from typing import Any, Callable, Dict, Optional

import httpx

from app.logging_utils import logger
from app import settings


REALTIME_BASE_URL = "https://api.openai.com/v1/realtime/sessions"
HTTP_TIMEOUT_SECONDS = 15.0


@dataclass(slots=True)
class RealtimeSession:
    """Represents an active OpenAI Realtime session."""

    session_id: str
    client_secret: str
    websocket_url: str
    expires_at: datetime
    model: str
    voice: str
    agent_name: Optional[str] = None
    device_id: Optional[str] = None
    call_id: Optional[str] = None
    mobile_token: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self, *, buffer_seconds: int = 0) -> bool:
        """Return True if the session has expired (optionally with buffer)."""

        if buffer_seconds < 0:
            buffer_seconds = 0

        return self.expires_at <= datetime.now(timezone.utc) + timedelta(seconds=buffer_seconds)


class RealtimeSessionStore:
    """Thread-safe in-memory registry of realtime sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[str, RealtimeSession] = {}
        self._call_index: Dict[str, str] = {}
        self._lock = threading.Lock()

    def upsert(self, session: RealtimeSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            call_id = session.call_id
            if call_id:
                self._call_index[call_id] = session.session_id

    def get_session(self, session_id: str) -> RealtimeSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def get(self, session_id: str) -> RealtimeSession | None:
        return self.get_session(session_id)

    def get_by_call(self, call_id: str) -> RealtimeSession | None:
        with self._lock:
            session_id = self._call_index.get(call_id)
            if session_id:
                return self._sessions.get(session_id)
            return None

    def replace_session(self, call_id: str, session: RealtimeSession) -> None:
        with self._lock:
            previous = self._call_index.get(call_id)
            if previous and previous != session.session_id:
                self._sessions.pop(previous, None)
            self._sessions[session.session_id] = session
            self._call_index[call_id] = session.session_id

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            for call_id, sid in list(self._call_index.items()):
                if sid == session_id:
                    self._call_index.pop(call_id, None)

    def delete_call(self, call_id: str) -> None:
        with self._lock:
            session_id = self._call_index.pop(call_id, None)
            if session_id:
                self._sessions.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._call_index.clear()


_STORE: RealtimeSessionStore | None = None


def get_realtime_store() -> RealtimeSessionStore:
    """Return global realtime session store."""

    global _STORE
    if _STORE is None:
        _STORE = RealtimeSessionStore()
    return _STORE


async def create_realtime_session(
    *,
    agent_name: str,
    model: str,
    voice: str,
    device_id: str,
    instructions: str | None = None,
    metadata: Dict[str, Any] | None = None,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> RealtimeSession:
    """Create a new OpenAI Realtime session for the given device/agent pair."""

    api_key = settings.get_env().openai_api_key
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
        "model": model,
        "voice": voice,
        "modalities": ["text", "audio"],
    }
    if instructions:
        payload["instructions"] = instructions

    merged_metadata: Dict[str, Any] = {
        "agent": agent_name,
        "device_id": device_id,
    }
    if metadata:
        merged_metadata.update(metadata)
    payload["metadata"] = merged_metadata

    factory = client_factory or (lambda: httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS))

    async with factory() as client:
        response = await client.post(REALTIME_BASE_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    session_id = _extract_session_id(data)
    client_secret = _extract_client_secret(data)
    expires_at = _parse_expiry(data)
    websocket_url = _extract_websocket_url(data, model, session_id)

    return RealtimeSession(
        session_id=session_id,
        client_secret=client_secret,
        websocket_url=websocket_url,
        expires_at=expires_at,
        model=model,
        voice=voice,
        agent_name=agent_name,
        device_id=device_id,
        metadata=merged_metadata,
    )


async def mirror_assistant_turn(
    call_id: str | None,
    text: str,
    voice: str | None,
    *,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> None:
    """Mirror assistant text into an active realtime session."""

    if not call_id or not text:
        return

    store = get_realtime_store()
    session = store.get_by_call(call_id)
    if session is None:
        logger.debug("Realtime session for call %s not available; skipping mirror", call_id)
        return

    if session.is_expired(buffer_seconds=5):
        logger.warning("Realtime session %s expired; skipping mirror", session.session_id)
        return

    resolved_voice = voice or session.voice

    try:
        await send_assistant_audio(
            session,
            text,
            resolved_voice,
            client_factory=client_factory,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to mirror assistant turn to realtime session %s: %s",
            session.session_id,
            exc,
        )


async def send_assistant_audio(
    session: RealtimeSession,
    text: str,
    voice: str,
    *,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> None:
    """Send assistant text to realtime session using conversation.item + response.create."""

    if not text:
        return

    events_url = f"{REALTIME_BASE_URL}/{session.session_id}/events"
    headers = {
        "Authorization": f"Bearer {session.client_secret}",
        "Content-Type": "application/json",
    }

    conversation_event = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "input_text",
                    "text": text,
                }
            ],
        },
    }

    response_event = {
        "type": "response.create",
        "response": {
            "conversation": "current",
            "modalities": ["audio"],
            "instructions": text,
            "voice": voice,
        },
    }

    logger.info(
        "Mirroring assistant response to realtime session %s voice=%s",
        session.session_id,
        voice,
    )

    factory = client_factory or (lambda: httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS))

    async with factory() as client:
        for event in (conversation_event, response_event):
            response = await client.post(events_url, json=event, headers=headers)
            response.raise_for_status()


def _extract_session_id(data: Dict[str, Any]) -> str:
    session_id = data.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Realtime session response missing 'id'")
    return session_id


def _extract_client_secret(data: Dict[str, Any]) -> str:
    secret = data.get("client_secret")
    if isinstance(secret, str):
        return secret
    if isinstance(secret, dict):
        value = secret.get("value")
        if isinstance(value, str) and value:
            return value
    raise ValueError("Realtime session response missing client secret")


def _parse_expiry(data: Dict[str, Any]) -> datetime:
    candidate = data.get("expires_at")
    if isinstance(candidate, (int, float)):
        return datetime.fromtimestamp(candidate, tz=timezone.utc).replace(microsecond=0)
    if isinstance(candidate, str):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            parsed = None
        else:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).replace(microsecond=0)

    now = datetime.now(timezone.utc)
    ttl = data.get("ttl")
    if isinstance(ttl, (int, float)) and ttl > 0:
        return (now + timedelta(seconds=int(ttl))).replace(microsecond=0)

    client_secret = data.get("client_secret")
    if isinstance(client_secret, dict):
        client_expiry = client_secret.get("expires_at")
        if isinstance(client_expiry, (int, float)):
            return datetime.fromtimestamp(client_expiry, tz=timezone.utc).replace(microsecond=0)
        if isinstance(client_expiry, str):
            try:
                parsed = datetime.fromisoformat(client_expiry)
            except ValueError:
                parsed = None
            else:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc).replace(microsecond=0)

    return (now + timedelta(minutes=30)).replace(microsecond=0)


def _extract_websocket_url(data: Dict[str, Any], model: str, session_id: str) -> str:
    urls = data.get("urls")
    if isinstance(urls, dict):
        realtime_url = urls.get("realtime")
        if isinstance(realtime_url, str) and realtime_url:
            return realtime_url

    ws_url = data.get("websocket_url")
    if isinstance(ws_url, str) and ws_url:
        return ws_url

    return f"wss://api.openai.com/v1/realtime?model={model}&session={session_id}"


__all__ = [
    "RealtimeSession",
    "RealtimeSessionStore",
    "create_realtime_session",
    "get_realtime_store",
    "mirror_assistant_turn",
    "send_assistant_audio",
]
