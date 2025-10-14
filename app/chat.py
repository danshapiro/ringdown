# Logging first so we capture import-time failures too
from typing import AsyncIterator, Dict, Any
import json

import logging
import asyncio  # Needed for async tool execution with thinking sounds
import inspect

import litellm


def acompletion(*args, **kwargs):
    """Proxy to :func:`litellm.acompletion` so tests can monkeypatch us."""

    return litellm.acompletion(*args, **kwargs)

from log_love import setup_logging

from . import tool_framework as tf
# Tool orchestration
from .tool_runner import ToolRunner, ToolEvent  # centralised tool handling
from . import settings as _settings

from datetime import datetime, timezone, date
import copy
import time
import os

# URLs for sound effects (absolute URLs provided via env vars when deployed)
_THINKING_SOUND_URL = os.getenv("SOUND_THINKING_URL", "/sounds/thinking.mp3")
_FINISHED_SOUND_URL = os.getenv("SOUND_FINISHED_URL", "/sounds/finished.mp3")


class ThinkingAudioController:
    """Authoritatively control when the looping thinking sound is active."""

    def __init__(self, source: str) -> None:
        self._source = source
        self._playing: bool = False

    def start_payload(self) -> dict[str, Any] | None:
        """Return a Twilio play payload if we need to start the loop."""

        if self._playing:
            return None

        self._playing = True
        return _make_play_payload(
            self._source,
            loop=1000,
            preemptible=True,
            interruptible=True,
        )

    def stop(self) -> None:
        """Mark the loop as stopped so the next wait can restart it."""

        self._playing = False


def _make_play_payload(
    source: str,
    *,
    loop: int = 0,
    preemptible: bool = True,
    interruptible: bool = True,
) -> dict[str, Any]:
    """Return Twilio ConversationRelay-compatible play payload."""

    return {
        "type": "play",
        "source": source,
        "loop": loop,
        "preemptible": preemptible,
        "interruptible": interruptible,
    }


# Module logger
logger = setup_logging()

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _json_default(obj: Any) -> Any:
    """Best-effort encoder for objects not naturally serialisable."""

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, Exception):
        return {"type": obj.__class__.__name__, "message": str(obj)}
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="python")
        except TypeError:
            return model_dump()
        except Exception:  # noqa: BLE001 - ignore and continue
            pass
    dict_method = getattr(obj, "dict", None)
    if callable(dict_method):
        try:
            return dict_method()
        except Exception:  # noqa: BLE001 - ignore and continue
            pass
    asdict = getattr(obj, "_asdict", None)
    if callable(asdict):
        try:
            return asdict()
        except Exception:  # noqa: BLE001 - ignore and continue
            pass
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return repr(obj)


def _safe_json_dumps(payload: Any) -> str:
    """Serialise *payload* to JSON, falling back to repr on failure."""

    try:
        return json.dumps(payload, default=_json_default, ensure_ascii=True)
    except Exception as exc:  # noqa: BLE001 - last resort fallback
        logger.error("Failed to serialise payload to JSON: %s", exc, exc_info=True)
        fallback = {"error": "serialization_failed", "repr": repr(payload)}
        return json.dumps(fallback, ensure_ascii=True)

# ---------------------------------------------------------------------------
# ToolRunner configuration (status + thinking sounds) – sourced from config.yaml
# ---------------------------------------------------------------------------

_cfg = _settings._load_config()  # internal helper; safe to reuse
_tool_cfg = (_cfg.get("defaults", {}).get("tool_runner") or {}) if _cfg else {}

_STATUS_FROM_CFG = _tool_cfg.get("status_messages", {})

# Fallbacks if config lacks entries ----------------------------------------

_FALLBACK_STATUS = {
    "TavilySearch": "Searching...",
    "TavilyExtract": "Extracting...",
    "SendEmail": "Sending email...",
    "default": "Processing...",
}

_FALLBACK_THINKING = {
    "default": ["hmm...", "checking...", "ok...", "still working..."],
}

TOOL_STATUS_MESSAGES: dict[str, str] = {**_FALLBACK_STATUS, **_STATUS_FROM_CFG}


def get_tool_status_message(tool_name: str) -> str:
    """Get the status message for a tool, with fallback."""
    return TOOL_STATUS_MESSAGES.get(tool_name, "Processing...")


