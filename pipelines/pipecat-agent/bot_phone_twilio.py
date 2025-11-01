#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#
# For documentation on GeminiMultimodalLiveLLMService parameters, see:
# .venv/Lib/site-packages/pipecat/services/gemini_multimodal_live/gemini.py
# or https://github.com/pipecat-ai/pipecat/blob/main/src/pipecat/services/gemini_multimodal_live/gemini.py

"""danbot: A voice-based chatbot.

This demo version is intended to be deployed to
Pipecat Cloud. For more information, visit:
- Deployment Quickstart: https://docs.pipecat.daily.co/quickstart
- Build for Twilio: https://docs.pipecat.daily.co/pipecat-in-production/telephony/twilio-mediastreams
"""

import asyncio
import json
import os
import sys
import time
from typing import Dict, Optional, Coroutine, Callable, Any

from dotenv import load_dotenv
from fastapi import WebSocket
from loguru import logger

try:
    from pipecat.audio.filters.krisp_filter import KrispFilter
except Exception as import_error:  # noqa: BLE001
    logger.warning("Krisp filter unavailable: {}", import_error)
    KrispFilter = None
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    VADUserStartedSpeakingFrame,
    UserStartedSpeakingFrame,
    LLMMessagesAppendFrame,
    Frame,  # Needed for event handler type hint
    # --- Add standard frame types for logging ---
    TranscriptionFrame,
    LLMTextFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    # --- End add standard frame types ---
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.gemini_multimodal_live.gemini import (
    GeminiMultimodalLiveLLMService,
)
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

# Import the config loader
from config_loader import load_config

load_dotenv(override=True)

logger.add(sys.stderr, level="DEBUG")

# --- Load Configuration ---
# All config loading and validation is now handled by load_config()
loaded_config = load_config()

SYSTEM_INSTRUCTION = loaded_config["system_instruction"]
PROACTIVE_INTRO_TEXT = loaded_config["proactive_intro_text"]
PROACTIVE_GREETING_DELAY_S = loaded_config["proactive_greeting_delay_s"]
OUTPUT_BUFFER_FLUSH_DELAY = loaded_config["output_buffer_flush_delay"]
GEMINI_MODEL = loaded_config["gemini_model"]
VOICE_ID = loaded_config["voice_id"]
TRANSCRIBE_USER_AUDIO = loaded_config["transcribe_user_audio"]
INFERENCE_ON_CONTEXT_INIT = loaded_config["inference_on_context_init"]
gemini_tools_list = loaded_config["gemini_tools_list"]  # Get the tools list
gemini_input_params = loaded_config["gemini_input_params"]
# --- End Load Configuration ---


# --- Silence Monitor ---


