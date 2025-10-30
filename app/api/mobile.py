"""Mobile client endpoints for device registration and managed A/V sessions."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app import settings
from app.chat import stream_response
from app.logging_utils import logger
from app.managed_av.client import ManagedAVClient, ManagedAVSession
from app.managed_av.session_store import ManagedAVSessionStore, ManagedSessionState, get_session_store
from app.call_state import store_call
from app.mobile.config_store import ensure_device_entry
from app.memory import log_turn
from app.settings import get_agent_config
from app.mobile.realtime import (
    RealtimeSession,
    create_realtime_session,
    get_realtime_store,
)

DEFAULT_POLL_AFTER_SECONDS = 5
PIPECAT_API_KEY_ENV = "PIPECAT_API_KEY"
ANDROID_MANAGED_SOURCE = "android-managed-av"

router = APIRouter(prefix="/v1/mobile", tags=["mobile"])

_session_store: ManagedAVSessionStore = get_session_store()
_managed_client: ManagedAVClient | None = None


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


class MobileVoiceSessionRequest(BaseModel):
    """Payload requesting a new managed voice session."""

    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(..., alias="deviceId", min_length=4, max_length=128)
    agent: Optional[str] = None


class MobileVoiceSessionResponse(BaseModel):
    """Response describing managed session bootstrap parameters."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(..., alias="sessionId")
    agent: str
    room_url: str = Field(..., alias="roomUrl")
    access_token: str = Field(..., alias="accessToken")
    expires_at: datetime = Field(..., alias="expiresAt")
    pipeline_session_id: Optional[str] = Field(default=None, alias="pipelineSessionId")
    greeting: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ManagedAVCompletionRequest(BaseModel):
    """Request payload delivered by the managed A/V pipeline."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(..., alias="sessionId")
    text: str
    final: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ManagedAVCompletionResponse(BaseModel):
    """Response payload returned to the managed pipeline."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool = True
    response_text: str = Field(..., alias="responseText")
    hold_text: Optional[str] = Field(default=None, alias="holdText")
    reset: bool = False


class MobileRealtimeSessionRequest(BaseModel):
    """Request payload for initiating realtime conversation bridging."""

    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(..., alias="deviceId", min_length=4, max_length=128)
    agent: Optional[str] = None


class MobileRealtimeRefreshRequest(BaseModel):
    """Request payload to refresh a realtime session secret."""

    model_config = ConfigDict(populate_by_name=True)

    call_sid: str = Field(..., alias="callSid")


class MobileRealtimeSessionResponse(BaseModel):
    """Response containing realtime session bootstrap details for devices."""

    model_config = ConfigDict(populate_by_name=True)

    call_sid: str = Field(..., alias="callSid")
    session_id: str = Field(..., alias="sessionId")
    client_secret: str = Field(..., alias="clientSecret")
    websocket_url: str = Field(..., alias="websocketUrl")
    expires_at: datetime = Field(..., alias="expiresAt")
    model: str
    voice: str
    websocket_token: str = Field(..., alias="websocketToken")
    server_vad: Dict[str, Any] = Field(default_factory=dict, alias="serverVad")


