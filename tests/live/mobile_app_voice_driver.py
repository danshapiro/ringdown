#!/usr/bin/env python3
"""Drive the mobile handset voice path end-to-end using aiortc and capture audio output."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import wave
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import List, Optional

import click
import httpx
import numpy as np
import websockets
from aiortc import (  # type: ignore[import]
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceCandidate,
    RTCIceServer,
)
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError  # type: ignore[import]
from av import AudioFrame  # type: ignore[import]
from pydub import AudioSegment  # type: ignore[import]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from live_test_call import generate_tts_audio  # noqa: E402

SAMPLE_RATE = 48_000
DEFAULT_BACKEND_URL = "https://danbot-twilio-bkvo7niota-uw.a.run.app/"
DEFAULT_DEVICE_ID = "instrumentation-device"


class PCMTrack(AudioStreamTrack):
    """Audio track that streams PCM samples to the peer connection."""

    kind = "audio"

    def __init__(self, samples: np.ndarray, sample_rate: int = SAMPLE_RATE, *, debug: bool = False) -> None:
        super().__init__()
        self._samples = samples.astype(np.int16)
        self._sample_rate = sample_rate
        self._chunk = max(1, sample_rate // 50)  # ~20 ms
        self._cursor = 0
        self._timestamp = 0
        self._debug = debug

    async def recv(self) -> AudioFrame:
        if self._cursor >= len(self._samples):
            await asyncio.sleep(self._chunk / self._sample_rate)
            raise MediaStreamError

        chunk = self._samples[self._cursor : self._cursor + self._chunk]
        self._cursor += len(chunk)

        frame = AudioFrame(format="s16", layout="mono", samples=len(chunk))
        frame.pts = self._timestamp
        frame.sample_rate = self._sample_rate
        frame.time_base = Fraction(1, self._sample_rate)
        frame.planes[0].update(chunk.tobytes())
        self._timestamp += len(chunk)

        if self._debug and self._cursor <= self._chunk * 5:
            click.echo(f"-> Sent audio frame with {len(chunk)} samples (cursor={self._cursor})")

        await asyncio.sleep(len(chunk) / self._sample_rate)
        return frame


@dataclass
class CallResult:
    backend_url: str
    device_id: str
    prompt_path: Path
    response_path: Path
    response_frames: int
    response_amplitude: float
    response_peak: int
    ice_servers: int


class RemoteAudioCollector:
    """Collects PCM data from the remote audio track."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.sample_rate = SAMPLE_RATE
        self.frames = 0
        self.first_frame = asyncio.Event()

    async def consume(self, track: AudioStreamTrack) -> None:
        try:
            while True:
                frame = await track.recv()
                if frame.sample_rate:
                    self.sample_rate = frame.sample_rate
                array = frame.to_ndarray(format="s16")
                self.buffer.extend(array.tobytes())
                self.frames += 1
                if not self.first_frame.is_set() and self.buffer:
                    self.first_frame.set()
        except MediaStreamError:
            return


def _prepare_prompt_audio(prompt: str, *, voice: str, model: str, silence_after: float) -> Path:
    audio_path = Path(generate_tts_audio(prompt, voice=voice, model=model, output_format="mp3"))
    segment = AudioSegment.from_file(audio_path)
    segment = segment.set_channels(1).set_frame_rate(SAMPLE_RATE).set_sample_width(2)
    if silence_after > 0:
        silence_ms = int(silence_after * 1000)
        segment += AudioSegment.silent(duration=silence_ms, frame_rate=SAMPLE_RATE)
    pcm_path = audio_path.with_suffix(".wav")
    segment.export(pcm_path, format="wav")
    return pcm_path


def _load_samples(pcm_path: Path) -> np.ndarray:
    segment = AudioSegment.from_file(pcm_path)
    segment = segment.set_channels(1).set_frame_rate(SAMPLE_RATE).set_sample_width(2)
    return np.array(segment.get_array_of_samples(), dtype=np.int16)