class SilenceMonitor:
    """
    Monitors user silence and triggers actions after a specified duration,
    resetting the timer if the user speaks.
    """

    def __init__(self, task_manager: Any):
        self._task_manager = task_manager
        self._last_speech_time: Optional[float] = None
        self._scheduled_actions: Dict[str, asyncio.Task] = {}

    def notify_speech_detected(self):
        """Call this when user speech is detected (e.g., VAD start)."""
        self._last_speech_time = time.monotonic()
        # We don't cancel tasks here; the tasks themselves check the time upon waking.

    async def schedule_action_after_silence(
        self,
        action_id: str,
        delay_secs: float,
        action_callable: Callable[[], Coroutine],
    ):
        """Schedules an awaitable callable to run after a period of silence."""
        existing_task = self._scheduled_actions.pop(action_id, None)
        if existing_task and not existing_task.done():
            logger.debug(
                f"Replacing existing scheduled action [{action_id}] for new schedule. Cancelling via task_manager."
            )
            await self._task_manager.cancel_task(existing_task, timeout=1.0)

        schedule_time = time.monotonic()
        logger.debug(
            f"Scheduling silence action [{action_id}] with delay {delay_secs}s at {schedule_time=}."
        )

        wrapper_task = self._task_manager.create_task(
            self._silence_task_wrapper(
                delay_secs, action_callable, action_id, schedule_time
            ),
            name=f"SilenceMonitor_{action_id}",
        )
        self._scheduled_actions[action_id] = wrapper_task

    async def _silence_task_wrapper(
        self,
        delay_secs: float,
        action_callable: Callable[[], Coroutine],
        action_id: str,
        schedule_time: float,  # Retaining schedule_time for accurate check
    ):
        """Internal wrapper that runs the scheduled action after checking silence."""
        this_task = asyncio.current_task()
        try:
            await asyncio.sleep(delay_secs)

            user_spoke_since_schedule = (
                self._last_speech_time is not None
                and self._last_speech_time > schedule_time
            )

            if user_spoke_since_schedule:
                logger.info(
                    f"Silence action [{action_id}] aborted: User spoke after task was scheduled (at {self._last_speech_time=:.3f} > {schedule_time=:.3f})."
                )
                return

            logger.info(
                f"Silence action [{action_id}]: {delay_secs:.1f}s elapsed without user speech since scheduling. Running action."
            )
            await action_callable()
            logger.info(
                f"Silence action [{action_id}]: Action completed successfully."
            )

        except asyncio.CancelledError:
            logger.info(
                f"Silence action [{action_id}] cancelled during execution or sleep."
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Silence action [{action_id}]: Error running action: {e}")
        finally:
            if self._scheduled_actions.get(action_id) is this_task:
                del self._scheduled_actions[action_id]
            logger.debug(f"Silence action [{action_id}]: Task wrapper exiting.")

    async def cancel_action(self, action_id: str):
        task = self._scheduled_actions.pop(action_id, None)
        if task and not task.done():
            logger.debug(
                f"Cancelling and awaiting silence action [{action_id}] via task_manager."
            )
            await self._task_manager.cancel_task(task, timeout=1.0)
            logger.debug(f"Silence action [{action_id}] cancellation awaited.")
        elif task:
            logger.debug(
                f"Silence action [{action_id}] was already done when cancel_action was called."
            )

    async def cancel_all_actions(self):
        num_actions = len(self._scheduled_actions)
        logger.debug(
            f"Cancelling all {num_actions} scheduled actions in SilenceMonitor directly via TaskManager."
        )

        actions_to_process = list(self._scheduled_actions.items())

        for action_id, task_wrapper in actions_to_process:
            if task_wrapper and hasattr(task_wrapper, "done") and not task_wrapper.done():
                task_name = (
                    task_wrapper.get_name()
                    if hasattr(task_wrapper, "get_name")
                    else "Unnamed Task"
                )
                logger.debug(
                    f"Attempting to cancel action [{action_id}], task [{task_name}] via task_manager in cancel_all_actions."
                )
                try:
                    await self._task_manager.cancel_task(task_wrapper, timeout=1.0)
                    logger.debug(
                        f"Action [{action_id}] task [{task_name}] cancellation initiated/awaited."
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        f"Error cancelling action [{action_id}] task [{task_name}] in cancel_all_actions: {e}"
                    )
            elif not task_wrapper or not hasattr(task_wrapper, "done"):
                logger.warning(
                    f"Found an item for action [{action_id}] in _scheduled_actions that is not a valid task or lacks 'done' method: {task_wrapper}"
                )

        self._scheduled_actions.clear()
        logger.debug(
            "Finished cancelling all actions in SilenceMonitor and cleared _scheduled_actions."
        )


# --- End Silence Monitor ---


async def main(ws: WebSocket):
    logger.debug("Starting WebSocket bot")

    start_data = ws.iter_text()
    await start_data.__anext__()
    logger.info("Twilio WebSocket connected.")
    call_data = json.loads(await start_data.__anext__())

    stream_sid = call_data["start"]["streamSid"]
    call_sid = call_data["start"]["callSid"]
    logger.info(f"Connected to Twilio call: CallSid={call_sid}, StreamSid={stream_sid}")

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
    )

    transport = FastAPIWebsocketTransport(
        websocket=ws,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_in_filter=KrispFilter() if KrispFilter else None,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=serializer,
        ),
    )

    llm = GeminiMultimodalLiveLLMService(
        api_key=os.getenv("GOOGLE_API_KEY"),
        model=GEMINI_MODEL,
        voice_id=VOICE_ID,
        system_instruction=SYSTEM_INSTRUCTION,
        transcribe_user_audio=TRANSCRIBE_USER_AUDIO,
        inference_on_context_initialization=INFERENCE_ON_CONTEXT_INIT,
        tools=gemini_tools_list,
        params=gemini_input_params,
    )

    pipeline = Pipeline([
        transport.input(),
        llm,
        transport.output(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_out_sample_rate=8000,
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    task_manager = task._task_manager
    silence_monitor = SilenceMonitor(task_manager)

    llm_output_buffer = ""
    llm_flush_task: Optional[asyncio.Task] = None

    async def flush_llm_buffer():
        nonlocal llm_output_buffer
        if llm_output_buffer:
            lines_to_log = llm_output_buffer.split("\n")
            for line in lines_to_log:
                if line.strip():
                    logger.info(f"LLM said: {line.strip()}")
            llm_output_buffer = ""

    async def _llm_flush_task_wrapper():
        nonlocal llm_flush_task
        this_task = asyncio.current_task()
        try:
            await asyncio.sleep(OUTPUT_BUFFER_FLUSH_DELAY)
            logger.debug(
                f"LLM buffer flush timer ({OUTPUT_BUFFER_FLUSH_DELAY}s) expired."
            )
            await flush_llm_buffer()
        except asyncio.CancelledError:
            logger.debug("LLM buffer flush timer cancelled.")
        finally:
            if llm_flush_task is this_task:
                llm_flush_task = None

    async def schedule_or_reset_llm_flush_timer():
        nonlocal llm_flush_task
        if llm_flush_task and not llm_flush_task.done():
            logger.debug(
                f"Cancelling existing LLMBufferFlushTimer: {llm_flush_task.get_name()} before scheduling new one."
            )
            await task_manager.cancel_task(llm_flush_task, timeout=1.0)
        llm_flush_task = task_manager.create_task(
            _llm_flush_task_wrapper(), name="LLMBufferFlushTimer"
        )
        logger.debug(
            f"Scheduled/Reset LLM buffer flush timer task: {llm_flush_task.get_name()}"
        )

    task.set_reached_upstream_filter((TranscriptionFrame,))
    task.set_reached_downstream_filter(
        (
            VADUserStartedSpeakingFrame,
            UserStartedSpeakingFrame,
            LLMTextFrame,
        )
    )

    async def send_intro_request():
        intro_message = [{"role": "user", "content": PROACTIVE_INTRO_TEXT}]
        logger.debug(
            f"Silence action [{PROACTIVE_INTRO_ACTION_ID}]: Queuing LLMMessagesAppendFrame with: {intro_message}"
        )
        await transport.input().queue_frame(
            LLMMessagesAppendFrame(messages=intro_message),
            FrameDirection.DOWNSTREAM,
        )

    @task.event_handler("on_frame_reached_upstream")
    async def handle_upstream_frame(task: PipelineTask, frame: Frame):
        nonlocal llm_flush_task
        if isinstance(frame, TranscriptionFrame) and frame.text:
            task_to_cancel = llm_flush_task
            if task_to_cancel and not task_to_cancel.done():
                logger.debug(
                    f"User spoke, cancelling pending LLM flush timer {task_to_cancel.get_name()} via task_manager."
                )
                await task_manager.cancel_task(task_to_cancel, timeout=1.0)
            await flush_llm_buffer()
            logger.info(f"USER_TRANSCRIPTION: {frame.text}")

    @task.event_handler("on_frame_reached_downstream")
    async def handle_downstream_frame(task: PipelineTask, frame: Frame):
        nonlocal llm_output_buffer
        if isinstance(frame, LLMTextFrame) and frame.text:
            llm_output_buffer += frame.text
            await schedule_or_reset_llm_flush_timer()
        elif isinstance(frame, LLMFullResponseStartFrame):
            logger.debug("LLM_RESPONSE_START")
        elif isinstance(frame, LLMFullResponseEndFrame):
            logger.debug("LLM_RESPONSE_END")
        elif isinstance(frame, (VADUserStartedSpeakingFrame, UserStartedSpeakingFrame)):
            logger.debug(
                f"User speech frame reached downstream at {time.monotonic():.3f}s. Notifying SilenceMonitor."
            )
            silence_monitor.notify_speech_detected()

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport: FastAPIWebsocketTransport, client: str):
        logger.info(f"Client connected: {client}")
        silence_monitor.notify_speech_detected()
        await silence_monitor.schedule_action_after_silence(
            action_id=PROACTIVE_INTRO_ACTION_ID,
            delay_secs=PROACTIVE_GREETING_DELAY_S,
            action_callable=send_intro_request,
        )
        logger.info(
            f"Scheduled proactive intro task (will run after {PROACTIVE_GREETING_DELAY_S}s of silence)."
        )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport: FastAPIWebsocketTransport, client: str):
        nonlocal llm_flush_task
        logger.info(f"Client disconnected: {client}")
        await silence_monitor.cancel_all_actions()

        task_to_cancel = llm_flush_task
        if task_to_cancel and not task_to_cancel.done():
            logger.debug(
                f"Client disconnected, cancelling pending LLM flush timer {task_to_cancel.get_name()} via task_manager."
            )
            await task_manager.cancel_task(task_to_cancel, timeout=1.0)

        await flush_llm_buffer()
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(task)


# Main entry point for Pipecat Cloud
async def bot(args):
    """Main bot entry point for WebSocket connections."""
    if hasattr(args, "room_url") and hasattr(args, "token") and not hasattr(args, "websocket"):
        logger.info("Daily session detected; delegating to Daily transport bot")
        from bot_web import bot as daily_bot

        await daily_bot(args)
        return

    logger.info("WebSocket bot process initialized")

    try:
        ws = getattr(args, "websocket", args)
        await main(ws)
        logger.info("WebSocket bot process completed")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error in WebSocket bot process: {str(e)}")
        raise