@router.post("/realtime/session", response_model=MobileRealtimeSessionResponse)
async def start_realtime_session(payload: MobileRealtimeSessionRequest) -> MobileRealtimeSessionResponse:
    """Create a realtime session and seed conversation state for Android devices."""

    device_id = payload.device_id.strip()
    device_entry = settings.get_mobile_device(device_id)
    if device_entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not registered")

    if not device_entry.get("enabled", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device not approved")

    agent_name = (payload.agent or device_entry.get("agent") or "unknown-caller").strip()
    agent_cfg = get_agent_config(agent_name)
    realtime_cfg = settings.get_agent_realtime_config(agent_name)

    voice = str(realtime_cfg.get("voice") or "").strip()
    if not voice:
        voice = str(agent_cfg.get("voice") or "").strip()
    if not voice:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Realtime voice missing")

    model = str(realtime_cfg.get("model") or "").strip()
    if not model:
        model = str(agent_cfg.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Realtime model missing")

    server_vad = realtime_cfg.get("server_vad")
    if not isinstance(server_vad, dict):
        server_vad = {}

    instructions = agent_cfg.get("realtime_instructions")
    if isinstance(instructions, str):
        instructions = instructions.strip() or None
    else:
        instructions = None

    metadata = {}
    context = device_entry.get("context")
    if isinstance(context, dict):
        metadata["device_context"] = context
    metadata["server_vad"] = copy.deepcopy(server_vad)

    call_sid = f"android-{uuid4().hex}"
    agent_snapshot = copy.deepcopy(agent_cfg)
    websocket_token = uuid4().hex

    session = await create_realtime_session(
        agent_name=agent_name,
        model=model,
        voice=voice,
        device_id=device_id,
        instructions=instructions,
        metadata=metadata if metadata else None,
    )
    session.call_id = call_sid
    session.mobile_token = websocket_token
    session.metadata.update(metadata)

    store = get_realtime_store()
    store.upsert(session)

    extras: Dict[str, Any] = {
        "realtime_session_id": session.session_id,
        "realtime_model": session.model,
        "realtime_voice": session.voice,
        "device_id": device_id,
        "mobile_token": websocket_token,
        "transport": "android-realtime",
        "server_vad": copy.deepcopy(server_vad),
    }

    caller_label = device_entry.get("label") or device_id
    metadata["device_label"] = caller_label
    store_call(call_sid, (agent_name, agent_snapshot, None, False, caller_label, extras))

    logger.info(
        json.dumps(
            {
                "severity": "INFO",
                "event": "mobile_realtime_session_created",
                "session_id": session.session_id,
                "call_sid": call_sid,
                "device_id": device_id,
                "agent": agent_name,
                "model": session.model,
                "voice": session.voice,
                "server_vad": server_vad,
            }
        )
    )

    payload = MobileRealtimeSessionResponse(
        call_sid=call_sid,
        session_id=session.session_id,
        client_secret=session.client_secret,
        websocket_url=session.websocket_url,
        expires_at=session.expires_at,
        model=session.model,
        voice=session.voice,
        websocket_token=websocket_token,
        server_vad=copy.deepcopy(server_vad),
    )
    return JSONResponse(
        content=payload.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=False,
            exclude_unset=False,
        )
    )

@router.post("/realtime/session/refresh", response_model=MobileRealtimeSessionResponse)
async def refresh_realtime_session(payload: MobileRealtimeRefreshRequest) -> MobileRealtimeSessionResponse:
    """Refresh an existing realtime session secret for continued playback."""

    call_sid = payload.call_sid.strip()
    if not call_sid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="callSid required")

    store = get_realtime_store()
    existing = store.get_by_call(call_sid)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    agent_name = existing.agent_name or "unknown-caller"
    device_id = existing.device_id or "unknown-device"
    realtime_cfg = settings.get_agent_realtime_config(agent_name)
    agent_cfg = get_agent_config(agent_name)

    model = str(realtime_cfg.get("model") or existing.model or "").strip()
    voice = str(realtime_cfg.get("voice") or existing.voice or "").strip()
    if not model or not voice:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Realtime configuration incomplete")

    server_vad = realtime_cfg.get("server_vad")
    if not isinstance(server_vad, dict):
        server_vad = existing.metadata.get("server_vad") if isinstance(existing.metadata, dict) else {}
        if not isinstance(server_vad, dict):
            server_vad = {}

    metadata = dict(existing.metadata) if isinstance(existing.metadata, dict) else {}
    metadata["server_vad"] = copy.deepcopy(server_vad)

    instructions = agent_cfg.get("realtime_instructions")
    if isinstance(instructions, str):
        instructions = instructions.strip() or None
    else:
        instructions = None

    websocket_token = uuid4().hex

    new_session = await create_realtime_session(
        agent_name=agent_name,
        model=model,
        voice=voice,
        device_id=device_id,
        instructions=instructions,
        metadata=metadata,
    )
    new_session.call_id = call_sid
    new_session.mobile_token = websocket_token
    new_session.agent_name = agent_name
    new_session.device_id = device_id

    store.replace_session(call_sid, new_session)

    agent_cfg = get_agent_config(agent_name)
    agent_snapshot = copy.deepcopy(agent_cfg)
    agent_snapshot = copy.deepcopy(agent_cfg)
    extras: Dict[str, Any] = {
        "realtime_session_id": new_session.session_id,
        "realtime_model": new_session.model,
        "realtime_voice": new_session.voice,
        "device_id": device_id,
        "mobile_token": websocket_token,
        "transport": "android-realtime",
        "server_vad": copy.deepcopy(server_vad),
    }

    caller_label = metadata.get("device_label") or device_id
    store_call(call_sid, (agent_name, agent_snapshot, None, False, caller_label, extras))

    logger.info(
        json.dumps(
            {
                "severity": "INFO",
                "event": "mobile_realtime_session_refreshed",
                "session_id": new_session.session_id,
                "call_sid": call_sid,
                "device_id": device_id,
                "agent": agent_name,
                "model": new_session.model,
                "voice": new_session.voice,
                "server_vad": server_vad,
            }
        )
    )

    payload = MobileRealtimeSessionResponse(
        call_sid=call_sid,
        session_id=new_session.session_id,
        client_secret=new_session.client_secret,
        websocket_url=new_session.websocket_url,
        expires_at=new_session.expires_at,
        model=new_session.model,
        voice=new_session.voice,
        websocket_token=websocket_token,
        server_vad=copy.deepcopy(server_vad),
    )
    return JSONResponse(
        content=payload.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=False,
            exclude_unset=False,
        )
    )



