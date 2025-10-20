"""Mobile client endpoints for device registration and WebRTC signaling."""

from __future__ import annotations

import asyncio
import json
import io
import os
from contextlib import suppress
from fractions import Fraction
from functools import lru_cache
from typing import Any, Dict, Literal, Optional

import httpx
from aiortc import RTCPeerConnection, RTCIceCandidate, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack
from av import AudioFrame, open as av_open
from av.audio.resampler import AudioResampler
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field

from app import settings
from app.logging_utils import logger
from app.mobile.config_store import ensure_device_entry

SAMPLE_RATE = 48_000
FRAME_DURATION_SEC = 0.02  # 20ms
SAMPLES_PER_FRAME = int(SAMPLE_RATE * FRAME_DURATION_SEC)
TTS_ENDPOINT = "https://api.openai.com/v1/audio/speech"
DEFAULT_TTS_MODEL = "tts-1"
DEFAULT_TTS_VOICE = "alloy"
HTTP_TIMEOUT = 60.0

DEFAULT_POLL_AFTER_SECONDS = 5

router = APIRouter(prefix="/v1/mobile", tags=["mobile"])
ws_router = APIRouter()


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

    status: Literal["PENDING", "APPROVED", "DENIED"]
    message: str
    poll_after_seconds: Optional[int] = Field(default=None, alias="pollAfterSeconds")
    agent: Optional[str] = None


class QueuedAudioStreamTrack(AudioStreamTrack):
    """Outbound audio track that plays queued audio frames."""

    kind = "audio"

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        super().__init__()
        self._sample_rate = sample_rate
        self._queue: asyncio.Queue[AudioFrame] = asyncio.Queue()
        self._timestamp = 0
        self._closing = False

    async def recv(self) -> AudioFrame:
        try:
            frame = await asyncio.wait_for(self._queue.get(), timeout=FRAME_DURATION_SEC)
        except asyncio.TimeoutError:
            frame = AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
            frame.pts = self._timestamp
            frame.sample_rate = self._sample_rate
            frame.time_base = Fraction(1, self._sample_rate)
            for plane in frame.planes:
                plane.update(b"\x00" * len(plane))
            self._timestamp += SAMPLES_PER_FRAME
            return frame

        frame.pts = self._timestamp
        frame.sample_rate = self._sample_rate
        frame.time_base = Fraction(1, self._sample_rate)
        self._timestamp += frame.samples
        return frame

    def enqueue(self, frame: AudioFrame) -> None:
        self._queue.put_nowait(frame)

    def close(self) -> None:
        self._closing = True


async def _play_initial_prompt(track: QueuedAudioStreamTrack, device_cfg: Dict[str, Any]) -> None:
    agent_name = device_cfg.get("agent") or "unknown-caller"
    try:
        agent_cfg = settings.get_agent_config(agent_name)
    except KeyError:
        agent_cfg = {}

    greeting = agent_cfg.get("welcome_greeting") or "You are connected to the Ringdown assistant."
    voice = agent_cfg.get("voice") or DEFAULT_TTS_VOICE

    frames = await _synthesize_speech_frames(greeting, voice)

    for frame in frames:
        track.enqueue(frame)
    track.close()