async def _register_device(base_url: str, device_id: str, timeout: float = 45.0, *, debug: bool) -> None:
    payload = {
        "deviceId": device_id,
        "label": "automation",
        "platform": "python-tool",
        "model": "aiortc-client",
        "appVersion": "tool",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{base_url}v1/mobile/devices/register", json=payload)
    if response.status_code == 429:
        if debug:
            click.echo("WARNING: Device registration throttled (429); assuming existing approval.")
        return
    response.raise_for_status()
    result = response.json()

    status = result.get("status")
    if status != "APPROVED":
        raise RuntimeError(f"Device {device_id} not approved (status={status})")


def _resolve_backend_and_device() -> tuple[str, str]:
    backend = os.environ.get("RINGDOWN_BACKEND_URL", DEFAULT_BACKEND_URL).strip()
    if not backend.endswith("/"):
        backend = f"{backend}/"

    device_id = os.environ.get("RINGDOWN_DEVICE_ID", DEFAULT_DEVICE_ID).strip()
    if not device_id:
        raise RuntimeError("RINGDOWN_DEVICE_ID must not be empty")

    return backend, device_id


async def _drive_call(
    *,
    backend_url: str,
    device_id: str,
    prompt_path: Path,
    samples: np.ndarray,
    wait_seconds: float,
    output_path: Path,
    skip_registration: bool,
    websocket_timeout: float,
    debug: bool,
) -> CallResult:
    base_url = backend_url.rstrip("/") + "/"
    if not skip_registration:
        await _register_device(base_url, device_id, debug=debug)

    signaling_url = base_url.replace("https://", "wss://") + "ws/mobile/voice"
    signaling_url = f"{signaling_url}?device_id={device_id}"

    pc = RTCPeerConnection()
    track = PCMTrack(samples, debug=debug)
    collector = RemoteAudioCollector()
    ice_server_count = 0

    @pc.on("track")
    def _on_track(track_obj: AudioStreamTrack) -> None:
        if track_obj.kind == "audio":
            asyncio.create_task(collector.consume(track_obj))

    async def _publish_candidate(candidate: RTCIceCandidate) -> None:
        payload = {
            "type": "candidate",
            "candidate": {
                "candidate": candidate.to_sdp(),
                "sdpMid": candidate.sdpMid,
                "sdpMLineIndex": candidate.sdpMLineIndex,
            },
        }
        await websocket.send(json.dumps(payload))

    ice_event = asyncio.Event()
    answer_event = asyncio.Event()

    async with websockets.connect(signaling_url, ping_interval=None, open_timeout=websocket_timeout) as websocket:
        @pc.on("icecandidate")
        def _on_icecandidate(candidate: Optional[RTCIceCandidate]) -> None:
            if candidate is None:
                return
            asyncio.create_task(_publish_candidate(candidate))

        async def _receiver() -> None:
            nonlocal ice_server_count
            try:
                async for raw in websocket:
                    message = json.loads(raw)
                    mtype = (message.get("type") or "").lower()
                    if mtype == "iceservers":
                        entries = message.get("iceServers") or []
                        servers: List[RTCIceServer] = []
                        for entry in entries:
                            urls = entry.get("urls")
                            if isinstance(urls, str):
                                urls = [urls]
                            if not urls:
                                continue
                            servers.append(
                                RTCIceServer(
                                    urls=urls,
                                    username=entry.get("username"),
                                    credential=entry.get("credential"),
                                )
                            )
                        if servers:
                            ice_server_count = len(servers)
                        ice_event.set()
                        if debug:
                            click.echo(f"Received {ice_server_count} ICE servers")
                    elif mtype == "answer":
                        sdp = message.get("sdp")
                        if not isinstance(sdp, str):
                            raise RuntimeError("Answer missing SDP")
                        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
                        answer_event.set()
                        if debug:
                            click.echo("Received answer")
                    elif mtype == "candidate":
                        payload = message.get("candidate") or {}
                        candidate_value = payload.get("candidate")
                        if not candidate_value:
                            continue
                        index_value = payload.get("sdpMLineIndex")
                        try:
                            index_int = int(index_value)
                        except (TypeError, ValueError):
                            index_int = 0
                        candidate = RTCIceCandidate(
                            sdpMid=payload.get("sdpMid"),
                            sdpMLineIndex=index_int,
                            candidate=candidate_value,
                        )
                        await pc.addIceCandidate(candidate)
                    elif mtype == "bye":
                        break
                    else:
                        if debug:
                            click.echo(f"WARNING: Ignoring signaling message: {message}")
            except Exception as exc:
                click.echo(f"ERROR: Receiver error: {exc}", err=True)
                raise

        receiver_task = asyncio.create_task(_receiver())

        pc.addTrack(track)

        await asyncio.wait_for(ice_event.wait(), timeout=websocket_timeout)

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await websocket.send(
            json.dumps(
                {
                    "type": "offer",
                    "deviceId": device_id,
                    "sdp": pc.localDescription.sdp,
                }
            )
        )

        await asyncio.wait_for(answer_event.wait(), timeout=websocket_timeout)

        try:
            await asyncio.wait_for(collector.first_frame.wait(), timeout=wait_seconds)
            if debug:
                click.echo("Remote audio started")
        except asyncio.TimeoutError:
            if debug:
                click.echo("WARNING: No remote audio within wait window")

        await asyncio.sleep(wait_seconds)
        await websocket.send(json.dumps({"type": "bye"}))
        await receiver_task

    await pc.close()

    if collector.buffer:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(collector.sample_rate)
            wav_file.writeframes(bytes(collector.buffer))
        amplitudes = np.frombuffer(collector.buffer, dtype=np.int16)
        mean = float(np.abs(amplitudes).mean()) if amplitudes.size else 0.0
        peak = int(np.abs(amplitudes).max()) if amplitudes.size else 0
    else:
        output_path.unlink(missing_ok=True)
        mean = 0.0
        peak = 0

    return CallResult(
        backend_url=backend_url,
        device_id=device_id,
        prompt_path=prompt_path,
        response_path=output_path,
        response_frames=collector.frames,
        response_amplitude=mean,
        response_peak=peak,
        ice_servers=ice_server_count,
    )


@click.command()
@click.option("--backend", default=None, help="Backend base URL (defaults to RINGDOWN_BACKEND_URL)")
@click.option("--device-id", default=None, help="Device ID override (defaults to env)")
@click.option("--prompt", default="Hello, can you hear me?", help="Prompt to speak via TTS")
@click.option("--tts-voice", default="alloy", help="OpenAI TTS voice preset")
@click.option("--tts-model", default="tts-1", help="OpenAI TTS model")
@click.option("--silence-after", default=2.0, help="Seconds of silence appended after prompt audio")
@click.option("--wait-seconds", default=5.0, help="Seconds to wait for assistant audio before hangup")
@click.option("--output", default="build/mobile_response.wav", help="File to write remote audio into")
@click.option("--websocket-timeout", default=60.0, show_default=True, help="Seconds to wait for ICE/answer events")
@click.option("--skip-register", is_flag=True, default=False, help="Skip device registration step")
@click.option("--debug", is_flag=True, default=False, help="Emit verbose signaling diagnostics")
@click.option("--no-summary", is_flag=True, default=False, help="Suppress textual summary output")
def main(
    backend: Optional[str],
    device_id: Optional[str],
    prompt: str,
    tts_voice: str,
    tts_model: str,
    silence_after: float,
    wait_seconds: float,
    output: str,
    websocket_timeout: float,
    skip_register: bool,
    debug: bool,
    no_summary: bool,
) -> None:
    backend_url, default_device = _resolve_backend_and_device()
    if backend:
        backend_url = backend
    if device_id:
        default_device = device_id

    pcm_path = _prepare_prompt_audio(prompt, voice=tts_voice, model=tts_model, silence_after=silence_after)
    samples = _load_samples(pcm_path)
    if debug:
        click.echo(f"INFO: Prompt samples mean amplitude: {float(np.abs(samples).mean()):.1f}")

    output_path = Path(output)
    result = asyncio.run(
        _drive_call(
            backend_url=backend_url,
            device_id=default_device,
            prompt_path=pcm_path,
            samples=samples,
            wait_seconds=wait_seconds,
            output_path=output_path,
            skip_registration=skip_register,
            websocket_timeout=websocket_timeout,
            debug=debug,
        )
    )

    if not no_summary:
        click.echo("Mobile voice call summary")
        click.echo(f"Backend: {result.backend_url}")
        click.echo(f"Device ID: {result.device_id}")
        click.echo(f"ICE servers: {result.ice_servers}")
        click.echo(f"Prompt audio: {result.prompt_path}")
        click.echo(f"Response frames: {result.response_frames}")
        click.echo(f"Response amplitude: {result.response_amplitude:.1f} (peak {result.response_peak})")
        if result.response_path.exists():
            click.echo(f"Response saved to: {result.response_path}")
        else:
            click.echo("Response file not created (no audio received)")


if __name__ == "__main__":
    main()


