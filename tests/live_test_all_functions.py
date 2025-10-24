#!/usr/bin/env python3
"""End-to-end voice call test that exercises all major Ringdown tools in a **single**
Twilio call.

The script re-uses the helper functions from ``live_test_call`` so that we
leverage the existing TTS generation, chained-audio logic, Cloud-Run log
collection and (optional) LLM-based log evaluation.

Test flow (all prompts are spoken via TTS and separated by configurable periods
of silence – defaults to 15 s):

1. Ask the bot to switch its underlying model to **gpt-5-mini** – this both
   exercises the ``change_llm`` tool and ensures the remainder of the test runs
   on a cheaper model.
2. Create a Google Doc – ``CreateGoogleDoc``
3. Perform a Tavily web search – ``TavilySearch``
4. Send an email - ``SendEmail`` (from ``app.tools.email``)
5. Schedule a calendar event - ``CreateCalendarEvent``
6. Delete the calendar event - ``DeleteCalendarEvent``
7. Reset conversation memory - ``reset`` tool
8. Ask a memory check follow-up
9. Tell the assistant to hang up so Twilio, not the harness, terminates the call

Everything happens within a single call so that we stress long-context handling
instead of many short calls.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import List

import click

# Ensure project root is on PYTHONPATH regardless of invocation location
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

# pylint: disable=wrong-import-position
from live_test_call import (
    generate_tts_audio,
    make_chained_test_call,
    prepare_log_evaluation_prompt,
    evaluate_logs_with_llm,
    DEFAULT_SERVICE_NAME,
    DEFAULT_SERVICE_REGION,
    DEFAULT_PROJECT_ID,
    DEFAULT_TO_NUMBER,
)
PROMPTS: List[str] = [
    # Change model (must be first)
    "Switch your model to gpt five mini.",
    "Search the web for the latest news about OpenAI's research breakthroughs and summarize the key points in a single sentence.",
    "Create a new Google Doc titled 'Integration Test Document' and write 'Hello from the automated integration test!' inside.",
    "Search for a document with the word parsnip in the title.",
    "Read the document to find out dan's alternate email and tell me what it is."
    "Send the sentence about OpenAI's research breakthroughs, nicely formatted, to dan's alternate email address, subject 'integration test'.",
    "Add a calendar event titled 'Ringdown Integration Test' tomorrow at noon lasting 30 minutes.",
    "Delete the calendar event you just scheduled.",
    "Reset your conversation memory now.",
    "What is Dan's alternate email?",
    "Hang up this call.",
]

@click.command()
@click.option("--to", "to_number", default=DEFAULT_TO_NUMBER, help="Phone number to call")
@click.option("--silence-timeout", default=15, type=int, help="Seconds of silence between prompts")
@click.option("--tts-voice", default="alloy", help="OpenAI TTS voice preset")
@click.option("--tts-model", default="tts-1", help="OpenAI TTS model")
@click.option("--evaluate-logs/--no-evaluate-logs", default=True, help="Run LLM analysis of Cloud-Run logs")
@click.option("--log-model", default="gpt-5", help="Model to use for log evaluation (default: gpt-5)")
@click.option("--debug", is_flag=True, help="Enable verbose debug output")
@click.option("--no-logs", is_flag=True, help="Disable Cloud Run log retrieval")
def main(
    to_number: str,
    silence_timeout: int,
    tts_voice: str,
    tts_model: str,
    evaluate_logs: bool,
    log_model: str,
    debug: bool,
    no_logs: bool,
):
    """Run the full-stack integration call.

    Environment variables such as TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and
    OPENAI_API_KEY need to be set – identical to requirements for
    ``live_test_call``.
    """

    # ------------------------------------------------------------------
    # 1. Generate TTS audio for every prompt
    # ------------------------------------------------------------------
    audio_files: List[str] = []
    for idx, txt in enumerate(PROMPTS, 1):
        click.echo(f"🔊 Generating TTS {idx}/{len(PROMPTS)} …")
        audio_path = generate_tts_audio(txt, voice=tts_voice, model=tts_model)
        audio_files.append(str(audio_path))

    # ------------------------------------------------------------------
    # 2. Make *one* chained test call
    # ------------------------------------------------------------------
    click.echo("📞 Initiating chained test call …")
    (
        call_sid,
        logs_output,
        local_call_duration,
        total_audio_duration,
        individual_durations,
    ) = make_chained_test_call(
        to_number=to_number,
        from_number=None,
        audio_files=audio_files,
        silence_timeout=silence_timeout,
        project_id=DEFAULT_PROJECT_ID,
        service_name=DEFAULT_SERVICE_NAME,
        region=DEFAULT_SERVICE_REGION,
        enable_log_monitoring=not no_logs,
        debug=debug,
    )

    click.echo(f"✅ Call completed. SID={call_sid}, duration~{local_call_duration} s")

    # ------------------------------------------------------------------
    # 3. Optional log evaluation
    # ------------------------------------------------------------------
    if evaluate_logs and logs_output and logs_output.strip():
        click.echo("🧐 Running LLM log evaluation …")
        prompt = prepare_log_evaluation_prompt(
            user_texts=PROMPTS,
            silence_timeout=silence_timeout,
            individual_durations=individual_durations,
            call_duration=int(local_call_duration),
            logs=logs_output,
        )
        analysis = evaluate_logs_with_llm(prompt, model=log_model)
        click.echo("\n" + "=" * 80)
        click.echo("🤖 LLM LOG ANALYSIS")
        click.echo("=" * 80 + "\n")
        click.echo(analysis)
    elif evaluate_logs:
        click.echo("⚠️  Log evaluation requested but no logs were captured.")


if __name__ == "__main__":
    main()
