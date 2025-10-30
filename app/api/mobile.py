"""Mobile client endpoints for device registration and managed A/V sessions."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import base64
import binascii
import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app import settings
from app.chat import stream_response
from app.logging_utils import logger
from app.managed_av.client import ManagedAVClient, ManagedAVSession
from app.managed_av.session_store import ManagedAVSessionStore, ManagedSessionState, get_session_store
from app.mobile.config_store import ensure_device_entry
from app.memory import log_turn
from app.settings import get_agent_config

DEFAULT_POLL_AFTER_SECONDS = 5
PIPECAT_API_KEY_ENV = "PIPECAT_API_KEY"
CONTROL_TOKEN_ENV = "MANAGED_AV_CONTROL_TOKEN"
CONTROL_AUTH_HEADER = "X-Ringdown-Control-Token"
CONTROL_KEY_HEADER = "X-Ringdown-Control-Key"
MAX_CONTROL_AUDIO_BYTES = 2_097_152  # 2 MiB
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


class ManagedAVControlPayload(BaseModel):
    """Audio payload delivered to the handset for deterministic playback."""

    model_config = ConfigDict(populate_by_name=True)

    prompt_id: str = Field(..., alias="promptId", min_length=1, max_length=128)
    audio_base64: str = Field(..., alias="audioBase64", min_length=8)
    sample_rate_hz: int = Field(..., alias="sampleRateHz", ge=8000, le=48000)
    channels: int = Field(default=1, ge=1, le=2)
    format: str = Field(default="pcm16", pattern="^[A-Za-z0-9_-]{3,16}$")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ManagedAVControlEnvelope(ManagedAVControlPayload):
    """Control payload plus bookkeeping fields returned to the handset."""

    message_id: str = Field(..., alias="messageId", min_length=6, max_length=64)
    enqueued_at: datetime = Field(..., alias="enqueuedAt")


class ManagedAVControlEnqueueRequest(BaseModel):
    """Harness request for queueing an audio prompt."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(..., alias="sessionId")
    message: ManagedAVControlPayload


class ManagedAVControlEnqueueResponse(BaseModel):
    """Acknowledgement that the control payload was queued."""

    model_config = ConfigDict(populate_by_name=True)

    queued: bool = Field(default=True)
    message_id: str = Field(..., alias="messageId")


