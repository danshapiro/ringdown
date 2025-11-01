#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio
import os
import sys
from typing import Any, Dict

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.transports.daily.transport import DailyParams, DailyTransport

from config_loader import load_config

load_dotenv(override=True)

LOCAL_RUN = os.getenv("LOCAL_RUN")

logger.add(sys.stderr, level="DEBUG")

loaded_config = load_config()

SYSTEM_INSTRUCTION = loaded_config["system_instruction"]
GEMINI_MODEL = loaded_config["gemini_model"]
VOICE_ID = loaded_config["voice_id"]
TRANSCRIBE_USER_AUDIO = loaded_config["transcribe_user_audio"]
INFERENCE_ON_CONTEXT_INIT = loaded_config["inference_on_context_init"]
gemini_tools_list = loaded_config["gemini_tools_list"]
gemini_input_params = loaded_config["gemini_input_params"]


async def main(transport: DailyTransport, config: Dict[str, Any]):
    logger.debug("Initializing assistant bot with loaded config")

    llm = GeminiLiveLLMService(
        api_key=os.getenv("GOOGLE_API_KEY"),
        model=GEMINI_MODEL,
        voice_id=VOICE_ID,
        system_instruction=SYSTEM_INSTRUCTION,
        transcribe_user_audio=TRANSCRIBE_USER_AUDIO,
        inference_on_context_initialization=INFERENCE_ON_CONTEXT_INIT,
        tools=gemini_tools_list,
        params=gemini_input_params,
    )

    pipeline = Pipeline(
        [
            transport.input(),
            llm,
            transport.output(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        logger.info("First participant joined: {}", participant["id"])

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        logger.info("Participant left: {}", participant)
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(task)


async def bot(args):
    room_url = getattr(args, "room_url", None)
    token = getattr(args, "token", None)
    body = getattr(args, "body", {})

    if not room_url or not token:
        logger.error("Missing room_url or token in arguments.")
        return

    logger.info(f"Bot process initializing for room {room_url}")

    transport = DailyTransport(
        room_url,
        token,
        "Assistant Bot",
        DailyParams(
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    try:
        await main(transport, body)
        logger.info("Bot process completed")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error in bot process: {str(e)}")
        raise


async def local_daily():
    import aiohttp
    try:
        from server.runner_web import configure
    except ImportError:
        logger.error("Could not import runner module. Local development requires runner.py.")
        return

    try:
        async with aiohttp.ClientSession() as session:
            (room_url, token) = await configure(session)
            transport = DailyTransport(
                room_url,
                token,
                "Assistant Bot (Local)",
                params=DailyParams(
                    audio_out_enabled=True,
                    vad_analyzer=SileroVADAnalyzer(),
                ),
            )
            await main(transport, {})
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Error in local development mode: {e}")


if LOCAL_RUN and __name__ == "__main__":
    try:
        asyncio.run(local_daily())
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Failed to run in local mode: {e}")