def _get_managed_client() -> ManagedAVClient:
    """Return a cached client for the managed A/V provider."""

    global _managed_client
    if _managed_client is not None:
        return _managed_client

    cfg = settings.get_mobile_managed_av_config()
    api_key = os.getenv(PIPECAT_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(f"{PIPECAT_API_KEY_ENV} must be configured")

    metadata = cfg.get("metadata")
    _managed_client = ManagedAVClient(
        base_url=str(cfg.get("api_base_url")),
        api_key=api_key,
        agent_name=str(cfg.get("agent_name")),
        session_ttl_seconds=int(cfg.get("session_ttl_seconds", 600)),
        metadata=metadata if isinstance(metadata, dict) else {},
    )
    return _managed_client


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
    return result


def _sanitise_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(config or {})


@router.post("/devices/register", response_model=MobileRegisterResponse)
async def register_device(payload: MobileRegisterRequest) -> MobileRegisterResponse:
    """Register the device and return approval status."""

    device_id = payload.device_id.strip()
    if not device_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid deviceId")

    if not os.getenv(PIPECAT_API_KEY_ENV):
        logger.error("%s missing while registering device %s", PIPECAT_API_KEY_ENV, device_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{MANAGED_AV_API_KEY_ENV} is required for voice calls.",
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


@router.post("/voice/session", response_model=MobileVoiceSessionResponse)
async def voice_session(payload: MobileVoiceSessionRequest) -> MobileVoiceSessionResponse:
    """Create a managed audio/video session for the mobile client."""

    device_id = payload.device_id.strip()
    if not device_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid deviceId")

    device_cfg = settings.get_mobile_device(device_id)
    if not device_cfg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown device")

    device_cfg = _normalise_device_entry(device_cfg)
    if not device_cfg.get("enabled"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device not approved")

    configured_agent = device_cfg.get("agent")
    if payload.agent and payload.agent != configured_agent:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent mismatch")

    agent_name = configured_agent or payload.agent
    if not agent_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent not specified")

    try:
        agent_cfg = settings.get_agent_config(agent_name)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent '{agent_name}' not found",
        ) from exc

    greeting = _resolve_greeting(agent_cfg)

    try:
        client = _get_managed_client()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to initialise managed A/V client: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Managed audio/video provider not configured",
        ) from exc

    metadata = {
        "device": {
            "label": device_cfg.get("label"),
            "notes": device_cfg.get("notes"),
            "context": device_cfg.get("context"),
        }
    }

    try:
        managed_session = await client.start_session(
            device_id=device_id,
            agent_name=agent_name,
            greeting=greeting,
            device_metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to start managed A/V session for device %s: %s", device_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to initialise managed audio/video session",
        ) from exc

    await _session_store.create_session(
        session_id=managed_session.session_id,
        device_id=device_id,
        agent_name=agent_name,
        agent_config=_sanitise_config(agent_cfg),
        greeting=managed_session.greeting or greeting,
        expires_at=managed_session.expires_at,
        ttl_seconds=settings.get_mobile_managed_av_config().get("session_ttl_seconds"),
        metadata=managed_session.metadata,
    )

    return MobileVoiceSessionResponse(
        session_id=managed_session.session_id,
        agent=managed_session.agent,
        room_url=managed_session.room_url,
        access_token=managed_session.access_token,
        expires_at=managed_session.expires_at,
        pipeline_session_id=managed_session.pipeline_session_id,
        greeting=managed_session.greeting or greeting,
        metadata=managed_session.metadata,
    )


@router.post("/managed-av/completions", response_model=ManagedAVCompletionResponse)
async def managed_av_completions(payload: ManagedAVCompletionRequest) -> ManagedAVCompletionResponse:
    """Process a transcript chunk from the managed pipeline and return the next response."""

    session = await _session_store.get_session(payload.session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    now = datetime.now(timezone.utc)
    if session.expires_at <= now:
        await _session_store.delete_session(payload.session_id)
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Session expired")

    user_text = payload.text.strip()
    if not user_text:
        return ManagedAVCompletionResponse(response_text="", hold_text=None, reset=False)

    async with session.lock:
        session.messages.append({"role": "user", "content": user_text})
        await asyncio.to_thread(log_turn, "user", user_text, source=ANDROID_MANAGED_SOURCE)

        response_text, hold_text, reset_requested = await _generate_response(
            session.agent_config,
            session.messages,
            user_text,
        )

        if reset_requested:
            # After resetting, ensure the system prompt is re-applied.
            session.messages = []
            prompt = session.agent_config.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                session.messages.append({"role": "system", "content": prompt.strip()})

        if response_text:
            session.messages.append({"role": "assistant", "content": response_text})
            await asyncio.to_thread(
                log_turn,
                "assistant",
                response_text,
                source=ANDROID_MANAGED_SOURCE,
            )

    return ManagedAVCompletionResponse(
        response_text=response_text,
        hold_text=hold_text,
        reset=reset_requested,
    )


@router.delete("/managed-av/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_managed_session(session_id: str) -> Response:
    """Dispose of conversation state when the managed pipeline ends a session."""

    await _session_store.delete_session(session_id)

    try:
        client = _get_managed_client()
    except Exception:  # noqa: BLE001
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await client.close_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _generate_response(
    agent_cfg: Dict[str, Any],
    messages: list[Dict[str, Any]],
    user_text: str,
) -> tuple[str, Optional[str], bool]:
    """Stream a response from the agent, capturing hold/reset markers."""

    responses: list[str] = []
    hold_text: Optional[str] = None
    reset_requested = False

    async for chunk in stream_response(user_text, agent_cfg, messages):
        if isinstance(chunk, dict):
            marker_type = chunk.get("type")
            if marker_type == "tool_executing" and hold_text is None:
                hold_text = "Give me a moment while I work on that."
            elif marker_type == "reset_conversation":
                reset_requested = True
                reset_message = chunk.get("message") or "Conversation reset."
                responses = [reset_message]
                break
            continue

        responses.append(chunk)

    response_text = "".join(responses).strip()
    return response_text, hold_text, reset_requested


__all__ = ["router"]