# We no longer cache a single global agent because multiple agents are
# active concurrently.  Instead, callers must pass the fully-merged agent
# configuration for which to generate a response.


async def stream_response(
    user_text: str,
    agent: Dict[str, Any],
    messages: list[dict[str, Any]] | None = None,
) -> AsyncIterator[str | dict[str, Any]]:
    """Stream an LLM reply for *user_text*.

    The *messages* parameter allows callers to maintain a *single* list that
    accumulates the full conversation across turns (system, user, assistant,
    tool messages, etc.).  If *messages* is *None* the function falls back to
    the original single-turn behaviour.

    The underlying call is delegated to :pyfunc:`litellm.acompletion`.
    
    Yields:
        str: Regular text tokens from the LLM response
        dict: Special markers (e.g., {"type": "tool_executing"}) to signal
              stream state to the WebSocket handler. These markers keep the
              stream alive during tool execution, preventing premature TTS
              termination.
    """

    logger.debug("LLM request (model=%s, tools enabled=%s): %s", agent["model"], bool(agent.get("tools")), user_text)

    # Propagate agent configuration to all tool modules (e.g., for greenlists)
    tf.set_agent_context(agent)

    thinking_audio = ThinkingAudioController(_THINKING_SOUND_URL)

    # --------------------------------------------------------------
    # Fallback / backup model handling
    # --------------------------------------------------------------

    # The agent config may specify an explicit backup model to use
    # when the primary model errors **or** yields zero tokens.
    backup_model: str | None = agent.get("backup_model")

    # Flag so we only fallback once at most.
    used_backup: bool = False
    # If we switch models we prefix the reply with "<model> says:\n".
    announce_prefix: str | None = None

    # ------------------------------------------------------------------
    # Build tool schemas if any enabled for this agent
    # ------------------------------------------------------------------

    tools = []
    if agent.get("tools"):
        try:
            tools = tf.get_tools_for_agent(agent)

            # Vertex/Gemini models reject certain JSON-Schema keywords like
            # "exclusiveMinimum".  Strip them out when target model is Gemini.
            if any(k in agent["model"].lower() for k in ("gemini", "vertex")):
                def _clean(d: dict):
                    if isinstance(d, dict):
                        # Remove problematic numeric-bound keywords
                        for bad in ("exclusiveMinimum", "exclusiveMaximum"):
                            d.pop(bad, None)
                        # Recurse
                        for v in d.values():
                            _clean(v)
                    elif isinstance(d, list):
                        for item in d:
                            _clean(item)

                for schema in tools:
                    _clean(schema)
        except Exception as exc:  # noqa: BLE001 – propagate config issues early
            logger.exception("Failed to build tool schemas: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Build / extend message history
    # ------------------------------------------------------------------

    if messages is None:
        # Legacy single-turn usage – build a fresh list each time.
        messages = [{"role": "system", "content": agent["prompt"]}]

    if messages and messages[0]["role"] == "system" and "_system_prompt_template" not in agent:
        agent["_system_prompt_template"] = agent.get("prompt") or messages[0]["content"]

    # Append current user turn (mutates caller-supplied list too)
    messages.append({"role": "user", "content": user_text})

    # Helper to refresh dynamic placeholders in the system prompt each time we
    # send a request to the LLM.  We mutate messages[0]['content'] in-place so
    # the latest timestamp is always used.

    def _refresh_dynamic_prompt() -> None:
        if messages and messages[0]["role"] == "system":
            template = agent.get("_system_prompt_template")
            if not template:
                template = agent.get("prompt") or messages[0]["content"]
                agent["_system_prompt_template"] = template

            if "{time_utc}" in template:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                messages[0]["content"] = template.replace("{time_utc}", ts)
            else:
                messages[0]["content"] = template

    # Trim very old context to mitigate runaway prompt growth.  We retain the
    # system prompt plus the *n* most recent messages.  This naive strategy
    # works well enough for voice calls that rarely exceed a dozen turns.
    max_history = int(agent["max_history"])  # Required - no fallback
    if len(messages) > max_history:
        # Preserve system prompt (index 0) and last (max_history-1) others.
        tail_count = max(1, max_history - 1)
        recent_context = messages[1:][-tail_count:]
        messages[:] = [messages[0]] + recent_context

    # Helper: truncate tool IDs for OpenAI's 40-character limit
    def _truncate_tool_ids_for_openai(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a deep copy of *msgs* with tool IDs clipped to the last 40
        characters to satisfy OpenAI's 40-character limit. The original list is
        left unmodified."""
        clipped = copy.deepcopy(msgs)
        for m in clipped:
            # Truncate IDs inside assistant role tool_calls
            for tc in m.get("tool_calls", []) or []:
                if isinstance(tc, dict) and tc.get("id") and len(tc["id"]) > 40:
                    original = tc["id"]
                    tc["id"] = original[-40:]
                    logger.debug("Truncated OpenAI tool id from %s… to …%s", original[:6], tc["id"])
            # Truncate the tool_call_id field for tool role messages
            if m.get("role") == "tool" and m.get("tool_call_id") and len(m["tool_call_id"]) > 40:
                orig_id = m["tool_call_id"]
                m["tool_call_id"] = orig_id[-40:]
                logger.debug("Truncated OpenAI tool_call_id from %s… to …%s", orig_id[:6], m["tool_call_id"])
        return clipped

    # Convenience: wrap litellm.acompletion so we always request streaming.
    async def _stream_llm(
        msgs: list[dict[str, Any]],
        *,
        choice: str | None,
    ):
        """Call the LLM with *stream=True* and return an async iterator.

        When tests monkey-patch ``litellm.acompletion`` they may return a
        *non-stream* response object.  In that case we wrap it in a dummy
        async generator so the rest of the code path still works.
        """

        # Prepare provider-specific message copy (OpenAI requires <=40-char tool IDs)
        send_msgs = (
            _truncate_tool_ids_for_openai(msgs)
            if any(k in agent["model"].lower() for k in ("gpt", "openai"))
            else msgs
        )

        pending = acompletion(
            model=agent["model"],
            messages=send_msgs,
            temperature=agent["temperature"],
            max_tokens=agent["max_tokens"],
            num_retries=3,
            fallbacks=agent.get("fallback_models", []),
            tools=tools if tools else None,
            tool_choice=choice,
            stream=True,
        )

        resp = await pending if inspect.isawaitable(pending) else pending

        iterator = resp
        if hasattr(resp, "__aiter__"):
            aiter_method = resp.__aiter__
            return_value = getattr(aiter_method, "return_value", None)
            if return_value is not None and inspect.isasyncgen(return_value):
                iterator = return_value
            else:
                iterator = aiter_method()
        else:
            raise TypeError(
                "litellm.acompletion must return an async iterator when stream=True; "
                "tests should stub it accordingly"
            )

        if inspect.isawaitable(iterator):
            iterator = await iterator

        if not hasattr(iterator, "__aiter__"):
            if hasattr(iterator, "__anext__"):
                class _AsyncIteratorWrapper:
                    def __init__(self, inner):
                        self._inner = inner

                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        return await self._inner.__anext__()

                iterator = _AsyncIteratorWrapper(iterator)
            else:
                raise TypeError(
                    "litellm.acompletion yielded an object without async iteration support"
                )

        return iterator

    max_tool_loops: int = int(agent.get("max_tool_iterations", 3))
    tool_loops = 0
    tools_disabled = False

    while True:
        if tool_loops >= max_tool_loops and not tools_disabled and tools:
            # Disable tools to force text reply
            logger.warning("Reached max tool iterations (%d); disabling tools for remainder of turn", max_tool_loops)
            tools = []  # Pass empty list to _stream_llm
            tools_disabled = True

        # Decide tool_choice for this request
        choice_mode: str | None
        if tools and not tools_disabled:
            choice_mode = "auto"
        elif tools and tools_disabled:
            choice_mode = "none"       # keep list but tell model not to call
        else:
            choice_mode = None         # no tools parameter → omit tool_choice

        logger.debug(
            f"Calling LLM (tool_choice={choice_mode}, loops={tool_loops}, disabled={tools_disabled}, msg_count={len(messages)})"
        )
        
        # Check for any pending async tool results and update messages
        for i, msg in enumerate(messages):
            if msg.get("role") == "tool" and msg.get("content"):
                try:
                    content_data = json.loads(msg["content"])
                    if isinstance(content_data, dict) and content_data.get("async_execution"):
                        async_id = content_data.get("async_id")
                        if async_id:
                            # Check if result is available
                            result = tf.get_async_result(async_id)
                            if result is not None:
                                # Update the message with the actual result
                                messages[i]["content"] = _safe_json_dumps(result)
                                logger.info(
                                    f"Updated pending async result for tool {content_data.get('tool_name')} at message {i}"
                                )
                except (json.JSONDecodeError, Exception):
                    # Not JSON or other issue, skip
                    pass
        
        # DEBUG: Log message array for debugging reset issue
        logger.debug("Message array content: %s", [{"role": m["role"], "content": str(m.get("content", ""))[:100], "tool_calls": bool(m.get("tool_calls")), "tool_call_id": m.get("tool_call_id")} for m in messages])

        # Recompute dynamic placeholders just before each LLM call
        _refresh_dynamic_prompt()

        # ---------------------- STREAMING ROUND ---------------------------

        # If we have a prefix to announce (because we switched models)
        # yield it *before* we call the LLM so it streams first.
        if announce_prefix is not None:
            yield announce_prefix
            announce_prefix = None

        # Call the LLM – if this raises we may retry with the backup model.
        try:
            start_payload = thinking_audio.start_payload()
            if start_payload is not None:
                yield start_payload

            _t_llm_start = time.perf_counter()
            resp_stream = await _stream_llm(messages, choice=choice_mode)
        except Exception as exc:  # noqa: BLE001 – propagate after trying backup
            logger.exception("LLM call failed for model %s: %s", agent["model"], exc)

            # Attempt fallback model once if configured
            if not used_backup and backup_model and backup_model != agent["model"]:
                logger.warning("Retrying with backup model: %s", backup_model)

                # Switch to backup model and apply optional overrides.
                agent["model"] = backup_model
                if "backup_temperature" in agent:
                    agent["temperature"] = agent["backup_temperature"]
                if "backup_max_tokens" in agent:
                    agent["max_tokens"] = agent["backup_max_tokens"]
                used_backup = True
                announce_prefix = f"{backup_model} says:\n"
                continue  # restart outer loop with backup model

            # No fallback or already failed backup – surface the error downstream
            thinking_audio.stop()
            yield f"{type(exc).__name__}: {exc}"
            return

        # Diagnostics ----------------------------------------------------
        last_finish_reason: str | None = None
        _diag_chunks: list[Any] = []  # capture first few raw chunks

        # Buffers to hold streamed content and any tool calls
        assistant_tokens: list[str] = []
        tool_call_parts: dict[int, dict[str, Any]] = {}
        tool_call_seen: bool = False

        # -----------------------------------------------------------------
        # Consume the stream. If an error occurs mid-stream (e.g. Anthropic
        # returns "Overloaded"), catch it so we can fall back to the backup
        # model rather than crashing the whole turn.
        # -----------------------------------------------------------------
        try:
            async for chunk in resp_stream:  # type: ignore[attribute-defined-outside-init]
                # "chunk" can be either a stream chunk (with .choices[0].delta) or
                # a full response object (when tests monkey-patch a non-stream
                # return).  Unify handling.

                delta: dict[str, Any]
                finish_reason: str | None

                if hasattr(chunk, "choices") and hasattr(chunk.choices[0], "delta"):
                    # Streaming chunk
                    delta = chunk.choices[0].delta  # type: ignore[index]
                    finish_reason = chunk.choices[0].finish_reason  # type: ignore[index]
                else:
                    # Non-stream full response
                    msg = chunk.choices[0].message  # type: ignore[index]
                    delta = {
                        "content": msg.get("content"),
                        "tool_calls": getattr(msg, "tool_calls", None) or msg.get("tool_calls"),
                    }
                    finish_reason = "stop"

                # Accumulate tool call fragments
                tool_calls_delta = delta.get("tool_calls") if delta else None
                if tool_calls_delta:
                    tool_call_seen = True
                    for tc_raw in tool_calls_delta:
                        # Accept either dicts (Vertex/Gemini) or pydantic objects (OpenAI SDK)
                        if hasattr(tc_raw, "model_dump"):
                            tc = tc_raw.model_dump(mode="python")  # type: ignore[arg-type]
                        else:
                            tc = tc_raw  # already dict-like

                        idx: int = tc.get("index", 0)
                        current = tool_call_parts.setdefault(
                            idx,
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )

                        if tc.get("id"):
                            current["id"] = tc["id"]

                        func = tc.get("function", {})
                        if func.get("name"):
                            current["function"]["name"] += func["name"]
                        if func.get("arguments"):
                            current["function"]["arguments"] += func["arguments"]

                # Stream plain content tokens (suppress once a tool call starts)
                if delta and delta.get("content"):
                    token: str = delta["content"]
                    thinking_audio.stop()
                    assistant_tokens.append(token)
                    if not tool_call_seen:
                        yield token

                # Record diagnostics ----------------------------------------
                last_finish_reason = finish_reason or last_finish_reason
                if len(_diag_chunks) < 5:
                    try:
                        _diag_chunks.append(chunk.model_dump(mode="json") if hasattr(chunk, "model_dump") else repr(chunk)[:500])
                    except Exception:
                        _diag_chunks.append(repr(chunk)[:500])

        except Exception as exc:  # noqa: BLE001 – propagate after trying backup
            logger.exception("LLM stream failed mid-iteration for model %s: %s", agent["model"], exc)

            # Attempt fallback model once if configured
            if not used_backup and backup_model and backup_model != agent["model"]:
                logger.warning("Retrying with backup model: %s", backup_model)

                # Switch to backup model and apply optional overrides.
                agent["model"] = backup_model
                if "backup_temperature" in agent:
                    agent["temperature"] = agent["backup_temperature"]
                if "backup_max_tokens" in agent:
                    agent["max_tokens"] = agent["backup_max_tokens"]
                used_backup = True
                announce_prefix = f"{backup_model} says:\n"
                continue  # restart outer loop with backup model

            # No fallback or already failed backup – surface the error downstream
            thinking_audio.stop()
            yield f"{type(exc).__name__}: {exc}"
            return

        # ---------------- Finished streaming this round ------------------

        _t_llm_elapsed = time.perf_counter() - _t_llm_start
        logger.debug(
            f"LLM stream completed – tokens={len(assistant_tokens)}, finish_reason={last_finish_reason}, elapsed={_t_llm_elapsed:.1f}s"
        )

        if not assistant_tokens and not tool_call_parts:
            logger.warning(
                "LLM produced zero content tokens. finish_reason=%s, sample_chunks=%s",
                last_finish_reason,
                _diag_chunks,
            )

            # Try the backup model once if we haven't already.
            if not used_backup and backup_model and backup_model != agent["model"]:
                logger.warning("Primary model returned zero tokens – retrying with backup model '%s'", backup_model)

                # Switch to backup model and apply optional overrides.
                agent["model"] = backup_model
                if "backup_temperature" in agent:
                    agent["temperature"] = agent["backup_temperature"]
                if "backup_max_tokens" in agent:
                    agent["max_tokens"] = agent["backup_max_tokens"]
                used_backup = True
                announce_prefix = f"{backup_model} says:\n"
                continue  # restart outer loop with backup model

            # Even the backup model failed (or none configured) – notify caller.
            thinking_audio.stop()
            yield f"No tokens were produced by {agent['model']}"
            return

        if tool_call_parts:
            # Model invoked at least one tool – execute them and iterate again.
            if tool_loops >= max_tool_loops:
                logger.warning("Reached max tool iterations (%d); aborting tool loop", max_tool_loops)
                thinking_audio.stop()
                yield f" [System: Reached maximum of {max_tool_loops} tool uses. Unable to complete the request.]"
                return  # give up

            tool_loops += 1

            tcs = [tool_call_parts[i] for i in sorted(tool_call_parts)]

            messages.append({
                "role": "assistant",
                "tool_calls": tcs,
                "content": None if not assistant_tokens else "".join(assistant_tokens),
            })

            # Inject tool-specific status messages and execute via ToolRunner
            runner = ToolRunner(
                    status_messages=TOOL_STATUS_MESSAGES,
                    thinking_sounds={"default": []},  # disable periodic thinking chatter
                    interval_sec=2.0,
                )

            for tc in tcs:
                name = tc["function"]["name"]
                args_json = tc["function"].get("arguments", "{}")
                try:
                    args = json.loads(args_json) if isinstance(args_json, str) else args_json
                except json.JSONDecodeError:
                    logger.error("Malformed tool args: %s", args_json)
                    args = {}

                logger.info(f"⚙️⚙️⚙️ Tool call requested: {name}({args})")

                # Tool announcement is handled by ToolRunner as first thinking event

                def _exec_tool(a: dict[str, Any], _n=name):
                    # Wrapper so lambda captures current name
                    return tf.execute_tool(_n, a)

                first_event = True
                _tool_start = time.perf_counter()
                async for ev in runner.run(name, tc.get("id", ""), args, _exec_tool):
                    if ev.kind == "thinking":
                        if first_event:
                            start_payload = thinking_audio.start_payload()
                            if start_payload is not None:
                                yield start_payload
                            # Keep stream alive while tool executes
                            yield {"type": "tool_executing", "tool_count": len(tcs)}
                            first_event = False
                        # Subsequent thinking events are ignored – media continues looping client-side.
                        continue
                    elif ev.kind == "result":
                        _tool_elapsed = time.perf_counter() - _tool_start
                        try:
                            _tool_str = _safe_json_dumps(ev.data)
                            logger.debug(
                                f"Tool '{name}' result bytes={len(_tool_str)} elapsed={_tool_elapsed:.1f}s preview={_tool_str[:2048]}"
                            )
                        except Exception as _exc:
                            logger.error("Failed to serialise tool result for logging: %s", _exc)

                        # Special handling for reset tool
                        if (name == "reset" and isinstance(ev.data, dict) 
                            and ev.data.get("action") == "reset_conversation"):
                            logger.info("Reset tool executed - clearing conversation history")
                            
                            # Add the tool result to messages for logging purposes (old conversation)
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id"),
                                "content": _safe_json_dumps(ev.data),
                            })
                            
                            # Yield a special marker to trigger full reset in WebSocket handler
                            # The WebSocket handler will send the reset message as the new conversation's greeting
                            thinking_audio.stop()
                            yield {"type": "reset_conversation", "message": ev.data["message"]}
                            
                            # End this conversation turn - the old conversation is complete
                            return
                            
                        # Special handling for change_llm tool
                        if (name == "change_llm" and isinstance(ev.data, dict)
                            and ev.data.get("action") == "model_changed"):
                            logger.info("Change LLM tool executed - switching model from %s to %s", 
                                       ev.data.get("previous_model"), ev.data.get("new_model"))
                            
                            # Update agent configuration with new model settings
                            agent["model"] = ev.data["new_model"]
                            agent["temperature"] = ev.data["settings"]["temperature"]  
                            # Note: max_tokens is not changed during model switch
                            
                            logger.info("Agent configuration updated: model=%s, temperature=%s", 
                                       agent["model"], agent["temperature"])
                            
                            # Add the tool call and result to messages
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id"), 
                                "content": _safe_json_dumps(ev.data),
                            })
                            
                            # Return the confirmation message directly 
                            thinking_audio.stop()
                            yield ev.data.get("message", "Changed to %s" % ev.data.get("new_model"))
                            return  # End the conversation turn with the confirmation message
                        
                        # Normal tool result handling
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": _safe_json_dumps(ev.data),
                        })
                        
                        # Check if this is an async tool result placeholder
                        if isinstance(ev.data, dict) and ev.data.get("async_execution"):
                            async_id = ev.data.get("async_id")
                            if async_id:
                                # Find the message we just added
                                msg_index = len(messages) - 1
                                
                                # Define callback to update the message when async completes
                                def update_message(aid: str, result: Dict[str, Any], idx: int = msg_index) -> None:
                                    """Update the message at index with the async result."""
                                    logger.info(f"Async tool completed, updating message at index {idx}")
                                    try:
                                        if idx < len(messages):
                                            # Update the content with the actual result
                                            serialised_result = _safe_json_dumps(result)
                                            messages[idx]["content"] = serialised_result
                                            logger.debug(
                                                f"Updated message {idx} with async result: {serialised_result[:200]}"
                                            )
                                        else:
                                            logger.error(f"Message index {idx} out of range (len={len(messages)})")
                                    except Exception as e:
                                        logger.error(f"Failed to update async message: {e}")
                                
                                # Register the callback
                                tf.register_async_callback(async_id, update_message)
                                logger.debug(f"Registered callback for async tool {name} with id {async_id}")
                # end for ev
            # Loop again to let the LLM generate a textual response
            continue

        # ---------------- No tool calls – final assistant reply ----------

        assistant_content = "".join(assistant_tokens)
        if assistant_content:
            logger.info(f"🤖🤖🤖 Assistant reply: {assistant_content}")

        messages.append({"role": "assistant", "content": assistant_content})
        thinking_audio.stop()
        return  # done
