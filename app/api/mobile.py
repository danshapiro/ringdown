"""Mobile client endpoints for device registration and local text streaming."""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app import settings
from app.chat import stream_response
from app.logging_utils import logger
from app.mobile.config_store import (
    approve_device,
    ensure_device_entry,
    ensure_device_security_fields,
)
from app.mobile.text_session_store import get_text_session_store
from app.memory import log_turn
from app.settings import get_agent_config

DEFAULT_POLL_AFTER_SECONDS = 5

router = APIRouter(prefix="/v1/mobile", tags=["mobile"])


class MobileRegisterRequest(BaseModel):
    """Registration payload submitted by the Android client."""

    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(..., alias="deviceId", min_length=4, max_length=128)
    label: Optional[str] = None
    platform: Optional[str] = None
    model: Optional[str] = None
    app_version: Optional[str] = Field(default=None, alias="appVersion")


class MobileRegisterResponse(BaseModel):
    """Backend response describing registration status."""

    model_config = ConfigDict(populate_by_name=True)

    status: str
    message: str
    poll_after_seconds: Optional[int] = Field(default=None, alias="pollAfterSeconds")
    agent: Optional[str] = None


class MobileTextSessionRequest(BaseModel):
    """Payload to initiate or resume a text streaming session."""

    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(..., alias="deviceId", min_length=4, max_length=128)
    auth_token: Optional[str] = Field(default=None, alias="authToken", min_length=1, max_length=256)
    agent: Optional[str] = None
    resume_token: Optional[str] = Field(default=None, alias="resumeToken")


class MobileTextSessionResponse(BaseModel):
    """Response describing the WebSocket bootstrap for text streaming."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(..., alias="sessionId")
    session_token: str = Field(..., alias="sessionToken")
    resume_token: str = Field(..., alias="resumeToken")
    websocket_path: str = Field(..., alias="websocketPath")
    agent: str
    expires_at: datetime = Field(..., alias="expiresAt")
    heartbeat_interval_seconds: int = Field(..., alias="heartbeatIntervalSeconds")
    heartbeat_timeout_seconds: int = Field(..., alias="heartbeatTimeoutSeconds")
    tls_pins: list[str] = Field(default_factory=list, alias="tlsPins")
    auth_token: Optional[str] = Field(default=None, alias="authToken")
    history: List["MobileConversationMessage"] = Field(default_factory=list, alias="history")


class MobileConversationMessage(BaseModel):
    """Serialised conversation entry shared with mobile clients."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    role: str
    text: str = ""
    timestamp_iso: Optional[str] = Field(default=None, alias="timestampIso")
    message_type: Optional[str] = Field(default=None, alias="messageType")
    tool_payload: Optional[Dict[str, Any]] = Field(default=None, alias="toolPayload")


_HISTORY_LIMIT = 200


def _serialise_history(messages: List[Dict[str, Any]] | None) -> List[MobileConversationMessage]:
    """Convert stored conversation messages into a mobile-friendly format."""

    if not messages:
        return []

    serialised: List[MobileConversationMessage] = []
    for entry in messages:
        role_value = str(entry.get("role") or "").strip().lower()
        if role_value not in {"assistant", "user", "tool"}:
            continue

        text_value = _extract_text(entry.get("content"))
        payload_value = _coerce_tool_payload(role_value, entry)
        if role_value != "tool" and not text_value:
            continue

        msg_type = entry.get("messageType") or entry.get("message_type")
        timestamp_iso = entry.get("timestampIso") or entry.get("timestamp_iso")
        message_id = entry.get("id") or entry.get("message_id")

        serialised.append(
            MobileConversationMessage(
                id=str(message_id or uuid.uuid4()),
                role=role_value,
                text=text_value or "",
                timestamp_iso=str(timestamp_iso).strip() if isinstance(timestamp_iso, str) and timestamp_iso.strip() else None,
                message_type=str(msg_type).strip() if isinstance(msg_type, str) and msg_type.strip() else None,
                tool_payload=payload_value,
            )
        )

    if len(serialised) > _HISTORY_LIMIT:
        return serialised[-_HISTORY_LIMIT:]
    return serialised


