"""Mobile client endpoints for device registration and WebRTC signaling."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from contextlib import suppress
from fractions import Fraction
from typing import Any, Dict, Literal, Optional

import httpx

from aiortc import (
    RTCPeerConnection,
    RTCConfiguration,
    RTCIceCandidate,
    RTCIceServer,
    RTCSessionDescription,
)
from aiortc.mediastreams import AudioStreamTrack
from aiortc.sdp import candidate_from_sdp
from av import AudioFrame
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app import settings
from app.chat import stream_response
from app.logging_utils import logger
from app.mobile.config_store import ensure_device_entry
from app.speech.providers import SpeechProvider, get_speech_provider

SAMPLE_RATE = 48_000
FRAME_DURATION_SEC = 0.02  # 20ms
SAMPLES_PER_FRAME = int(SAMPLE_RATE * FRAME_DURATION_SEC)
DEFAULT_TTS_VOICE = "alloy"

DEFAULT_POLL_AFTER_SECONDS = 5
MIN_UTTERANCE_DURATION_SEC = 0.5
MAX_UTTERANCE_DURATION_SEC = 12.0
SILENCE_DURATION_SEC = 0.6
ENERGY_THRESHOLD = 900.0

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

    def __init__(self, sample_rate: int = SAMPLE_RATE, device_id: str | None = None) -> None:
        super().__init__()
        self._sample_rate = sample_rate
        self._queue: asyncio.Queue[AudioFrame] = asyncio.Queue()
        self._timestamp = 0
        self._closing = False
        self._device_id = device_id or "unknown-device"

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
            logger.debug("QueuedAudioStreamTrack(%s) sending silence frame", self._device_id)
            return frame

        frame.pts = self._timestamp
        frame.sample_rate = self._sample_rate
        frame.time_base = Fraction(1, self._sample_rate)
        self._timestamp += frame.samples
        logger.debug(
            "QueuedAudioStreamTrack(%s) dequeued frame samples=%d queue_size=%d",
            self._device_id,
            frame.samples,
            self._queue.qsize(),
        )
        return frame

    def enqueue(self, frame: AudioFrame) -> None:
        self._queue.put_nowait(frame)
        logger.debug(
            "QueuedAudioStreamTrack(%s) enqueued frame samples=%d queue_size=%d",
            self._device_id,
            frame.samples,
            self._queue.qsize(),
        )

    def close(self) -> None:
        self._closing = True


class MobileVoiceSession:
    """Manage a mobile voice session with transcription, LLM, and TTS."""

    def __init__(
        self,
        device_id: str,
        device_cfg: Dict[str, Any],
        outbound_track: QueuedAudioStreamTrack,
        *,
        speech_provider: SpeechProvider | None = None,
    ) -> None:
        self.device_id = device_id
        self.device_cfg = device_cfg
        self._outbound_track = outbound_track
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._active = True
        self._silence_samples = int(SILENCE_DURATION_SEC * SAMPLE_RATE)
        self._min_samples = int(MIN_UTTERANCE_DURATION_SEC * SAMPLE_RATE)
        self._max_samples = int(MAX_UTTERANCE_DURATION_SEC * SAMPLE_RATE)

        agent_name = device_cfg.get("agent") or "unknown-caller"
        try:
            agent_cfg = settings.get_agent_config(agent_name)
        except KeyError:
            logger.warning(
                "Agent %s not found for device %s; falling back to unknown-caller",
                agent_name,
                device_id,
            )
            agent_name = "unknown-caller"
            agent_cfg = settings.get_agent_config(agent_name)

        self.agent_name = agent_name
        self.agent_cfg = copy.deepcopy(agent_cfg)
        prompt = self.agent_cfg.get("prompt", "")
        self.messages: list[dict[str, Any]] = []
        if prompt:
            self.messages.append({"role": "system", "content": prompt})

        self._voice = self.agent_cfg.get("voice") or DEFAULT_TTS_VOICE
        self._language = self.agent_cfg.get("language") or "en-US"
        self._speech_model = self.agent_cfg.get("speech_model") or None
        self._speech_provider = speech_provider or get_speech_provider(self.agent_cfg)

    async def start(self) -> None:
        """Play the configured greeting at session start."""

        greeting = self._resolve_greeting()
        if not greeting:
            return

        await self._speak(greeting)
        self.messages.append({"role": "assistant", "content": greeting})

    def _resolve_greeting(self) -> str | None:
        candidate = self.agent_cfg.get("welcome_greeting")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        return "You are connected to the Ringdown assistant."

    def attach_incoming_track(self, track: AudioStreamTrack) -> None:
        """Start consuming audio from the handset microphone."""

        task = asyncio.create_task(self._consume_track(track))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        """Cancel background tasks and release resources."""

        self._active = False
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

        self._outbound_track.close()

    async def _consume_track(self, track: AudioStreamTrack) -> None:
        """Segment incoming audio into utterances and dispatch for processing."""

        buffer = bytearray()
        speaking = False
        speech_samples = 0
        silence_samples = 0

        try:
            while self._active:
                try:
                    frame = await track.recv()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Audio track for %s closed: %s", self.device_id, exc)
                    break

                samples = frame.to_ndarray(format="s16")
                if samples.ndim == 2:
                    mono = samples[0]
                else:
                    mono = samples
                mono = np.asarray(mono, dtype=np.int16)

                amplitude = float(np.abs(mono).mean())
                pcm_chunk = mono.tobytes()

                if not speaking:
                    if amplitude < ENERGY_THRESHOLD:
                        continue
                    speaking = True
                    speech_samples = len(mono)
                    buffer.extend(pcm_chunk)
                    silence_samples = 0
                    continue

                buffer.extend(pcm_chunk)
                speech_samples += len(mono)

                if amplitude < ENERGY_THRESHOLD:
                    silence_samples += len(mono)
                else:
                    silence_samples = 0

                should_flush = False
                if silence_samples >= self._silence_samples:
                    should_flush = True
                elif speech_samples >= self._max_samples:
                    should_flush = True

                if should_flush:
                    chunk = bytes(buffer)
                    buffer.clear()
                    speaking = False
                    speech_samples = 0
                    silence_samples = 0
                    if chunk and len(chunk) >= self._min_samples * 2:
                        self._schedule_chunk(chunk)

        finally:
            if buffer and len(buffer) >= self._min_samples * 2:
                self._schedule_chunk(bytes(buffer))

    def _schedule_chunk(self, pcm_bytes: bytes) -> None:
        if not self._active or not pcm_bytes:
            return
        task = asyncio.create_task(self._process_chunk(pcm_bytes))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _process_chunk(self, pcm_bytes: bytes) -> None:
        try:
            async with self._lock:
                transcript = await self._transcribe(pcm_bytes)
                if not transcript:
                    return

                logger.info("Mobile user %s said: %s", self.device_id, transcript)
                logger.debug(
                    "Mobile transcript length=%d chars (chunk %d bytes)",
                    len(transcript),
                    len(pcm_bytes),
                )
                self.messages.append({"role": "user", "content": transcript})

                response_text = await self._generate_response(transcript)
                if not response_text:
                    return

                self.messages.append({"role": "assistant", "content": response_text})
                await self._speak(response_text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error processing mobile audio chunk: %s", exc)

    async def _transcribe(self, pcm_bytes: bytes) -> str:
        if not pcm_bytes:
            return ""

        return await self._speech_provider.transcribe(
            pcm_bytes,
            SAMPLE_RATE,
            language=self._language,
            model=self._speech_model,
        )

    async def _generate_response(self, user_text: str) -> str:
        responses: list[str] = []
        tool_announced = False

        async for chunk in stream_response(user_text, self.agent_cfg, self.messages):
            if isinstance(chunk, dict):
                marker_type = chunk.get("type")
                if marker_type == "tool_executing" and not tool_announced:
                    tool_announced = True
                    await self._speak("Give me a moment while I work on that.")
                elif marker_type == "reset_conversation":
                    reset_message = chunk.get("message") or "Conversation reset."
                    self.messages = []
                    prompt = self.agent_cfg.get("prompt", "")
                    if prompt:
                        self.messages.append({"role": "system", "content": prompt})
                    return reset_message
                continue

            responses.append(chunk)

        return "".join(responses).strip()

    async def _speak(self, text: str) -> None:
        if not text or not self._active:
            return

        try:
            frames = await self._speech_provider.synthesize(
                text,
                voice=self._voice,
                prosody=self.agent_cfg.get("tts_prosody"),
                language=self._language,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("TTS synthesis failed for device %s: %s", self.device_id, exc)
            return

        if not frames:
            logger.warning(
                "Synthesized 0 frames for device %s (text length=%d)",
                self.device_id,
                len(text),
            )
            return

        total_samples = 0
        for frame in frames:
            samples = getattr(frame, "samples", 0) or 0
            total_samples += samples
            self._outbound_track.enqueue(frame)

        duration = total_samples / SAMPLE_RATE if total_samples else 0.0
        logger.debug(
            "Synthesized %d frames (%.2fs) for device %s",
            len(frames),
            duration,
            self.device_id,
        )


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


def _serialize_candidate(candidate: RTCIceCandidate) -> Dict[str, Any]:
    return {
        "candidate": candidate.candidate,
        "sdpMid": candidate.sdpMid,
        "sdpMLineIndex": candidate.sdpMLineIndex,
    }


async def _fetch_ice_servers() -> list[dict[str, Any]]:
    """Return TURN/STUN server definitions for this session."""

    env = settings.get_env()
    account_sid = env.twilio_account_sid
    auth_token = env.twilio_auth_token

    ice_servers: list[dict[str, Any]] = [
        {"urls": "stun:stun.l.google.com:19302"},
    ]

    if not account_sid:
        return ice_servers

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Tokens.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, auth=(account_sid, auth_token))
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unable to fetch Twilio ICE servers: %s", exc)
        return ice_servers

    remote_servers = payload.get("ice_servers") or []
    for server in remote_servers:
        urls = server.get("urls")
        if not urls:
            continue
        ice_servers.append(
            {
                "urls": urls,
                "username": server.get("username"),
                "credential": server.get("credential"),
            }
        )

    return ice_servers


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

    await websocket.accept()

    ice_server_payload = await _fetch_ice_servers()
    rtc_ice_servers: list[RTCIceServer] = []
    for entry in ice_server_payload:
        urls = entry.get("urls")
        if isinstance(urls, str):
            urls = [urls]
        if not urls:
            continue
        username = entry.get("username")
        credential = entry.get("credential")
        rtc_ice_servers.append(
            RTCIceServer(urls=urls, username=username, credential=credential)
        )

    peer_connection = RTCPeerConnection(
        configuration=RTCConfiguration(iceServers=rtc_ice_servers)
    )
    audio_track = QueuedAudioStreamTrack(device_id=device_id)
    peer_connection.addTrack(audio_track)
    session = MobileVoiceSession(device_id, device_cfg, audio_track)

    if ice_server_payload:
        logger.debug(
            "Sending %d ICE server entries to %s", len(ice_server_payload), device_id
        )
        await websocket.send_text(
            json.dumps({"type": "iceServers", "iceServers": ice_server_payload})
        )

    @peer_connection.on("iceconnectionstatechange")
    def _on_ice_state_change() -> None:
        logger.debug(
            "ICE connection state for %s -> %s",
            device_id,
            peer_connection.iceConnectionState,
        )

    @peer_connection.on("connectionstatechange")
    def _on_connection_state_change() -> None:
        logger.debug(
            "Peer connection state for %s -> %s",
            device_id,
            peer_connection.connectionState,
        )

    @peer_connection.on("icecandidate")
    def _on_icecandidate(candidate: Optional[RTCIceCandidate]) -> None:
        if candidate is None:
            return
        payload = {
            "type": "candidate",
            "candidate": _serialize_candidate(candidate),
        }
        asyncio.create_task(websocket.send_text(json.dumps(payload)))

    inbound_started = False

    @peer_connection.on("track")
    def _on_track(track) -> None:  # noqa: ANN001 - aiortc callback signature
        nonlocal inbound_started
        if track.kind == "audio" and not inbound_started:
            inbound_started = True
            logger.debug("Received audio track from %s", device_id)
            session.attach_incoming_track(track)

    greeting_started = False

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
                if not greeting_started:
                    greeting_started = True
                    await session.start()
            elif mtype == "candidate":
                candidate_payload = message.get("candidate") or {}
                candidate_str = candidate_payload.get("candidate")
                if candidate_str:
                    index = candidate_payload.get("sdpMLineIndex")
                    if isinstance(index, str) and index.isdigit():
                        index = int(index)
                    try:
                        rtc_candidate = candidate_from_sdp(candidate_str)
                    except Exception:  # pragma: no cover - defensive logging
                        logger.warning(
                            "Dropping malformed ICE candidate from %s: %s",
                            device_id,
                            candidate_str,
                            exc_info=True,
                        )
                        continue
                    rtc_candidate.sdpMid = candidate_payload.get("sdpMid")
                    rtc_candidate.sdpMLineIndex = index
                    await peer_connection.addIceCandidate(rtc_candidate)
                elif "candidate" in candidate_payload:
                    await peer_connection.addIceCandidate(None)
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
        await session.close()
        await peer_connection.close()


__all__ = ["router", "ws_router"]
