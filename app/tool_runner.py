from __future__ import annotations

"""Utility to execute tools while streaming announcement and thinking sounds.

Motivation
=========
* `app.chat.stream_response` became bloated with per-tool timers and filler
  sounds.  Extract that responsibility into a dedicated helper so the
  streaming loop focuses on the LLM orchestration.

Design
======
* `ToolRunner` is initialised once with:
  • `status_messages` – mapping tool-name → announcement text
  • `thinking_sounds` – mapping tool-name → list[str] **or** `[]`/`None` to
    suppress sounds for that tool
* At runtime `run()` executes the given tool function in a background thread
  while yielding a deterministic sequence of events:

      announce(thinking)  →  additional thinking  → result

* Each **thinking** event picks **one** random element from the configured
  list.  The caller converts these events into whatever output tokens the
  front-end expects (e.g. WebSocket `{"type":"thinking_sound", …}`).
"""

import asyncio
import contextlib
import logging
import random
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, NamedTuple

logger = logging.getLogger(__name__)


class ToolEvent(NamedTuple):
    """Lightweight event emitted by :py:meth:`ToolRunner.run`."""

    kind: str  # "thinking" | "result"  (announcement is a thinking event)
    text: str | None = None  # for announce/thinking
    data: Any = None  # for result


class ToolRunner:
    """Run a tool while streaming announcement and thinking events."""

    def __init__(
        self,
        *,
        status_messages: Mapping[str, str] | None = None,
        thinking_sounds: Mapping[str, List[str] | None] | None = None,
        interval_sec: float = 2.0,
    ) -> None:
        self._interval = max(interval_sec, 0.2)

        # Defaults – callers may override via kwargs
        self._status_messages: Dict[str, str] = {
            "default": "Processing…",
            **(status_messages or {}),
        }
        self._thinking_sounds: Dict[str, List[str] | None] = {
            "default": [
                "hmm…",
                "checking…",
                "ok…",
                "still working…",
            ],
            **(thinking_sounds or {}),
        }

    # ---------------------------------------------------------------------
    # Public helpers
    # ---------------------------------------------------------------------

    def status_message(self, tool_name: str) -> str:
        return self._status_messages.get(tool_name, self._status_messages["default"])

    def sound_list(self, tool_name: str) -> List[str]:
        sounds = self._thinking_sounds.get(tool_name, self._thinking_sounds["default"])
        # Normalise: None → [], copy so callers can't mutate internal list.
        return list(sounds or [])

    # ------------------------------------------------------------------
    # Core execution – async generator
    # ------------------------------------------------------------------

    async def run(
        self,
        tool_name: str,
        call_id: str,
        args: Dict[str, Any],
        exec_fn: Callable[[Dict[str, Any]], Any],
    ) -> AsyncIterator[ToolEvent]:
        """Execute *exec_fn(args)* while yielding :class:`ToolEvent` objects.

        Parameters
        ----------
        tool_name:
            Name of the tool – used for status/thinking overrides.
        call_id:
            LLM-generated call-id (unused for now, but useful for tracing).
        args:
            Dict passed verbatim to *exec_fn*.
        exec_fn:
            Synchronous callable that executes the tool.
        """

        # 1. Emit announcement as first "thinking" event --------------
        announcement = self.status_message(tool_name)
        logger.debug("ToolRunner: announcing %s", announcement)
        yield ToolEvent("thinking", text=announcement)

        # 2. Launch tool in a background thread ------------------------
        tool_task = asyncio.create_task(asyncio.to_thread(exec_fn, args))

        # If the tool finishes very quickly (<interval) we skip thinking sounds.
        thinking_sounds = self.sound_list(tool_name)
        first_wait = True

        result: Any | None = None

        try:
            while True:
                try:
                    await asyncio.wait_for(asyncio.shield(tool_task), timeout=self._interval)
                    # Reached if task completed without Timing out – success or error
                    break
                except asyncio.TimeoutError:
                    # Task still running.
                    if thinking_sounds:
                        sound = random.choice(thinking_sounds)
                        logger.debug("ToolRunner: thinking sound '%s'", sound)
                        yield ToolEvent("thinking", text=sound)
                    # Emit at most once immediately if tool is quick so users
                    # perceive responsiveness.
                    if first_wait:
                        first_wait = False
                    continue
                except Exception as exc:  # Tool raised whilst awaiting
                    logger.exception("Tool '%s' raised during wait: %s", tool_name, exc)
                    result = {"error": str(exc)}
                    break

            # Ensure we have result if not captured yet
            if result is None:
                try:
                    result = tool_task.result()
                except Exception as exc:  # noqa: BLE001 – capture for downstream handling
                    logger.exception("Tool '%s' raised: %s", tool_name, exc)
                    result = {"error": str(exc)}

            yield ToolEvent("result", data=result)
        finally:
            # Defensive: if caller forgets to exhaust iterator, avoid leaks.
            if not tool_task.done():
                tool_task.cancel()
                with contextlib.suppress(Exception):
                    await tool_task 