async def _synthesize_speech_frames(text: str, voice: str) -> list[AudioFrame]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")

    payload = {
        "model": os.getenv("VOICE_TTS_MODEL", DEFAULT_TTS_MODEL),
        "input": text,
        "voice": voice,
        "format": "wav",
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(TTS_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()

    return _decode_audio(response.content)


def _decode_audio(data: bytes) -> list[AudioFrame]:
    container = av_open(io.BytesIO(data))
    try:
        stream = next(s for s in container.streams if s.type == "audio")
    except StopIteration as exc:  # pragma: no cover - defensive
        container.close()
        raise ValueError("No audio stream in synthesized output") from exc

    resampler = AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    frames: list[AudioFrame] = []
    for frame in container.decode(stream):
        resampled_frames = resampler.resample(frame)
        if resampled_frames is None:
            continue
        for resampled in resampled_frames:
            resampled.pts = None
            resampled.sample_rate = SAMPLE_RATE
            resampled.time_base = Fraction(1, SAMPLE_RATE)
            frames.append(resampled)

    container.close()
    if not frames:
        raise ValueError("Synthesized audio did not produce any frames")
    return frames


def _normalise_device_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of *entry* with snake_case keys where appropriate."""

    result: Dict[str, Any] = dict(entry or {})
    # Harmonise possible camelCase keys saved from earlier versions.
    if "pollAfterSeconds" in result and "poll_after_seconds" not in result:
        result["poll_after_seconds"] = result["pollAfterSeconds"]
    if "blockedReason" in result and "blocked_reason" not in result:
        result["blocked_reason"] = result["blockedReason"]
    return result


@router.post("/devices/register", response_model=MobileRegisterResponse)
async def register_device(payload: MobileRegisterRequest) -> MobileRegisterResponse:
    """Register the device and return approval status."""

    device_id = payload.device_id.strip()
    if not device_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid deviceId")

    if not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY missing while registering device %s", device_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OPENAI_API_KEY is required for voice calls.",
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


async def _drain_inbound_audio(track, device_id: str) -> None:
    """Consume inbound audio frames to keep the receiver alive."""

    try:
        while True:
            await track.recv()
    except Exception:
        logger.debug("Audio track for device %s closed", device_id)


def _serialize_candidate(candidate: RTCIceCandidate) -> Dict[str, Any]:
    return {
        "candidate": candidate.candidate,
        "sdpMid": candidate.sdpMid,
        "sdpMLineIndex": candidate.sdpMLineIndex,
    }


@ws_router.websocket("/ws/mobile/voice")
async def voice_signaling(websocket: WebSocket) -> None:
    """Bidirectional signaling channel for WebRTC voice sessions."""

    device_id = websocket.query_params.get("device_id")
    if not device_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    device_cfg = settings.get_mobile_device(device_id)
    if not device_cfg or not device_cfg.get("enabled"):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    if not os.getenv("OPENAI_API_KEY"):
        await websocket.accept()
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="OPENAI_API_KEY missing")
        logger.error("OPENAI_API_KEY missing during voice signaling for %s", device_id)
        return

    await websocket.accept()

    peer_connection = RTCPeerConnection()
    audio_track = QueuedAudioStreamTrack()
    peer_connection.addTrack(audio_track)

    @peer_connection.on("icecandidate")
    def _on_icecandidate(candidate: Optional[RTCIceCandidate]) -> None:
        if candidate is None:
            return
        payload = {
            "type": "candidate",
            "candidate": _serialize_candidate(candidate),
        }
        asyncio.create_task(websocket.send_text(json.dumps(payload)))

    @peer_connection.on("track")
    def _on_track(track) -> None:  # noqa: ANN001 - aiortc callback signature
        if track.kind == "audio":
            logger.debug("Received audio track from %s", device_id)
            asyncio.create_task(_drain_inbound_audio(track, device_id))

    play_task: Optional[asyncio.Task[None]] = None

    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                logger.warning("Dropping malformed signaling payload from %s: %s", device_id, data)
                continue

            mtype = message.get("type")
            if mtype == "offer":
                sdp = message.get("sdp")
                if not isinstance(sdp, str):
                    raise ValueError("offer missing sdp")
                offer = RTCSessionDescription(sdp=sdp, type="offer")
                await peer_connection.setRemoteDescription(offer)
                answer = await peer_connection.createAnswer()
                await peer_connection.setLocalDescription(answer)
                response = {
                    "type": "answer",
                    "sdp": peer_connection.localDescription.sdp,
                }
                await websocket.send_text(json.dumps(response))
                if play_task is None:
                    play_task = asyncio.create_task(_play_initial_prompt(audio_track, device_cfg))
            elif mtype == "candidate":
                candidate_payload = message.get("candidate") or {}
                candidate_str = candidate_payload.get("candidate")
                if candidate_str:
                    index = candidate_payload.get("sdpMLineIndex")
                    if isinstance(index, str) and index.isdigit():
                        index = int(index)
                    rtc_candidate = RTCIceCandidate(
                        sdpMid=candidate_payload.get("sdpMid"),
                        sdpMLineIndex=index,
                        candidate=candidate_str,
                    )
                    await peer_connection.addIceCandidate(rtc_candidate)
            elif mtype == "bye":
                await websocket.close()
                break
            else:
                logger.debug("Unhandled signaling message type %s from %s", mtype, device_id)
    except WebSocketDisconnect:
        logger.info("Voice signaling socket for %s closed", device_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error in voice signaling handler for %s: %s", device_id, exc)
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    finally:
        if play_task is not None:
            play_task.cancel()
            with suppress(Exception):
                await play_task
        audio_track.close()
        await peer_connection.close()


__all__ = ["router", "ws_router"]