def _extract_text(content: Any) -> str:
    """Best-effort string extraction from LLM message content."""

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        for key in ("text", "content", "value"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    if isinstance(content, list):
        parts: List[str] = []
        for chunk in content:
            if isinstance(chunk, str) and chunk.strip():
                parts.append(chunk.strip())
            elif isinstance(chunk, dict):
                text = chunk.get("text") or chunk.get("content") or chunk.get("value")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()

    return ""


def _coerce_tool_payload(role: str, entry: Dict[str, Any]) -> Dict[str, Any] | None:
    """Normalise tool payloads so the client can render structured pills."""

    candidate = entry.get("toolPayload") or entry.get("tool_payload")
    payload = dict(candidate) if isinstance(candidate, dict) else None

    if role == "tool" and payload is None:
        content = entry.get("content")
        if isinstance(content, dict):
            payload = dict(content)
        elif isinstance(content, str):
            try:
                loaded = json.loads(content)
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, dict):
                payload = loaded

    if payload is not None:
        tool_call_id = entry.get("tool_call_id") or entry.get("toolCallId")
        if isinstance(tool_call_id, str) and tool_call_id.strip():
            payload = dict(payload)
            payload.setdefault("tool_call_id", tool_call_id.strip())

    return payload

def _resolve_greeting(agent_cfg: Dict[str, Any]) -> Optional[str]:
    candidate = agent_cfg.get("welcome_greeting")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return "You are connected to the Ringdown assistant."