class ManagedAVControlFetchRequest(BaseModel):
    """Handset poll request for the next control payload."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(..., alias="sessionId")


class ManagedAVControlFetchResponse(BaseModel):
    """Handset response containing the next control payload, if any."""

    model_config = ConfigDict(populate_by_name=True)

    message: Optional[ManagedAVControlEnvelope] = None


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


def _require_control_auth(request: Request) -> None:
    """Ensure the caller is authorised to enqueue control messages."""

    shared_token = os.getenv(CONTROL_TOKEN_ENV)
    if not shared_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Managed control channel not configured",
        )

    expected = shared_token.strip()
    provided = (request.headers.get(CONTROL_AUTH_HEADER) or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid control token",
        )


def _extract_control_key(session: ManagedSessionState) -> Optional[str]:
    """Return the per-session control key if configured."""

    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    control = metadata.get("control")
    if isinstance(control, dict):
        key = control.get("key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    return None


def _validate_control_key(request: Request, session: ManagedSessionState) -> None:
    """Verify the handset control key supplied by the caller matches the session."""

    expected = _extract_control_key(session)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Control channel not enabled for this session",
        )

    provided = (request.headers.get(CONTROL_KEY_HEADER) or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid control key",
        )


def _validate_control_payload(payload: ManagedAVControlPayload) -> None:
    """Validate the audio payload bounds and encoding."""

    try:
        audio_bytes = base64.b64decode(payload.audio_base64, validate=True)
    except (ValueError, binascii.Error):  # type: ignore[name-defined]
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audioBase64 is not valid base64") from None

    if not audio_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audioBase64 payload empty")
    if len(audio_bytes) > MAX_CONTROL_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="audioBase64 exceeds maximum supported size",
        )


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

    realtime_cfg = settings.get_agent_realtime_config(agent_name)
    server_vad = realtime_cfg.get("server_vad") if isinstance(realtime_cfg, dict) else {}

    device_metadata = {
        "device": {
            "label": device_cfg.get("label"),
            "notes": device_cfg.get("notes"),
            "context": device_cfg.get("context"),
        }
    }
    session_context = {
        "realtime": {
            "model": realtime_cfg.get("model") if isinstance(realtime_cfg, dict) else None,
            "voice": realtime_cfg.get("voice") if isinstance(realtime_cfg, dict) else None,
            "server_vad": server_vad if isinstance(server_vad, dict) else {},
        },
    }
    control_key: Optional[str] = None
    if os.getenv(CONTROL_TOKEN_ENV):
        control_key = secrets.token_urlsafe(24)
        session_context["testing"] = {
            **session_context.get("testing", {}),
            "control_enabled": True,
        }

    try:
        managed_session = await client.start_session(
            device_id=device_id,
            agent_name=agent_name,
            greeting=greeting,
            device_metadata=device_metadata,
            session_metadata=session_context,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to start managed A/V session for device %s: %s", device_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to initialise managed audio/video session",
        ) from exc

    session_metadata = managed_session.metadata if isinstance(managed_session.metadata, dict) else {}
    if managed_session.pipeline_session_id and "pipeline_session_id" not in session_metadata:
        session_metadata = dict(session_metadata)
        session_metadata["pipeline_session_id"] = managed_session.pipeline_session_id
    if control_key:
        session_metadata = dict(session_metadata)
        control_entry = dict(session_metadata.get("control") or {})
        control_entry.update(
            {
                "key": control_key,
                "pollPath": "/v1/mobile/managed-av/control/next",
            }
        )
        session_metadata["control"] = control_entry

    await _session_store.create_session(
        session_id=managed_session.session_id,
        device_id=device_id,
        agent_name=agent_name,
        agent_config=_sanitise_config(agent_cfg),
        greeting=managed_session.greeting or greeting,
        expires_at=managed_session.expires_at,
        ttl_seconds=settings.get_mobile_managed_av_config().get("session_ttl_seconds"),
        metadata=session_metadata,
    )

    managed_session.metadata = session_metadata

    log_payload: Dict[str, Any] = {
        "severity": "INFO",
        "event": "mobile_managed_session_started",
        "session_id": managed_session.session_id,
        "pipeline_session_id": managed_session.pipeline_session_id,
        "device_id": device_id,
        "agent": agent_name,
        "expires_at": managed_session.expires_at.isoformat(),
    }
    if session_metadata:
        log_payload["metadata"] = session_metadata

    logger.info(json.dumps(log_payload))

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

    metadata: Dict[str, Any] = session.metadata if isinstance(session.metadata, dict) else {}
    pipeline_session_id = metadata.get("pipeline_session_id") if isinstance(metadata, dict) else None
    log_payload = {
        "severity": "INFO",
        "event": "mobile_managed_completion",
        "session_id": session.session_id,
        "pipeline_session_id": pipeline_session_id or session.session_id,
        "device_id": session.device_id,
        "agent": session.agent_name,
        "final": payload.final,
        "reset": reset_requested,
        "user_char_count": len(user_text),
        "response_char_count": len(response_text),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        log_payload["metadata"] = metadata

    logger.info(json.dumps(log_payload))

    return ManagedAVCompletionResponse(
        response_text=response_text,
        hold_text=hold_text,
        reset=reset_requested,
    )


@router.post("/managed-av/control", response_model=ManagedAVControlEnqueueResponse, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_control_message(
    request: Request,
    payload: ManagedAVControlEnqueueRequest,
) -> ManagedAVControlEnqueueResponse:
    """Queue an audio payload for the handset control channel."""

    _require_control_auth(request)
    session = await _session_store.get_session(payload.session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    _validate_control_key(request, session)
    _validate_control_payload(payload.message)

    envelope = payload.message.model_dump(by_alias=True)
    message_id = secrets.token_urlsafe(16)
    envelope["messageId"] = message_id
    envelope["enqueuedAt"] = datetime.now(timezone.utc).isoformat()

    await _session_store.enqueue_control_message(payload.session_id, envelope)
    return ManagedAVControlEnqueueResponse(message_id=message_id)


@router.post("/managed-av/control/next", response_model=ManagedAVControlFetchResponse)
async def fetch_control_message(
    request: Request,
    payload: ManagedAVControlFetchRequest,
) -> ManagedAVControlFetchResponse:
    """Return the next queued control payload (if any) for the handset."""

    session = await _session_store.get_session(payload.session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    _validate_control_key(request, session)

    message = await _session_store.dequeue_control_message(payload.session_id)
    if message is None:
        return ManagedAVControlFetchResponse(message=None)

    try:
        envelope = ManagedAVControlEnvelope.model_validate(message)
    except Exception as exc:  # noqa: BLE001
        logger.error("Invalid control message payload for session %s: %s", payload.session_id, exc)
        return ManagedAVControlFetchResponse(message=None)

    return ManagedAVControlFetchResponse(message=envelope)


@router.delete("/managed-av/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_managed_session(session_id: str) -> Response:
    """Dispose of conversation state when the managed pipeline ends a session."""

    session = await _session_store.get_session(session_id)
    metadata: Dict[str, Any] = {}
    device_id: Optional[str] = None
    agent_name: Optional[str] = None
    if session is not None:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        device_id = session.device_id
        agent_name = session.agent_name

    await _session_store.delete_session(session_id)

    log_payload = {
        "severity": "INFO",
        "event": "mobile_managed_session_closed",
        "session_id": session_id,
        "pipeline_session_id": (metadata.get("pipeline_session_id") if isinstance(metadata, dict) else None)
        or session_id,
        "device_id": device_id,
        "agent": agent_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        log_payload["metadata"] = metadata
    logger.info(json.dumps(log_payload))

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
