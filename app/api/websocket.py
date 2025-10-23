"""WebSocket endpoint handling for Twilio ConversationRelay."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any
import litellm
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool

from app.audio import (
    apply_prosody,
    merge_prosody,
    prosody_is_useful,
    provider_supports_speed,
    voice_supports_ssml,
)
from app.call_state import agent_is_active, mark_agent_active, pop_call, release_agent
from app.chat import stream_response
from app.logging_utils import logger
from app.memory import delete_state, log_turn, save_state
from app.metrics import METRIC_MESSAGES
from app.pricing import calculate_llm_cost, estimate_twilio_cost
from app.settings import get_agent_config
from app.validators import is_from_twilio

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    logger.info("WebSocket /ws endpoint hit")
    logger.debug("WS connection attempt from %s", ws.client)
    logger.debug("WS headers: %s", dict(ws.headers))

    # Check if client requested specific subprotocol
    requested_subprotocols = ws.headers.get("sec-websocket-protocol", "").split(", ")
    logger.debug("Client requested subprotocols: %s", requested_subprotocols)

    # Only specify subprotocol if client requested it
    try:
        if "conversationrelay.v1" in requested_subprotocols:
            await ws.accept(subprotocol="conversationrelay.v1")
            logger.debug("WS accepted with subprotocol: conversationrelay.v1")
        else:
            await ws.accept()
            logger.debug("WS accepted without subprotocol")
    except Exception as e:
        logger.error("Failed to accept WebSocket connection: %s", e)
        raise

    if not is_from_twilio(ws):
        logger.warning("WS connection rejected - invalid Twilio signature")
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    logger.debug("WS connection validated - waiting for messages...")
    logger.info("WebSocket connection established from %s", ws.client)

    # Track if user has disconnected
    ws.scope["user_disconnected"] = False

    # Wrapper to safely send messages (no-op if disconnected)
    async def safe_send_json(data: dict):
        if not ws.scope.get("user_disconnected", False):
            try:
                # Ensure text messages are non-preemptible
                if data.get("type") == "text":
                    data["preemptible"] = False
                    data["interruptible"] = True
                await ws.send_json(data)
            except (WebSocketDisconnect, RuntimeError) as e:
                logger.info("User disconnected while sending message: %s", e)
                ws.scope["user_disconnected"] = True

    # Helper to send finished sound after TTS completes
    async def send_finished_sound():
        """Queue the end-of-turn chime so it can pre-empt any buffered speech."""
        # Skip if we've already queued the chime for this turn
        if ws.scope.get("chime_sent"):
            return

        finished_source = os.getenv("SOUND_FINISHED_URL", "/sounds/finished.mp3")
        payload = {
            "type": "play",
            "source": finished_source,
            "loop": 1,  # Play once
            "preemptible": True,  # Allow chime to pre-empt current audio
            "interruptible": True,
        }

        try:
            await safe_send_json(payload)
        except Exception as exc:  # noqa: BLE001 – log and continue cleanup
            logger.warning("Failed to queue finished sound: %s", exc)
            return

        ws.scope["chime_sent"] = True
        logger.info("🔔 Finished sound queued → %s", finished_source)

    try:
        logger.debug("Entering message loop...")

        async for raw in ws.iter_text():
            logger.debug("Raw WS message received: %s", raw)

            # If user disconnected while we were waiting, exit gracefully
            if ws.scope.get("user_disconnected", False):
                logger.info("User disconnected - exiting message loop")
                break

            # Check for Cloud Run timeout approaching (55 minutes = 3300 seconds)
            connection_start = ws.scope.get("connection_start_time")
            if connection_start:
                connection_elapsed = time.perf_counter() - connection_start
                if connection_elapsed >= 3300 and not ws.scope.get("reconnection_sent"):
                    # Send reconnection message to client
                    logger.warning(
                        "Connection approaching timeout (%.1fs), initiating graceful reconnection (55-minute cutoff)",
                        connection_elapsed,
                    )

                    # Send reconnection notification to Twilio
                    reconnect_msg = {
                        "type": "text",
                        "token": "I need to briefly reconnect our call to maintain quality. You'll hear a short beep and we'll continue right where we left off.",
                        "last": False,
                    }
                    await safe_send_json(reconnect_msg)

                    # Send end marker to complete the message
                    await safe_send_json({"type": "text", "token": "", "last": True})

                    ws.scope["reconnection_sent"] = True

                    # Close the WebSocket with a custom code/reason to trigger client reconnection
                    await ws.close(code=4000, reason="Graceful reconnection required")
                    break

            # Enforce max-disconnect timeout
            max_disc = ws.scope.get("max_disconnect")
            start_time = ws.scope.get("start_time")
            if max_disc and start_time:
                elapsed = time.perf_counter() - start_time
                remaining = max_disc - elapsed
                if remaining <= 5 and not ws.scope.get("goodbye_sent"):
                    # Send graceful timeout message <=5 s before cutoff
                    goodbye_text = "You've reached the time limit. Goodbye."
                    logger.info("Sending goodbye message (%ss left)", max(0, round(remaining, 2)))
                    await safe_send_json({"type": "text", "token": goodbye_text, "last": False})
                    await safe_send_json({"type": "text", "token": "", "last": True})

                    try:
                        ws.scope["completion_tokens"] += litellm.token_counter(
                            model=ws.scope["agent_config"]["model"], text=goodbye_text
                        )
                    except Exception as exc:
                        logger.error("token_counter failed: %s", exc)

                    # Wait the remaining time (>=0) then close
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                    await ws.close()
                    break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse JSON message: %s (raw: %s)", e, raw)
                # Send error response and continue - don't close the connection
                await safe_send_json(
                    {
                        "type": "text",
                        "token": "Sorry, I received a malformed message.",
                        "last": True,
                    }
                )
                continue

            # -----------------------------------------------------------------------------
            # Initial setup: Twilio emits a `setup` message when the ConversationRelay
            # session starts. We wait for that payload before initialising call state so we
            # stay aligned with the protocol expectations (replying before `setup` still
            # triggers Twilio error 64102: unable to connect to WebSocket).
            # -----------------------------------------------------------------------------
            if msg.get("type") == "setup":
                call_sid = msg.get("callSid") or msg.get("call_sid")
                logger.debug("Received setup for CallSid=%s", call_sid)

                # Resolve agent & pre-loaded state tuple
                tpl = pop_call(call_sid)
                if tpl is None:
                    logger.warning(
                        "CallSid %s not found in map; falling back to unknown-caller agent",
                        call_sid,
                    )
                    tpl = (
                        "unknown-caller",
                        get_agent_config("unknown-caller"),
                        None,
                        False,
                        None,
                    )

                agent_name, agent, saved_messages, _resumed, caller_number = tpl

                # Log visually distinct header for new call
                caller_display = caller_number or "unknown"
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                logger.info("=" * 80)
                logger.info("🚀 NEW CALL from %s at %s", caller_display, timestamp)
                logger.info("=" * 80)

                # Concurrency guard (policy 7c)
                if agent_is_active(agent_name):
                    logger.info("Concurrent call rejected for agent %s", agent_name)
                    await ws.close(code=1013, reason="Agent already in a call")
                    return
                mark_agent_active(agent_name)
                ws.scope["agent_name"] = agent_name
                ws.scope["agent_config"] = agent  # merged & maybe overridden
                ws.scope["saved_messages"] = saved_messages
                ws.scope["call_sid"] = call_sid
                ws.scope["call_context"] = {
                    "call_sid": call_sid,
                    "agent_name": agent_name,
                    "caller_number": caller_number,
                }

                # Log the system prompt once during agent initialization
                logger.debug(
                    "System prompt for CallSid=%s: %r",
                    call_sid,
                    agent.get("prompt", "No prompt configured"),
                )

                # Initialise per-call accounting
                ws.scope["start_time"] = time.perf_counter()
                ws.scope["connection_start_time"] = (
                    time.perf_counter()
                )  # Track WebSocket connection start
                ws.scope["prompt_tokens"] = 0
                ws.scope["completion_tokens"] = 0
                ws.scope["max_disconnect"] = agent.get("max_disconnect_seconds", 60)
                ws.scope["reconnection_sent"] = False  # Track if we've sent reconnection message

                # Twilio ConversationRelay no longer expects any immediate handshake
                # message. We simply finish initialisation and wait for the first prompt.
                continue

            if msg.get("type") == "prompt":
                user_text = msg.get("voicePrompt", "")
                logger.info(f"🧑🧑🧑 User: '{user_text}'")
                METRIC_MESSAGES.labels(role="user").inc()

                # Fetch agent config resolved during setup
                agent_cfg = ws.scope.get("agent_config")
                if agent_cfg is None:
                    logger.error(
                        "Agent config missing from WebSocket scope; defaulting to unknown-caller"
                    )
                    agent_cfg = get_agent_config("unknown-caller")

                # Persist user turn without blocking the event loop
                await run_in_threadpool(log_turn, "user", user_text)

                # Track prompt tokens using litellm
                try:
                    ws.scope["prompt_tokens"] += litellm.token_counter(
                        model=agent_cfg["model"], text=user_text
                    )
                except Exception as exc:
                    logger.error("token_counter failed: %s", exc)

                # Coalesce small token chunks to reduce bandwidth
                buffer: list[str] = []
                start = time.perf_counter()

                # Reset chime flag at the start of every assistant turn
                ws.scope["chime_sent"] = False

                # Conversation history – initialise once per call
                if "messages" not in ws.scope:
                    prev_msgs = ws.scope.get("saved_messages")
                    if prev_msgs is not None:
                        ws.scope["messages"] = prev_msgs
                    else:
                        ws.scope["messages"] = [{"role": "system", "content": agent_cfg["prompt"]}]

                        # Reset chime flag for this assistant turn
                        ws.scope["chime_sent"] = False

                assistant_full: list[str] = []  # accumulate for DB logging

                # Streaming response ----------------------------------------------------
                voice_name: str = agent_cfg.get("voice", "")
                provider_name: str = agent_cfg.get("tts_provider", "")

                provider_speed = provider_supports_speed(provider_name)

                # Only enable SSML when the voice supports it, the provider
                # does *not* expose a direct speed parameter **and** there is
                # at least one non-default prosody attribute.  This avoids
                # sending unnecessary <speak> wrappers for plain text.
                allow_ssml: bool = (
                    voice_supports_ssml(voice_name)
                    and not provider_speed
                    and prosody_is_useful(agent_cfg.get("tts_prosody", {}))
                )
                prosody_cfg: dict[str, str | float] = merge_prosody(
                    agent_cfg.get("tts_prosody", {}), {}
                )

                try:
                    # We hold one *pending* chunk so we can mark it as `last=True` once
                    # we know the LLM stream is finished, avoiding an extra empty frame.

                    pending_chunk: str | None = None
                    # Track whether we've already opened a <speak> element so
                    # we only emit ONE root tag for the entire response. This
                    # avoids Google TTS rejecting multiple <speak> roots once
                    # ConversationRelay concatenates all text frames.

                    started_speak: bool = False

                    def _strip_speak(fragment: str) -> str:  # local helper
                        """Return *fragment* with the outer <speak> … </speak> removed."""

                        if fragment.lstrip().startswith("<speak>") and fragment.rstrip().endswith(
                            "</speak>"
                        ):
                            return fragment[len("<speak>") : -len("</speak>")]
                        return fragment

                    # =====================================================================
                    # STREAMING CONTINUITY DURING TOOL EXECUTION
                    # =====================================================================
                    # When the LLM uses tools, it may output status tokens like "Searching."
                    # before executing the tool. Without special handling, this creates a bug:
                    #
                    # 1. LLM outputs "Searching." with a tool call
                    # 2. We stream "Searching." and send last=True when the generator pauses
                    # 3. Tool executes, then LLM outputs the actual response
                    # 4. This creates a NEW TTS session, but Twilio may not process it properly
                    #    after the previous session ended with last=True
                    #
                    # Solution: stream_response yields special marker dicts during tool execution
                    # to keep the stream alive. We handle these markers here to maintain one
                    # continuous TTS session across all tool iterations.
                    # =====================================================================

                    # Check if user disconnected and add a note for the LLM
                    if ws.scope.get("user_disconnected", False):
                        ws.scope["messages"].append(
                            {
                                "role": "user",
                                "content": "[[Note: The user has disconnected, so while further tool calls are possible if needed to complete their request, no further interaction is possible.]]",
                            }
                        )

                    call_context = ws.scope.get("call_context")
                    hangup_marker: dict[str, Any] | None = None

                    async for token in stream_response(
                        user_text,
                        agent_cfg,
                        ws.scope["messages"],
                        call_context=call_context,
                    ):
                        # Handle special markers from stream_response
                        # These are used to keep the stream alive during tool execution
                        if isinstance(token, dict):
                            if token.get("type") == "tool_executing":
                                # -------------------------------------------------
                                # Flush any buffered text *immediately* so that the
                                # status word (e.g. "Searching", "Extracting") is
                                # spoken before we start running the tool.  Without
                                # this, the buffered text sits unsent while the
                                # potentially slow tool executes, and the caller
                                # perceives a long awkward pause followed by all
                                # output at once.
                                # -------------------------------------------------

                                if buffer:
                                    payload = "".join(buffer)

                                    # Build outbound chunk identical to regular flush
                                    if allow_ssml:
                                        out_chunk = apply_prosody(payload, prosody_cfg)
                                    else:
                                        import html
                                        from xml.sax.saxutils import escape as _xml_escape

                                        # Decode HTML entities first to prevent double-encoding
                                        payload = html.unescape(payload)
                                        out_chunk = _xml_escape(payload)

                                    # Send any reserved (pending) chunk first
                                    if pending_chunk is not None:
                                        await safe_send_json(
                                            {"type": "text", "token": pending_chunk, "last": False}
                                        )
                                        if allow_ssml:
                                            logger.info("SSML chunk → %s", pending_chunk)

                                        try:
                                            ws.scope["completion_tokens"] += litellm.token_counter(
                                                model=agent_cfg["model"], text=pending_chunk
                                            )
                                        except Exception as exc:
                                            logger.error("token_counter failed: %s", exc)

                                    # Send the freshly built status chunk immediately
                                    await safe_send_json(
                                        {"type": "text", "token": out_chunk, "last": False}
                                    )
                                    if allow_ssml:
                                        logger.info("SSML chunk (pre-tool) → %s", out_chunk)

                                    try:
                                        ws.scope["completion_tokens"] += litellm.token_counter(
                                            model=agent_cfg["model"], text=out_chunk
                                        )
                                    except Exception as exc:
                                        logger.error("token_counter failed: %s", exc)

                                    # Clear buffer & reset timer; no pending chunk at the moment – the next
                                    # tokens from the LLM will establish a new one.
                                    pending_chunk = None
                                    buffer.clear()
                                    start = time.perf_counter()

                                # Tool is being executed - keep stream alive but don't send to TTS
                                # This prevents premature last=True which would cut off speech
                                logger.debug(
                                    "Tool execution marker received: %s tools",
                                    token.get("tool_count", 0),
                                )
                                continue
                            elif token.get("type") == "play":
                                # Forward play media messages directly to the client.
                                await safe_send_json(token)
                                src = token.get("source")
                                if isinstance(src, dict):
                                    src_url = src.get("uri") or src.get("url")
                                else:
                                    src_url = src or token.get("url")
                                logger.info("🎵🎵🎵 Play media message → %s", src_url)
                                continue
                            elif token.get("type") == "reset_conversation":
                                # -------------------------------------------------
                                # Reset conversation state to simulate a new call.
                                # The reset command and response have already been
                                # logged in the "old" conversation history.
                                # -------------------------------------------------
                                logger.info("Reset marker received - resetting conversation state")

                                # Get the caller info to look up fresh agent config
                                # We need to determine which agent should be used
                                # In the WebSocket context, we don't have the original caller number,
                                # but we can use the agent name from the current config
                                current_agent = ws.scope.get("agent_config", {})

                                # Find which agent this is by checking phone numbers
                                from app.settings import get_agent_for_number, _load_config

                                config = _load_config()
                                agent_name = None

                                # Find the agent name by matching current config
                                for name, cfg in config["agents"].items():
                                    if cfg.get("bot_name") == current_agent.get("bot_name"):
                                        agent_name = name
                                        break

                                if agent_name and agent_name != "unknown-caller":
                                    # Get fresh agent config (undoing any change_llm modifications)
                                    fresh_agent = get_agent_config(agent_name)
                                else:
                                    # Fallback to current agent if we can't determine
                                    fresh_agent = current_agent.copy()

                                # Delete any persisted state
                                try:
                                    agent_name_reset = ws.scope.get("agent_name")
                                    if agent_name_reset:
                                        delete_state(agent_name_reset)
                                except Exception as exc:
                                    logger.error(
                                        "Failed to delete persisted state for %s: %s",
                                        agent_name_reset,
                                        exc,
                                    )

                                # Reset WebSocket scope to initial state
                                ws.scope["agent_config"] = fresh_agent
                                ws.scope["messages"] = [
                                    {"role": "system", "content": fresh_agent["prompt"]}
                                ]
                                ws.scope["start_time"] = time.perf_counter()  # Reset timer
                                ws.scope["prompt_tokens"] = 0
                                ws.scope["completion_tokens"] = 0
                                ws.scope["chime_sent"] = False

                                # Propagate fresh agent context to all tools
                                from app import tool_framework as tf

                                tf.set_agent_context(fresh_agent)
                                tf.set_call_context(ws.scope.get("call_context"))

                                # Log the reset in memory (marks boundary between old and new conversation)
                                await run_in_threadpool(
                                    log_turn, "system", "--- CONVERSATION RESET ---"
                                )

                                # Refresh runtime settings so subsequent turns use the new agent profile
                                agent_cfg = fresh_agent
                                voice_name = agent_cfg.get("voice", "")
                                provider_name = agent_cfg.get("tts_provider", "")
                                allow_ssml = (
                                    voice_supports_ssml(voice_name)
                                    and not provider_supports_speed(provider_name)
                                    and prosody_is_useful(agent_cfg.get("tts_prosody", {}))
                                )
                                prosody_cfg = merge_prosody(agent_cfg.get("tts_prosody", {}), {})

                                # Send the reset message as the greeting for the new conversation
                                greeting_source = agent_cfg.get("welcome_greeting") or "Hi"
                                greeting_clean = greeting_source.strip().rstrip(". ")
                                fallback_greeting = greeting_clean or "Hi"
                                reset_message = token.get("message", f"Reset. {fallback_greeting}")

                                if allow_ssml:
                                    greeting_chunk = apply_prosody(reset_message, prosody_cfg)
                                else:
                                    greeting_chunk = reset_message

                                await safe_send_json(
                                    {"type": "text", "token": greeting_chunk, "last": True}
                                )

                                # Track completion tokens for the greeting
                                try:
                                    ws.scope["completion_tokens"] += litellm.token_counter(
                                        model=agent_cfg["model"], text=reset_message
                                    )
                                except Exception as exc:
                                    logger.error("token_counter failed: %s", exc)

                                # Log this greeting as the first message of the new conversation
                                await run_in_threadpool(log_turn, "bot", reset_message)

                                logger.info(
                                    "Reset complete - new conversation started with greeting: %s",
                                    reset_message,
                                )

                                # Break out of the token processing loop since we've completed the stream
                                break
                            elif token.get("type") == "hangup_call":
                                hangup_marker = token
                                marker_message = token.get("message")
                                if marker_message and not buffer:
                                    buffer.append(marker_message)
                                    assistant_full.append(marker_message)
                                logger.info("Hangup marker received – scheduling call disconnect")
                                break

                            else:
                                # Unknown marker type - log and skip
                                logger.warning("Unknown stream marker: %s", token)
                                continue

                        # Regular string token - process as before
                        assistant_full.append(token)
                        buffer.append(token)

                        try:
                            ws.scope["completion_tokens"] += litellm.token_counter(
                                model=agent_cfg["model"], text=token
                            )
                        except Exception as exc:
                            logger.error("token_counter failed: %s", exc)

                        # ---------------------- Flush heuristic ----------------------

                        flush: bool = False
                        elapsed = time.perf_counter() - start
                        payload = "".join(buffer)

                        if allow_ssml:
                            import re

                            sentence_end = re.search(r"[.!?][\"')\]]?\s*$", payload)
                            has_word = re.search(r"[A-Za-z]{2,}", payload) is not None

                            # More aggressive thresholds so speech starts sooner
                            if len(payload) >= 60:
                                flush = True
                            elif sentence_end and len(payload) >= 30 and has_word:
                                flush = True
                            elif elapsed > 0.25 and has_word:
                                flush = True
                            elif pending_chunk is None and has_word and len(payload) >= 15:
                                flush = True
                        else:
                            if len(buffer) >= 8 or elapsed > 0.06:
                                flush = True

                        if not flush:
                            continue

                        # -------------- Find safe split (always on space) ------------

                        send_part = payload  # keep trailing whitespace so next chunk breaks cleanly
                        leftover = ""

                        if allow_ssml and (len(send_part) > 200 or not sentence_end):
                            idx = send_part.rfind(" ")
                            if 0 < idx < len(send_part) - 1:
                                send_part, leftover = send_part[: idx + 1], send_part[idx + 1 :]

                        # Skip flushing if chunk has no real word (just punctuation) --
                        if allow_ssml and re.search(r"[A-Za-z]{2,}", send_part) is None:
                            # keep accumulating until we get meaningful content
                            continue

                        # Build outbound chunk ---------------------------------------

                        if allow_ssml:
                            out_chunk = apply_prosody(send_part, prosody_cfg)
                        else:
                            import html
                            from xml.sax.saxutils import escape as _xml_escape

                            # Decode HTML entities first to prevent double-encoding
                            send_part = html.unescape(send_part)
                            out_chunk = _xml_escape(send_part)

                        if pending_chunk is None:
                            # First chunk – send it immediately so TTS can start speaking
                            await safe_send_json(
                                {"type": "text", "token": out_chunk, "last": False}
                            )
                            if allow_ssml:
                                logger.info("SSML chunk → %s", out_chunk)
                            try:
                                ws.scope["completion_tokens"] += litellm.token_counter(
                                    model=agent_cfg["model"], text=out_chunk
                                )
                            except Exception as exc:
                                logger.error("token_counter failed: %s", exc)
                            # Do NOT set pending_chunk yet – wait for next flush
                        else:
                            # Normal behaviour: send previous, hold current
                            await safe_send_json(
                                {"type": "text", "token": pending_chunk, "last": False}
                            )
                            if allow_ssml:
                                logger.info("SSML chunk → %s", pending_chunk)
                            try:
                                ws.scope["completion_tokens"] += litellm.token_counter(
                                    model=agent_cfg["model"], text=pending_chunk
                                )
                            except Exception as exc:
                                logger.error("token_counter failed: %s", exc)

                            pending_chunk = out_chunk

                        # Reset buffer & timer ---------------------------------------
                        buffer.clear()
                        if leftover:
                            buffer.append(leftover)

                        start = time.perf_counter()

                    # ----------- Final flush & termination chunk(s) ------------------

                    tail = "".join(buffer).strip()
                    final_chunk: str | None = None

                    if tail:
                        if allow_ssml and re.search(r"[A-Za-z]{2,}", tail):
                            final_chunk = apply_prosody(tail, prosody_cfg)
                        elif not allow_ssml:
                            import html
                            from xml.sax.saxutils import escape as _xml_escape

                            # Decode HTML entities first to prevent double-encoding
                            tail = html.unescape(tail)
                            final_chunk = _xml_escape(tail)

                    # No speech at all in the stream – extreme corner case.
                    if pending_chunk is None and final_chunk is None:
                        await safe_send_json({"type": "text", "token": " ", "last": True})
                        if allow_ssml:
                            logger.info("SSML final → <blank whitespace>")
                    else:
                        if pending_chunk is None:
                            # We flushed the last spoken chunk earlier (e.g. before tool execution).
                            # If we still have additional text (final_chunk), send it and close properly.
                            if final_chunk is None:
                                # Extreme corner case: nothing left – send whitespace to close conversation.
                                await safe_send_json({"type": "text", "token": " ", "last": True})
                                if allow_ssml:
                                    logger.info("SSML final → <blank whitespace>")
                            else:
                                await safe_send_json(
                                    {"type": "text", "token": final_chunk, "last": True}
                                )
                                if allow_ssml:
                                    logger.info("SSML final → %s", final_chunk)

                                try:
                                    ws.scope["completion_tokens"] += litellm.token_counter(
                                        model=agent_cfg["model"], text=final_chunk
                                    )
                                except Exception as exc:
                                    logger.error("token_counter failed: %s", exc)
                        else:
                            if final_chunk is None:
                                # Only the reserved chunk remains – ensure we close the <speak> tag.
                                closing_ready = pending_chunk

                                if (
                                    allow_ssml
                                    and started_speak
                                    and not pending_chunk.rstrip().endswith("</speak>")
                                ):
                                    closing_ready = f"{pending_chunk}</speak>"

                                await safe_send_json(
                                    {"type": "text", "token": closing_ready, "last": True}
                                )
                                if allow_ssml:
                                    logger.info("SSML final → %s", closing_ready)

                                try:
                                    ws.scope["completion_tokens"] += litellm.token_counter(
                                        model=agent_cfg["model"], text=closing_ready
                                    )
                                except Exception as exc:
                                    logger.error("token_counter failed: %s", exc)
                            else:
                                # We have both: send reserved chunk (no close yet), then final chunk (adds </speak>).
                                await safe_send_json(
                                    {"type": "text", "token": pending_chunk, "last": False}
                                )
                                if allow_ssml:
                                    logger.info("SSML chunk → %s", pending_chunk)

                                try:
                                    ws.scope["completion_tokens"] += litellm.token_counter(
                                        model=agent_cfg["model"], text=pending_chunk
                                    )
                                except Exception as exc:
                                    logger.error("token_counter failed: %s", exc)

                                await safe_send_json(
                                    {"type": "text", "token": final_chunk, "last": True}
                                )
                                if allow_ssml:
                                    logger.info("SSML final → %s", final_chunk)

                                try:
                                    ws.scope["completion_tokens"] += litellm.token_counter(
                                        model=agent_cfg["model"], text=final_chunk
                                    )
                                except Exception as exc:
                                    logger.error("token_counter failed: %s", exc)

                    # Play finished sound after AI completes speaking unless we are hanging up immediately
                    if hangup_marker is None:
                        await send_finished_sound()

                    # Persist assistant turn (async).
                    assistant_text = "".join(assistant_full)

                    if assistant_text:
                        await run_in_threadpool(log_turn, "bot", assistant_text)

                    # Persist state if enabled
                    try:
                        agent_name_persist = ws.scope.get("agent_name")
                        agent_cfg_persist = ws.scope.get("agent_config", {})
                        if agent_name_persist and agent_cfg_persist.get("continue_conversation"):
                            save_state(agent_name_persist, agent_cfg_persist, ws.scope["messages"])
                    except Exception as exc:
                        logger.error("Failed to save state: %s", exc)

                    METRIC_MESSAGES.labels(role="bot").inc()

                    if hangup_marker is not None:
                        reason = hangup_marker.get("reason", "Hangup requested")
                        logger.info("Hangup tool completed – closing WebSocket (%s)", reason)
                        ws.scope["user_disconnected"] = True
                        try:
                            await ws.close(code=4100, reason=reason[:120])
                        except Exception as exc:
                            logger.warning("Failed to close WebSocket during hangup: %s", exc)
                        break

                    # Signal end-of-turn for easier tracing – we are now idle until the next user prompt.
                    logger.debug("Waiting for user")

                    # If user disconnected, exit the message loop
                    if ws.scope.get("user_disconnected", False):
                        logger.info(
                            "User disconnected - exiting message loop after completing request"
                        )
                        break

                except Exception as exc:  # LLM or internal failure
                    logger.exception("LLM failure: %s", exc)
                    await safe_send_json(
                        {"type": "text", "token": "Sorry, I ran into an error.", "last": True}
                    )
                    await send_finished_sound()

            elif msg.get("type") == "interrupt":
                # -----------------------------------------------------
                # ConversationRelay signalled an interrupt (user spoke
                # while bot was speaking). Log detailed info and continue
                # waiting for the next user input.
                # -----------------------------------------------------

                utterance_partial = msg.get("utteranceUntilInterrupt", "")
                duration_ms = msg.get("durationUntilInterruptMs", 0)

                logger.info(
                    "User interrupted bot after %dms. Partial utterance: '%s'",
                    duration_ms,
                    utterance_partial,
                )

                # Optional: Log additional interrupt context if available
                if msg.get("reason"):
                    logger.debug("Interrupt reason: %s", msg.get("reason"))
                if msg.get("confidence"):
                    logger.debug("Interrupt confidence: %s", msg.get("confidence"))

                # No response needed - just continue waiting for next user prompt
                continue

            elif msg.get("type") == "error":
                # -----------------------------------------------------
                # ConversationRelay signalled an error (e.g. malformed
                # message).  Log details, inform the caller, then continue
                # waiting for further frames.
                # -----------------------------------------------------

                code = msg.get("code", "N/A")
                desc = msg.get("description", "An unknown error occurred.")
                logger.error("ConversationRelay error %s: %s", code, desc)

                apology = f"Oops. Error: {desc}"

                await safe_send_json({"type": "text", "token": apology, "last": True})
                await send_finished_sound()

                # Do NOT close the WebSocket – allow ConversationRelay to continue if possible.
                continue

            else:
                # Log any unhandled message types
                logger.warning("Unhandled message type: %s, full message: %s", msg.get("type"), msg)

    except WebSocketDisconnect as e:
        # Connection dropped by client (e.g., call ended)
        _log_call_summary(ws)
        logger.info("WS disconnect from %s, code: %s, reason: %s", ws.client, e.code, e.reason)
        ws.scope["user_disconnected"] = True

        # Log additional context for Twilio disconnections
        if e.code == 1000:  # Normal closure
            logger.info("Normal WebSocket closure - call ended normally")
        elif e.code == 1001:  # Going away
            logger.warning("WebSocket going away - endpoint shutting down")
        elif e.code == 1006:  # Abnormal closure
            logger.error("Abnormal WebSocket closure - no close frame received")
        elif e.code == 1011:  # Server error
            logger.error("WebSocket server error - unexpected condition")
        elif e.code == 4000:  # Our custom reconnection code
            logger.info("Graceful reconnection initiated - approaching Cloud Run timeout")
        else:
            logger.warning("Unexpected WebSocket close code: %s", e.code)

    except Exception as e:
        logger.exception("Unexpected error in WebSocket handler: %s", e)
        raise
    finally:
        # Ensure active-call flag cleared
        agent_name_final = ws.scope.get("agent_name")
        if agent_name_final:
            release_agent(agent_name_final)


def _log_call_summary(ws: WebSocket) -> None:
    """Log duration, token usage, and estimated cost for a completed call."""

    start = ws.scope.get("start_time")
    if start is None:
        return

    duration_sec = time.perf_counter() - start
    h, rem = divmod(int(duration_sec), 3600)
    m, s = divmod(rem, 60)
    hhmmss = f"{h:02}:{m:02}:{s:02}"

    connection_start = ws.scope.get("connection_start_time")
    if connection_start:
        connection_duration_sec = time.perf_counter() - connection_start
        ch, crem = divmod(int(connection_duration_sec), 3600)
        cm, cs = divmod(crem, 60)
        connection_hhmmss = f"{ch:02}:{cm:02}:{cs:02}"
        logger.info(
            "WebSocket connection duration: %s (%d seconds)",
            connection_hhmmss,
            int(connection_duration_sec),
        )

    prompt_tokens = ws.scope.get("prompt_tokens", 0)
    completion_tokens = ws.scope.get("completion_tokens", 0)
    agent_cfg = ws.scope.get("agent_config", {})
    model = agent_cfg.get("model", "unknown")

    try:
        llm_cost = calculate_llm_cost(model, prompt_tokens, completion_tokens)
    except Exception as exc:  # noqa: BLE001 – pricing is best-effort telemetry
        logger.error(
            "Failed to compute LLM cost for model %s (prompt=%d, completion=%d): %s",
            model,
            prompt_tokens,
            completion_tokens,
            exc,
            exc_info=True,
        )
        llm_cost = 0.0

    twilio_minutes = (duration_sec + 59) // 60
    twilio_cost = estimate_twilio_cost(twilio_minutes)
    total_cost = llm_cost + twilio_cost

    logger.info(
        "CALL SUMMARY – duration %s, tokens prompt=%d, completion=%d, LLM cost=$%.4f, Twilio cost=$%.4f, TOTAL=$%.4f",
        hhmmss,
        prompt_tokens,
        completion_tokens,
        llm_cost,
        twilio_cost,
        total_cost,
    )
