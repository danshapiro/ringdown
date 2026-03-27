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

import asyncio
import json
import os
import sys
from pathlib import Path

import click

# Ensure project root is on PYTHONPATH regardless of invocation location
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

# pylint: disable=wrong-import-position
from app.mobile.smoke import SmokeTestError, run_remote_smoke  # noqa: E402
from live_test_call import (  # noqa: E402
    DEFAULT_PROJECT_ID,
    DEFAULT_SERVICE_NAME,
    DEFAULT_SERVICE_REGION,
    DEFAULT_TO_NUMBER,
    _run_cmd,
    evaluate_logs_with_llm,
    generate_tts_audio,
    make_chained_test_call,
    prepare_log_evaluation_prompt,
)

PROMPTS: list[str] = [
    # Change model (must be first)
    "Switch your model to gpt five mini.",
    (
        "Search the web for the latest news about OpenAI's research "
        "breakthroughs and summarize the key points in a single sentence."
    ),
    (
        "Create a new Google Doc titled 'OpenAI update "
        "(Danbot integration test)'. The body is the openai summary you just "
        "prepared."
    ),
    "Search for a document with the word parsnip in the title.",
    (
        "Read the document to find out dan's alternate email and tell me what "
        "it is."
        "Send the sentence about OpenAI's research breakthroughs, nicely "
        "formatted, to dan's alternate email address, subject 'integration "
        "test'."
    ),
    "Add a calendar event titled 'Ringdown Integration Test' tomorrow at noon lasting 30 minutes.",
    "Delete the calendar event you just scheduled.",
    "Reset your conversation memory now.",
    "Have I mentioned a parsnip in this conversation?",
    "Hang up this call.",
]


def _format_tail(records: object) -> str:
    if not records:
        return "[]"
    try:
        return json.dumps(list(records)[-3:], default=str)
    except Exception:  # noqa: BLE001
        return repr(list(records)[-3:])


@click.command()
@click.option("--to", "to_number", default=DEFAULT_TO_NUMBER, help="Phone number to call")
@click.option("--silence-timeout", default=15, type=int, help="Seconds of silence between prompts")
@click.option("--tts-voice", default="alloy", help="OpenAI TTS voice preset")
@click.option("--tts-model", default="tts-1", help="OpenAI TTS model")
@click.option(
    "--evaluate-logs/--no-evaluate-logs", default=True, help="Run LLM analysis of Cloud-Run logs"
)
@click.option(
    "--log-model", default="gpt-5.2", help="Model to use for log evaluation (default: gpt-5.2)"
)
@click.option("--debug", is_flag=True, help="Enable verbose debug output")
@click.option("--no-logs", is_flag=True, help="Disable Cloud Run log retrieval")
@click.option(
    "--mobile-device-id",
    default=None,
    help="Mobile text smoke-test device id (defaults to LIVE_TEST_MOBILE_DEVICE_ID).",
)
def main(
    to_number: str,
    silence_timeout: int,
    tts_voice: str,
    tts_model: str,
    evaluate_logs: bool,
    log_model: str,
    debug: bool,
    no_logs: bool,
    mobile_device_id: str | None,
):
    """Run the full-stack integration call.

    Environment variables such as TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and
    OPENAI_API_KEY need to be set – identical to requirements for
    ``live_test_call``.
    """

    # ------------------------------------------------------------------
    # 1. Generate TTS audio for every prompt
    # ------------------------------------------------------------------
    audio_files: list[str] = []
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

    resolved_device_id = mobile_device_id or os.environ.get("LIVE_TEST_MOBILE_DEVICE_ID")
    if resolved_device_id:
        base_url = None
        try:
            base_url = os.environ.get("LIVE_TEST_BASE_URL")
            if not base_url:
                if not (DEFAULT_SERVICE_NAME and DEFAULT_SERVICE_REGION and DEFAULT_PROJECT_ID):
                    raise click.ClickException(
                        "Unable to resolve Cloud Run service for mobile text smoke test."
                    )
                cmd = (
                    "gcloud run services describe "
                    f"{DEFAULT_SERVICE_NAME} "
                    f"--region {DEFAULT_SERVICE_REGION} "
                    f"--project {DEFAULT_PROJECT_ID} "
                    '--format="value(status.url)"'
                )
                base_url = _run_cmd(cmd)

            base_url = (base_url or "").strip().rstrip("/")
            if not base_url:
                click.echo("!! Skipping mobile text smoke test: service URL unavailable.")
            else:
                click.echo(">> Running mobile text smoke test ...")
                result = asyncio.run(
                    run_remote_smoke(
                        base_url=base_url,
                        device_id=resolved_device_id,
                        prompt_text="Mobile text live smoke verification.",
                        timeout=30.0,
                    )
                )
                preview = (result.response_text or "")[:60]
                click.echo(
                    ">> Mobile text smoke test succeeded "
                    f"(session {result.session_id}, response '{preview}...')."
                )
        except SmokeTestError as exc:
            event_tail = _format_tail(getattr(exc, "events", None))
            log_tail = _format_tail(getattr(exc, "logs", None))
            raise click.ClickException(
                "Mobile text smoke test failed: "
                f"{exc or exc.__class__.__name__}; "
                f"events_tail={event_tail}; logs_tail={log_tail}; "
                f"base_url={base_url or 'unknown'}; device_id={resolved_device_id}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise click.ClickException(
                "Mobile text smoke test failed "
                f"(unexpected {exc.__class__.__name__}): {exc}"
            ) from exc
    else:
        click.echo("!! Skipping mobile text smoke test: LIVE_TEST_MOBILE_DEVICE_ID not provided.")


if __name__ == "__main__":
    main()
