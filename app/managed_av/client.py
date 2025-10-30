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
        agent_name: str,
        session_ttl_seconds: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required for ManagedAVClient")
        if not api_key:
            raise ValueError("api_key is required for ManagedAVClient")
        if not agent_name:
            raise ValueError("agent_name is required for ManagedAVClient")

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._agent_name = agent_name
        self._session_ttl_seconds = session_ttl_seconds
        self._metadata = metadata or {}

    async def start_session(
        self,
        *,
        device_id: str,
        agent_name: str,
        greeting: Optional[str],
        device_metadata: Optional[Dict[str, Any]],
        session_metadata: Optional[Dict[str, Any]] = None,
    ) -> ManagedAVSession:
        """Request a new managed session for the given device/agent."""

        body_payload: Dict[str, Any] = {
            "device": {
                "id": device_id,
                "metadata": device_metadata or {},
            },
            "sessionTtlSeconds": self._session_ttl_seconds,
        }
        if greeting:
            body_payload["greeting"] = greeting

        merged_metadata: Dict[str, Any] = {}
        if isinstance(self._metadata, dict) and self._metadata:
            merged_metadata.update(self._metadata)
        if session_metadata:
            merged_metadata.update(session_metadata)
        if merged_metadata:
            body_payload["metadata"] = merged_metadata

        request_payload: Dict[str, Any] = {
            "createDailyRoom": True,
            "body": body_payload,
        }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{self._base_url}/{self._agent_name}/start",
                json=request_payload,
                headers=self._headers(),
            )
        response.raise_for_status()
        data = response.json()

        session_id = _require_str(data, "sessionId")
        agent = data.get("agentName") or agent_name

        room_url = _coerce_room_url(data.get("dailyRoom"))
        access_token = _coerce_meeting_token(data.get("dailyMeetingToken") or data.get("dailyToken"))

        response_metadata = data.get("metadata")
        combined_metadata: Dict[str, Any] = {}
        if merged_metadata:
            combined_metadata.update(merged_metadata)
        if isinstance(response_metadata, dict):
            combined_metadata.update(response_metadata)

        expires_at = _parse_expiry(data.get("expiresAt"), self._session_ttl_seconds)

        pipeline_session_id = data.get("pipelineSessionId") or data.get("pipeline_session_id")
        if isinstance(pipeline_session_id, str) and pipeline_session_id.strip():
            pipeline_session_id = pipeline_session_id.strip()
        else:
            pipeline_session_id = session_id
        if pipeline_session_id and "pipeline_session_id" not in combined_metadata:
            combined_metadata = dict(combined_metadata)
            combined_metadata["pipeline_session_id"] = pipeline_session_id

        return ManagedAVSession(
            session_id=session_id,
            agent=agent,
            room_url=room_url,
            access_token=access_token,
            expires_at=expires_at,
            pipeline_session_id=pipeline_session_id,
            greeting=greeting,
            metadata=combined_metadata,
        )

    async def close_session(self, session_id: str) -> None:
        """Notify the managed provider that a session has ended."""

        if not session_id:
            return

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            try:
                response = await client.delete(
                    f"{self._base_url}/agents/{self._agent_name}/sessions/{session_id}",
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


def _coerce_room_url(room_payload: Any) -> str:
    if isinstance(room_payload, str) and room_payload:
        return room_payload
    if isinstance(room_payload, dict):
        candidate = room_payload.get("url")
        if isinstance(candidate, str) and candidate:
            return candidate
    raise ValueError("Managed A/V response missing Daily room URL")


def _coerce_meeting_token(token_payload: Any) -> str:
    if isinstance(token_payload, str) and token_payload:
        return token_payload
    if isinstance(token_payload, dict):
        candidate = token_payload.get("token") or token_payload.get("value")
        if isinstance(candidate, str) and candidate:
            return candidate
    raise ValueError("Managed A/V response missing Daily access token")


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
