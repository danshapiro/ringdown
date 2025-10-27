"""HTTP client for the managed audio/video orchestration service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx

HTTP_TIMEOUT_SECONDS = 15.0


@dataclass
class ManagedAVSession:
    """Response payload describing an active managed A/V session."""

    session_id: str
    agent: str
    room_url: str
    access_token: str
    expires_at: datetime
    pipeline_session_id: Optional[str] = None
    greeting: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ManagedAVClient:
    """Client wrapper that talks to the managed A/V control plane."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        pipeline_handle: str,
        session_ttl_seconds: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required for ManagedAVClient")
        if not api_key:
            raise ValueError("api_key is required for ManagedAVClient")
        if not pipeline_handle:
            raise ValueError("pipeline_handle is required for ManagedAVClient")

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._pipeline_handle = pipeline_handle
        self._session_ttl_seconds = session_ttl_seconds
        self._metadata = metadata or {}

    async def start_session(
        self,
        *,
        device_id: str,
        agent_name: str,
        greeting: Optional[str],
        device_metadata: Optional[Dict[str, Any]],
    ) -> ManagedAVSession:
        """Request a new managed session for the given device/agent."""

        payload: Dict[str, Any] = {
            "pipelineHandle": self._pipeline_handle,
            "agent": agent_name,
            "device": {
                "id": device_id,
                "metadata": device_metadata or {},
            },
            "sessionTtlSeconds": self._session_ttl_seconds,
            "metadata": self._metadata,
        }
        if greeting:
            payload["greeting"] = greeting

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{self._base_url}/sessions",
                json=payload,
                headers=self._headers(),
            )
        response.raise_for_status()
        data = response.json()

        session_id = _require_str(data, "sessionId")
        room_url = _require_str(data, "roomUrl")
        access_token = _require_str(data, "accessToken")
        agent = data.get("agent") or agent_name
        pipeline_session_id = data.get("pipelineSessionId")
        greeting_response = data.get("greeting") or greeting
        metadata = data.get("metadata") or {}

        expires_at = _parse_expiry(data.get("expiresAt"), self._session_ttl_seconds)

        return ManagedAVSession(
            session_id=session_id,
            agent=agent,
            room_url=room_url,
            access_token=access_token,
            expires_at=expires_at,
            pipeline_session_id=pipeline_session_id,
            greeting=greeting_response,
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    async def close_session(self, session_id: str) -> None:
        """Notify the managed provider that a session has ended."""

        if not session_id:
            return

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            try:
                response = await client.delete(
                    f"{self._base_url}/sessions/{session_id}",
                    json={"pipelineHandle": self._pipeline_handle},
                    headers=self._headers(),
                )
                # Consider 404 as already closed.
                if response.status_code not in (200, 202, 204, 404):
                    response.raise_for_status()
            except httpx.HTTPError:
                # Treat shutdown failures as non-fatal; upstream retries externally.
                return

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }


def _require_str(data: Dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Managed A/V response missing required field '{key}'")
    return value


def _parse_expiry(expires_at: Any, ttl_seconds: int) -> datetime:
    if isinstance(expires_at, str) and expires_at:
        try:
            parsed = datetime.fromisoformat(expires_at)
        except ValueError:
            parsed = None
        else:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

    ttl = max(ttl_seconds, 60)
    return datetime.now(timezone.utc) + timedelta(seconds=ttl)