def _normalise_device_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of *entry* with snake_case keys where appropriate."""

    result: Dict[str, Any] = dict(entry or {})
    if "pollAfterSeconds" in result and "poll_after_seconds" not in result:
        result["poll_after_seconds"] = result["pollAfterSeconds"]
    if "blockedReason" in result and "blocked_reason" not in result:
        result["blocked_reason"] = result["blockedReason"]
    if "approvedMessage" in result and "approved_message" not in result:
        result["approved_message"] = result["approvedMessage"]
    if "pendingMessage" in result and "pending_message" not in result:
        result["pending_message"] = result["pendingMessage"]
    if "authToken" in result and "auth_token" not in result:
        result["auth_token"] = result["authToken"]
    if "tlsPins" in result and "tls_pins" not in result:
        result["tls_pins"] = result["tlsPins"]
    if "sessionResumeTtlSeconds" in result and "session_resume_ttl_seconds" not in result:
        result["session_resume_ttl_seconds"] = result["sessionResumeTtlSeconds"]
    return result


@router.post("/devices/register", response_model=MobileRegisterResponse)
async def register_device(payload: MobileRegisterRequest) -> MobileRegisterResponse:
    """Register the device and return approval status."""

    device_id = payload.device_id.strip()
    if not device_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_device_id",
                "message": "deviceId must be a non-empty string.",
            },
        )

    metadata = {
        "platform": payload.platform,
        "model": payload.model,
        "app_version": payload.app_version,
    }

    created, entry = ensure_device_entry(device_id, label=payload.label, metadata=metadata)
    if created:
        logger.info("Added new mobile device %s to config.yaml (pending approval)", device_id)

    device_cfg = settings.get_mobile_device(device_id) or entry
    device_cfg = _normalise_device_entry(device_cfg)

    env_settings = settings.get_env()
    auto_device_id = (env_settings.live_test_mobile_device_id or "").strip()
    if auto_device_id and device_id == auto_device_id and not device_cfg.get("enabled"):
        desired_agent = (
            device_cfg.get("agent")
            or payload.label
            or settings.get_default_bot_name()
        )
        try:
            updated_entry = approve_device(device_id, agent=desired_agent)
            device_cfg = _normalise_device_entry(updated_entry)
            logger.info(
                "Auto-approved live test device %s (agent=%s)",
                device_id,
                device_cfg.get("agent"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to auto-approve live test device %s: %s", device_id, exc)

    enabled = bool(device_cfg.get("enabled"))
    blocked_reason = device_cfg.get("blocked_reason")

    if blocked_reason and not enabled:
        status_value = "DENIED"
        message = str(blocked_reason)
        poll_after = None
    elif enabled:
        status_value = "APPROVED"
        message = device_cfg.get("approved_message") or "Device approved"
        poll_after = None
    else:
        status_value = "PENDING"
        message = device_cfg.get("pending_message") or "Awaiting administrator approval"
        poll_after = int(device_cfg.get("poll_after_seconds") or DEFAULT_POLL_AFTER_SECONDS)

    return MobileRegisterResponse(
        status=status_value,
        message=message,
        poll_after_seconds=poll_after,
        agent=device_cfg.get("agent"),
    )


@router.post("/text/session", response_model=MobileTextSessionResponse)
async def text_session(payload: MobileTextSessionRequest) -> MobileTextSessionResponse:
    """Issue or resume a WebSocket text session for the mobile client."""

    device_id = payload.device_id.strip()
    if not device_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_device_id",
                "message": "deviceId must be a non-empty string.",
            },
        )

    raw_cfg = settings.get_mobile_device(device_id)
    if not raw_cfg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "device_not_registered",
                "message": f"Device '{device_id}' is not registered. Add the device under mobile_devices in config.yaml and redeploy.",
            },
        )

    device_cfg = _normalise_device_entry(raw_cfg)
    if not device_cfg.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "device_not_approved",
                "message": f"Device '{device_id}' is pending approval. Set enabled: true in config.yaml mobile_devices and redeploy.",
            },
        )

    provided_token = (payload.auth_token or "").strip()
    configured_token = (device_cfg.get("auth_token") or "").strip()

    if not configured_token:
        try:
            refreshed_entry = ensure_device_security_fields(device_id, metadata=device_cfg)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to backfill security fields for device %s: %s", device_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "security_initialisation_failed",
                    "message": f"Unable to prepare security configuration for device '{device_id}'. Check server logs.",
                },
            ) from exc
        device_cfg = _normalise_device_entry(refreshed_entry)
        configured_token = (device_cfg.get("auth_token") or "").strip()

    if provided_token and configured_token:
        if not secrets.compare_digest(provided_token, configured_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "invalid_credentials",
                    "message": "Auth token rejected. Verify the mobile device entry matches the handset token.",
                },
            )
    elif configured_token:
        logger.warning(
            "Device %s initiated text session without auth token; allowing due to legacy client",
            device_id,
        )
        provided_token = configured_token
    else:
        logger.warning("Device %s missing configured auth token; allowing unsecured handshake", device_id)

    agent_name = payload.agent or device_cfg.get("agent")
    if not agent_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "agent_not_specified",
                "message": "Agent not specified in request or device configuration.",
            },
        )

    try:
        agent_cfg = settings.get_agent_config(agent_name)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "agent_not_found",
                "message": f"Agent '{agent_name}' not found in server configuration.",
            },
        ) from exc

    text_cfg = settings.get_mobile_text_config()
    heartbeat_interval = int(text_cfg.get("heartbeat_interval_seconds") or 15)
    heartbeat_timeout = int(text_cfg.get("heartbeat_timeout_seconds") or 45)
    session_ttl = int(text_cfg.get("session_ttl_seconds") or 900)
    resume_default = int(text_cfg.get("resume_ttl_seconds") or 300)
    device_resume = int(device_cfg.get("session_resume_ttl_seconds") or resume_default)
    resume_ttl = max(device_resume, 60)

    tls_pins: list[str] = []
    for pin in text_cfg.get("tls_pins", []):
        if pin not in tls_pins:
            tls_pins.append(pin)
    for pin in device_cfg.get("tls_pins", []):
        if pin not in tls_pins:
            tls_pins.append(pin)

    store = get_text_session_store()
    resumed = bool(payload.resume_token)

    if resumed:
        resume_token = (payload.resume_token or "").strip()
        try:
            state, session_token = await store.resume_session(
                resume_token=resume_token,
                session_ttl_seconds=session_ttl,
                resume_ttl_seconds=resume_ttl,
                heartbeat_interval_seconds=heartbeat_interval,
                heartbeat_timeout_seconds=heartbeat_timeout,
                tls_pins=tls_pins,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "resume_token_not_recognised",
                    "message": "Resume token not recognised or expired.",
                },
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "session_already_active",
                    "message": "Session already active on another connection.",
                },
            ) from exc

        if state.agent_name != agent_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "agent_mismatch",
                    "message": "Resume token belongs to a different agent.",
                },
            )
        state.agent_config = agent_cfg
    else:
        state, session_token = await store.create_session(
            device_id=device_id,
            agent_name=agent_name,
            agent_config=agent_cfg,
            heartbeat_interval_seconds=heartbeat_interval,
            heartbeat_timeout_seconds=heartbeat_timeout,
            tls_pins=tls_pins,
            session_ttl_seconds=session_ttl,
            resume_ttl_seconds=resume_ttl,
        )

    websocket_path = str(text_cfg.get("websocket_path") or "/v1/mobile/text/session")

    history = _serialise_history(state.messages)

    logger.info(
        json.dumps(
            {
                "event": "mobile_text_session.issued",
                "deviceId": device_id,
                "sessionId": state.session_id,
                "resumed": resumed,
            },
            ensure_ascii=True,
        )
    )

    return MobileTextSessionResponse(
        session_id=state.session_id,
        session_token=session_token,
        resume_token=state.resume_token,
        websocket_path=websocket_path,
        agent=state.agent_name,
        expires_at=state.expires_at,
        heartbeat_interval_seconds=heartbeat_interval,
        heartbeat_timeout_seconds=heartbeat_timeout,
        tls_pins=tls_pins,
        auth_token=configured_token or None,
        history=history,
    )


__all__ = ["router"]
