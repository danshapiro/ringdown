"""Mobile text streaming WebSocket endpoint."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any, Dict, List

import litellm
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool

from app.chat import stream_response
from app.logging_utils import logger
from app.memory import log_turn
from app.metrics import METRIC_MESSAGES
from app.mobile.text_session_store import TextSessionState, get_text_session_store

_SESSION_SOURCE = "mobile-text"
_DEFAULT_GREETING = "You are connected to the Ringdown assistant."
_SESSION_TOKEN_HEADER = "x-ringdown-session-token"

router = APIRouter(prefix="/v1/mobile/text", tags=["mobile-text"])


def _structured_log(level: str, event: str, **fields: Any) -> None:
    """Emit JSON-structured log lines."""

    payload = {"event": event, **fields}
    try:
        message = json.dumps(payload, ensure_ascii=True, default=str, separators=(",", ":"))
    except TypeError:
        message = json.dumps({"event": event, "fallback": "serialization_failed"}, ensure_ascii=True)

    log_method = getattr(logger, level, logger.info)
    log_method(message)


async def _send_json(ws: WebSocket, payload: Dict[str, Any]) -> None:
    """Safely send JSON data if the connection remains open."""

    if ws.application_state.name == "CONNECTED":
        await ws.send_json(payload)


def _resolve_greeting(agent_cfg: dict[str, Any]) -> str | None:
    candidate = agent_cfg.get("welcome_greeting")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return _DEFAULT_GREETING


def _initialise_messages(state: TextSessionState) -> List[Dict[str, Any]]:
    """Ensure the session carries a message history seeded with the system prompt."""

    messages = state.messages or []
    if messages:
        return messages

    prompt = state.agent_config.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        messages = [{"role": "system", "content": prompt}]
    else:
        messages = []

    state.messages = messages
    return messages


@router.websocket("/session")
async def mobile_text_session(ws: WebSocket) -> None:
    """Bidirectional text streaming endpoint for the Android client."""

    session_token = (
        ws.headers.get(_SESSION_TOKEN_HEADER)
        or ws.query_params.get("sessionToken")
        or ""
    ).strip()
    if not session_token:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="session token required")
        return

    store = get_text_session_store()
    state: TextSessionState | None = None
    try:
        state = await store.consume_session_token(session_token)
    except KeyError:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid session token")
        return

    agent_cfg = state.agent_config or {}
    messages = _initialise_messages(state)

    await ws.accept()

    ws.scope["messages"] = messages
    ws.scope["prompt_tokens"] = 0
    ws.scope["completion_tokens"] = 0
    ws.scope["session_state"] = state

    device_id = state.device_id
    session_id = state.session_id

    _structured_log(
        "info",
        "mobile_text_session.connected",
        session_id=session_id,
        device_id=device_id,
        agent=state.agent_name,
        client=str(ws.client),
    )

    heartbeat_interval = max(int(state.heartbeat_interval_seconds), 5)
    heartbeat_timeout = max(int(state.heartbeat_timeout_seconds), heartbeat_interval + 5)
    last_heartbeat = time.monotonic()
    guard_task: asyncio.Task[None] | None = None

    async def heartbeat_guard() -> None:
        while True:
            await asyncio.sleep(float(heartbeat_interval))
            if ws.application_state.name != "CONNECTED":
                break
            if time.monotonic() - last_heartbeat > heartbeat_timeout:
                _structured_log(
                    "warning",
                    "mobile_text_session.heartbeat_timeout",
                    session_id=session_id,
                )
                with contextlib.suppress(Exception):
                    await ws.close(code=4401, reason="heartbeat timeout")
                break

    guard_task = asyncio.create_task(heartbeat_guard())

    ready_payload = {
        "type": "ready",
        "sessionId": session_id,
        "agent": state.agent_name,
        "greeting": _resolve_greeting(agent_cfg),
        "heartbeatIntervalSeconds": heartbeat_interval,
        "heartbeatTimeoutSeconds": heartbeat_timeout,
    }
    await _send_json(ws, ready_payload)

    greeting_text = ready_payload["greeting"]
    model_name = agent_cfg.get("model")
    if greeting_text and not state.greeting_sent:
        await _send_json(
            ws,
            {
                "type": "assistant_token",
                "token": greeting_text,
                "final": True,
                "messageType": "greeting",
            },
        )
        messages.append({"role": "assistant", "content": greeting_text})
        state.greeting_sent = True
        try:
            METRIC_MESSAGES.labels(role="bot").inc()
        except Exception:
            _structured_log("warning", "mobile_text_session.metric_error", role="bot")
        try:
            await run_in_threadpool(log_turn, "bot", greeting_text, source=_SESSION_SOURCE)
        except Exception as exc:
            _structured_log(
                "warning",
                "mobile_text_session.greeting_log_failed",
                session_id=session_id,
                error=str(exc),
            )
        if isinstance(model_name, str) and model_name:
            try:
                ws.scope["completion_tokens"] += litellm.token_counter(
                    model=model_name, text=greeting_text
                )
            except Exception as exc:
                _structured_log(
                    "warning",
                    "mobile_text_session.completion_token_error",
                    session_id=session_id,
                    error=str(exc),
                )

    user_buffer: list[str] = []
    processing_lock = asyncio.Lock()

    async def process_user_text(text: str, utterance_id: str | None) -> None:
        if not text:
            return

        async with processing_lock:
            try:
                METRIC_MESSAGES.labels(role="user").inc()
            except Exception:
                _structured_log("warning", "mobile_text_session.metric_error", role="user")

            try:
                await run_in_threadpool(log_turn, "user", text, source=_SESSION_SOURCE)
            except Exception as exc:
                _structured_log(
                    "warning",
                    "mobile_text_session.user_log_failed",
                    session_id=session_id,
                    error=str(exc),
                )

            if isinstance(model_name, str) and model_name:
                try:
                    ws.scope["prompt_tokens"] += litellm.token_counter(model=model_name, text=text)
                except Exception as exc:
                    _structured_log(
                        "warning",
                        "mobile_text_session.prompt_token_error",
                        session_id=session_id,
                        error=str(exc),
                    )

            call_context = {
                "channel": _SESSION_SOURCE,
                "session_id": session_id,
                "device_id": device_id,
                "utterance_id": utterance_id,
            }

            assistant_full: list[str] = []
            pending_chunk: str | None = None

            try:
                async for token in stream_response(
                    text, agent_cfg, messages, call_context=call_context
                ):
                    if isinstance(token, dict):
                        await _send_json(
                            ws,
                            {
                                "type": "tool_event",
                                "event": token.get("type"),
                                "payload": token,
                            },
                        )
                        continue

                    assistant_full.append(token)

                    if pending_chunk is not None:
                        await _send_json(
                            ws, {"type": "assistant_token", "token": pending_chunk, "final": False}
                        )
                        if isinstance(model_name, str) and model_name:
                            try:
                                ws.scope["completion_tokens"] += litellm.token_counter(
                                    model=model_name, text=pending_chunk
                                )
                            except Exception as exc:
                                _structured_log(
                                    "warning",
                                    "mobile_text_session.completion_token_error",
                                    session_id=session_id,
                                    error=str(exc),
                                )

                    pending_chunk = token

                if pending_chunk is not None:
                    await _send_json(
                        ws, {"type": "assistant_token", "token": pending_chunk, "final": True}
                    )
                    if isinstance(model_name, str) and model_name:
                        try:
                            ws.scope["completion_tokens"] += litellm.token_counter(
                                model=model_name, text=pending_chunk
                            )
                        except Exception as exc:
                            _structured_log(
                                "warning",
                                "mobile_text_session.completion_token_error",
                                session_id=session_id,
                                error=str(exc),
                            )
                else:
                    await _send_json(ws, {"type": "assistant_token", "token": "", "final": True})

            except Exception as exc:
                _structured_log(
                    "error",
                    "mobile_text_session.stream_failure",
                    session_id=session_id,
                    error=str(exc),
                )
                await _send_json(
                    ws,
                    {
                        "type": "error",
                        "code": "assistant_failure",
                        "message": "Sorry, I ran into an error.",
                    },
                )
                return

            assistant_text = "".join(assistant_full).strip()
            if assistant_text:
                try:
                    await run_in_threadpool(log_turn, "bot", assistant_text, source=_SESSION_SOURCE)
                except Exception as exc:
                    _structured_log(
                        "warning",
                        "mobile_text_session.assistant_log_failed",
                        session_id=session_id,
                        error=str(exc),
                    )
                try:
                    METRIC_MESSAGES.labels(role="bot").inc()
                except Exception:
                    _structured_log("warning", "mobile_text_session.metric_error", role="bot")

    try:
        async for raw in ws.iter_text():
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await _send_json(
                    ws,
                    {"type": "error", "code": "invalid_json", "message": "Message must be JSON."},
                )
                continue

            msg_type = message.get("type")
            if msg_type == "heartbeat":
                last_heartbeat = time.monotonic()
                await _send_json(ws, {"type": "heartbeat", "sessionId": session_id})
                continue

            if msg_type in {"user_token", "user_message"}:
                token_value = (
                    message.get("token") if msg_type == "user_token" else message.get("text")
                )
                if not isinstance(token_value, str):
                    await _send_json(
                        ws,
                        {
                            "type": "error",
                            "code": "invalid_payload",
                            "message": "Token must be a string.",
                        },
                    )
                    continue

                user_buffer.append(token_value)
                final_flag = bool(message.get("final", msg_type == "user_message"))
                utterance_id = message.get("utteranceId") or message.get("utterance_id")

                if final_flag:
                    user_text = "".join(user_buffer).strip()
                    user_buffer.clear()
                    await process_user_text(user_text, utterance_id)
                continue

            if msg_type == "cancel":
                user_buffer.clear()
                await _send_json(ws, {"type": "ack", "event": "cancelled"})
                continue

            await _send_json(
                ws,
                {
                    "type": "error",
                    "code": "unknown_type",
                    "message": f"Unsupported message type: {msg_type}",
                },
            )
    except WebSocketDisconnect:
        _structured_log(
            "info",
            "mobile_text_session.disconnected",
            session_id=session_id,
            device_id=device_id,
        )
    finally:
        if guard_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                guard_task.cancel()
                await guard_task

        if state is not None:
            await store.update_messages(state.session_id, ws.scope.get("messages", []))
            await store.mark_disconnected(state.session_id)

            summary = {
                "session_id": session_id,
                "device_id": device_id,
                "prompt_tokens": ws.scope.get("prompt_tokens", 0),
                "completion_tokens": ws.scope.get("completion_tokens", 0),
            }
            _structured_log("info", "mobile_text_session.completed", **summary)
