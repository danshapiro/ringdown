#!/usr/bin/env python3
"""Outbound call test script for voicebot testing.

This script tests the Ringdown LLM assistant Twilio service by making an outbound call TO the bot itself.
The test scenario is:
1. Script calls Ringdown's phone number
2. Ringdown answers and begins its welcome greeting
3. Script plays combined TTS audio (with pauses between prompts)
4. Script hangs up immediately after audio playback completes
5. Call duration exactly matches TTS + pause duration

This is a BOT-TO-BOT test - we're not calling a human, we're calling the Ringdown service
to test its audio processing, transcription, and conversation flow with precise timing.
The "interrupt" events in logs are the bot detecting audio from our test TTS, not human speech.

For chained prompts, multiple TTS files are combined with silence gaps to maintain 
conversation context while ensuring proper timing between prompts.

During the call, retrieves and displays Cloud Run logs from the deployed service.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional, Callable
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
import time
import click
import subprocess
import datetime as dt
from zoneinfo import ZoneInfo
import requests
import tempfile
from pydub import AudioSegment
import json
from click.testing import CliRunner
import pytest

pytestmark = pytest.mark.live

try:
    from dotenv import load_dotenv
except ImportError:  # Gracefully continue if python-dotenv is unavailable
    def load_dotenv(*_: object, **__: object) -> None:  # type: ignore
        return None

load_dotenv(override=False)

# Ensure project root is on PYTHONPATH regardless of script location
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

# Import utils AFTER ensuring the project root is on sys.path
from utils.mp3_uploader import upload_mp3_to_twilio as upload_mp3_to_twilio_util  # noqa: E402

from app.settings import get_env
from log_love import setup_logging

# Setup logging
logger = setup_logging()

# Constants
_PLACEHOLDER_DEMO_NUMBER = "+15555550100"  # Legacy demo fallback
DEFAULT_LOG_ENTRY_LIMIT = 500
TWIML_BIN_URL = None  # Will be set dynamically if hosting MP3
DEFAULT_TWIML_REDIRECT = os.environ.get("RINGDOWN_TWIML_URL")


def _resolve_default_to_number() -> str:
    """Determine the Twilio destination number for live tests."""

    env_override = os.environ.get("LIVE_TEST_TO_NUMBER")
    if env_override:
        return env_override

    try:
        env_settings = get_env()
    except RuntimeError:
        # Required secrets missing; fall back to the legacy placeholder so the
        # script still surfaces credential errors later during execution.
        return _PLACEHOLDER_DEMO_NUMBER

    return env_settings.live_test_to_number or _PLACEHOLDER_DEMO_NUMBER


DEFAULT_TO_NUMBER = _resolve_default_to_number()


def _run_gcloud(args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["gcloud", *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:  # noqa: BLE001 – gcloud may not be installed in some environments
        return None

    if proc.returncode != 0:
        return None

    value = (proc.stdout or "").strip()
    if value and value != "(unset)":
        return value
    return None


def _discover_service_defaults(project_id: str) -> tuple[str | None, str | None]:
    if not project_id:
        return None, None

    value = _run_gcloud(
        [
            "run",
            "services",
            "list",
            "--platform",
            "managed",
            f"--project={project_id}",
            "--format=value(name,region)",
            "--limit",
            "1",
        ]
    )
    if not value:
        return None, None

    first_line = value.splitlines()[0].strip()
    if not first_line:
        return None, None

    parts = first_line.split()
    if len(parts) == 2:
        return parts[0], parts[1]

    # Older gcloud versions may separate fields with commas
    alt_parts = [p.strip() for p in first_line.split(",") if p.strip()]
    if len(alt_parts) == 2:
        return alt_parts[0], alt_parts[1]

    return None, None


def _resolve_setting(
    env_vars: tuple[str, ...],
    *,
    fallback: str,
    gcloud_fetcher: Callable[[], Optional[str]] | None = None,
) -> str:
    for env_key in env_vars:
        val = os.environ.get(env_key)
        if val:
            return val

    if gcloud_fetcher:
        value = gcloud_fetcher()
        if value:
            return value

    return fallback


DEFAULT_PROJECT_ID = _resolve_setting(
    (
        "LIVE_TEST_PROJECT_ID",
        "RINGDOWN_PROJECT_ID",
        "DEPLOY_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
    ),
    fallback="",
    gcloud_fetcher=lambda: _run_gcloud(["config", "get-value", "project"]),
)

_DISCOVERED_SERVICE_INFO: tuple[str | None, str | None] | None = None


def _ensure_service_info() -> tuple[str | None, str | None]:
    global _DISCOVERED_SERVICE_INFO
    if _DISCOVERED_SERVICE_INFO is None:
        _DISCOVERED_SERVICE_INFO = _discover_service_defaults(DEFAULT_PROJECT_ID)
    return _DISCOVERED_SERVICE_INFO


DEFAULT_SERVICE_NAME = _resolve_setting(
    ("LIVE_TEST_SERVICE_NAME", "RINGDOWN_SERVICE_NAME", "DEPLOY_DEFAULT_SERVICE"),
    fallback="",
    gcloud_fetcher=lambda: _ensure_service_info()[0],
)

DEFAULT_SERVICE_REGION = _resolve_setting(
    ("LIVE_TEST_SERVICE_REGION", "RINGDOWN_SERVICE_REGION", "DEPLOY_DEFAULT_REGION"),
    fallback="",
    gcloud_fetcher=lambda: _ensure_service_info()[1],
)

if not DEFAULT_PROJECT_ID:
    raise RuntimeError(
        "Unable to determine Cloud Run project. Set LIVE_TEST_PROJECT_ID in .env or run "
        "'gcloud config set project <id>' before executing the live tests."
    )

if not DEFAULT_SERVICE_NAME or not DEFAULT_SERVICE_REGION:
    raise RuntimeError(
        "Unable to determine Cloud Run service/region. Set LIVE_TEST_SERVICE_NAME and "
        "LIVE_TEST_SERVICE_REGION in .env, or ensure the configured project has a Cloud Run "
        "service accessible via 'gcloud run services list'."
    )

def _run_cmd(cmd: str, *, check: bool = True, capture: bool = True) -> str:
    """Run cmd in shell and return stdout (stripped). Raises on error.
    
    Replicates the pattern from cloudrun-deploy.py for consistency.
    """
    logger.info("$ %s", cmd)
    capture_mode = subprocess.PIPE if capture else None
    proc = subprocess.run(
        cmd,
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=capture_mode,
        stderr=capture_mode,
    )
    if check and proc.returncode != 0:
        out = proc.stdout or ""
        err = proc.stderr or ""
        logger.error("Command failed (%s)\nstdout: %s\nstderr: %s", proc.returncode, out, err)
        raise RuntimeError(f"Command failed: {cmd}\n{err}")
    return (proc.stdout or "").strip()


def retrieve_cloudrun_logs(
    project_id: str,
    start_time: dt.datetime,
    limit: int = DEFAULT_LOG_ENTRY_LIMIT,
    debug: bool = False,
    *,
    service_name: str | None = None,
    region: str | None = None,
) -> str:
    """Retrieve Cloud Run logs since the specified start time."""
    try:
        if start_time.tzinfo is None:
            start_utc = start_time.replace(tzinfo=dt.timezone.utc)
        else:
            start_utc = start_time.astimezone(dt.timezone.utc)

        def _format_ts(dt_value: dt.datetime) -> str:
            return (
                dt_value.astimezone(dt.timezone.utc)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            )

        def _build_filter(window_seconds: int | None, *, include_service: bool = True) -> str:
            parts: list[str] = ['resource.type="cloud_run_revision"']
            if window_seconds is not None:
                window_start = start_utc - dt.timedelta(seconds=window_seconds)
                parts.append(f'timestamp>="{_format_ts(window_start)}"')
            if include_service and service_name:
                parts.append(f'resource.labels.service_name="{service_name}"')
            if include_service and region:
                parts.append(f'resource.labels.location="{region}"')
            return " AND ".join(parts)

        attempt_specs = [
            {"filter_window": 5, "freshness": None, "post_window": 5, "include_service": True},
            {"filter_window": 90, "freshness": None, "post_window": 90, "include_service": True},
            {"filter_window": 300, "freshness": None, "post_window": 300, "include_service": True},
            {"filter_window": None, "freshness": "15m", "post_window": 900, "include_service": True},
            {"filter_window": None, "freshness": "30m", "post_window": 1800, "include_service": False},
        ]

        attempt_notes: list[str] = []
        use_shell = os.name == "nt"
        retry_delay_seconds = 4.0

        for attempt_index, spec in enumerate(attempt_specs, start=1):
            include_service = spec.get("include_service", True)
            filter_expr = _build_filter(spec["filter_window"], include_service=include_service) if spec["filter_window"] is not None else _build_filter(None, include_service=include_service)
            cmd = [
                "gcloud",
                "logging",
                "read",
                filter_expr,
                f"--project={project_id}",
                f"--limit={limit}",
                "--format=json",
                "--order=desc",
            ]
            freshness = spec["freshness"]
            if freshness:
                cmd.append(f"--freshness={freshness}")

            if debug:
                print(
                    f"[debug] Attempt {attempt_index}/{len(attempt_specs)} filter={filter_expr} freshness={freshness or 'n/a'} include_service={include_service}"
                )
                print(f"[debug] Command: {' '.join(cmd)}")

            exec_cmd = subprocess.list2cmdline(cmd) if use_shell else cmd
            proc = subprocess.run(
                exec_cmd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=use_shell,
            )

            if debug:
                print(f"[debug] Return code: {proc.returncode}")
                if proc.stdout:
                    preview = proc.stdout if len(proc.stdout) < 500 else proc.stdout[:500] + "…"
                    print(f"[debug] stdout({len(proc.stdout)}): {preview}")
                if proc.stderr:
                    print(f"[debug] stderr: {proc.stderr.strip()}")

            if proc.returncode != 0:
                err = (proc.stderr or "").strip() or "gcloud logging read failed"
                return f"\n=== Error retrieving logs: {err} ===\n"

            stdout = (proc.stdout or "").strip()
            if not stdout or stdout == "[]":
                scope_note = "service scoped" if include_service else "no service filter"
                attempt_notes.append(f"attempt {attempt_index}: empty result ({scope_note})")
                if attempt_index < len(attempt_specs):
                    if debug:
                        print(f"[debug] Sleeping {retry_delay_seconds}s before next attempt")
                    time.sleep(retry_delay_seconds)
                continue

            try:
                entries = json.loads(stdout)
            except json.JSONDecodeError as exc:
                if debug:
                    print(f"[debug] JSON decode failed: {exc}")
                return f"\n=== Error parsing gcloud output: {exc} ===\nRaw output:\n{stdout}\n"

            if isinstance(entries, dict):
                parsed_entries = [entries]
            elif isinstance(entries, list):
                parsed_entries = entries
            else:
                return "\n=== Error retrieving logs: unexpected gcloud output format ===\n"

            window_seconds = spec.get("post_window")
            min_allowed = start_utc - dt.timedelta(seconds=window_seconds) if window_seconds is not None else start_utc

            filtered_entries: list[dict] = []
            for entry in parsed_entries:
                ts_str = entry.get("timestamp") if isinstance(entry, dict) else None
                entry_dt = None
                if ts_str:
                    try:
                        entry_dt = dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        entry_dt = None
                if entry_dt is None or entry_dt >= min_allowed:
                    filtered_entries.append(entry)

            if not filtered_entries:
                scope_note = "service scoped" if include_service else "no service filter"
                attempt_notes.append(
                    f"attempt {attempt_index}: {len(parsed_entries)} entries, but all older than window ({scope_note})"
                )
                if attempt_index < len(attempt_specs):
                    if debug:
                        print(f"[debug] Sleeping {retry_delay_seconds}s before next attempt")
                    time.sleep(retry_delay_seconds)
                continue

            lines_out: list[str] = []
            for entry in sorted(filtered_entries, key=lambda e: e.get("timestamp") or ""):
                ts = entry.get("timestamp", "") if isinstance(entry, dict) else ""
                severity = entry.get("severity", "") if isinstance(entry, dict) else ""
                resource = entry.get("resource") if isinstance(entry, dict) else None
                labels = resource.get("labels", {}) if isinstance(resource, dict) else {}
                svc_name = labels.get("service_name") if isinstance(labels, dict) else None

                message: str | None = entry.get("textPayload") if isinstance(entry, dict) else None
                if not message and isinstance(entry, dict):
                    json_payload = entry.get("jsonPayload")
                    if isinstance(json_payload, dict):
                        message = (
                            json_payload.get("message")
                            or json_payload.get("event")
                            or json_payload.get("text")
                        )
                        if not message:
                            message = json.dumps(json_payload, ensure_ascii=False)
                if not message and isinstance(entry, dict):
                    proto_payload = entry.get("protoPayload")
                    if isinstance(proto_payload, dict):
                        status = proto_payload.get("status")
                        if isinstance(status, dict):
                            message = status.get("message")
                if not message:
                    message = "<no message>"

                summary_parts: list[str] = []
                if ts:
                    summary_parts.append(ts)
                if severity:
                    summary_parts.append(severity)
                if svc_name:
                    summary_parts.append(f"[{svc_name}]")
                summary = " ".join(summary_parts)
                line = f"{summary} {message}" if summary else message
                lines_out.append(line.strip())

            formatted = "\n".join(lines_out)
            return (
                f"\n=== Cloud Run Logs (filter: {filter_expr}) ===\n"
                f"{formatted}\n"
            )


        filter_details = '; '.join(attempt_notes) if attempt_notes else 'no filters executed'
        return f"\n=== No logs found (filters tried: {filter_details}) ===\n"

    except Exception as exc:
        logger.error("Failed to retrieve Cloud Run logs: %s", exc)
        return f"\n=== Error retrieving logs: {exc} ===\n"
def create_test_twiml(mp3_url: str) -> str:
    """Create TwiML that plays MP3 and hangs up immediately.
    
    Plays the audio file and hangs up immediately after completion,
    with no waiting period or goodbye message.
    """
    response = VoiceResponse()

    # Play the MP3 file
    response.play(mp3_url)

    # Hang up immediately after audio completes
    response.hangup()

    return str(response)


def generate_tts_audio(text: str, voice: str = "alloy", model: str = "tts-1", output_format: str = "mp3") -> Path:
    """Generate audio from text using OpenAI TTS API and return the file path.
    
    Args:
        text: Text to be spoken
        voice: Voice preset (alloy, echo, fable, onyx, nova, shimmer)
        model: TTS model (tts-1, tts-1-hd)
        output_format: Audio format (mp3, wav, etc.)
        
    Returns:
        Path to the generated audio file
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is required for TTS generation")
    
    # Create temporary file for the generated audio.  A timestamp with
    # **second-level** precision can collide when we synthesize multiple
    # prompts in rapid succession (~ <1 s apart).  Maintain a module-level
    # counter and append it to guarantee uniqueness.

    if not hasattr(generate_tts_audio, "_counter"):
        generate_tts_audio._counter = 0  # type: ignore[attr-defined]

    generate_tts_audio._counter += 1  # type: ignore[attr-defined]
    counter = generate_tts_audio._counter  # type: ignore[attr-defined]

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_dir = Path(tempfile.gettempdir())
    output_path = temp_dir / f"tts_generated_{timestamp}_{counter}.{output_format}"
    
    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "format": output_format,
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    api_url = "https://api.openai.com/v1/audio/speech"
    
    try:
        logger.info("Generating TTS audio for text: %s", text[:50] + ("..." if len(text) > 50 else ""))
        response = requests.post(
            api_url,
            json=payload,
            headers=headers,
            stream=True,
            timeout=300,
        )
        response.raise_for_status()
        
        # Write the audio data to file
        with open(output_path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
        
        logger.info("TTS audio generated successfully: %s", output_path)
        return output_path
        
    except requests.HTTPError as exc:
        msg = exc.response.text if exc.response is not None else str(exc)
        raise RuntimeError(f"TTS API error {exc.response.status_code if exc.response else ''}: {msg}") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to generate TTS audio: {exc}") from exc


def combine_audio_files_with_pauses(audio_files: list[str], pause_seconds: int) -> tuple[str, float, list[float]]:
    """Combine multiple audio files with silence pauses between them.
    
    Args:
        audio_files: List of paths to audio files to combine
        pause_seconds: Number of seconds of silence to insert between files
        
    Returns:
        Tuple of (path to combined audio file, total duration in seconds, list of individual audio durations)
    """
    if not audio_files:
        raise ValueError("No audio files provided")
    
    individual_durations = []
    
    if len(audio_files) == 1:
        # Single file - add final silence period for bot response
        single_audio = AudioSegment.from_mp3(audio_files[0])
        duration = len(single_audio) / 1000.0
        individual_durations.append(duration)
        
        # Add final silence period to give time for bot response
        silence = AudioSegment.silent(duration=pause_seconds * 1000)  # pydub uses milliseconds
        combined_audio = single_audio + silence
        
        # Generate output filename for single file with silence
        temp_dir = Path(tempfile.gettempdir())
        combined_path = temp_dir / f"combined_audio_{int(time.time())}.mp3"
        combined_audio.export(str(combined_path), format="mp3")
        
        total_duration = len(combined_audio) / 1000.0
        logger.info("Single audio with final silence created: %s (%.1fs total)", combined_path, total_duration)
        
        return str(combined_path), total_duration, individual_durations
    
    logger.info("Combining %d audio files with %d second pauses...", len(audio_files), pause_seconds)
    
    try:
        # Load the first audio file
        combined_audio = AudioSegment.from_mp3(audio_files[0])
        first_duration = len(combined_audio) / 1000.0
        individual_durations.append(first_duration)
        logger.info("Loaded audio file 1: %s (%.1fs)", audio_files[0], first_duration)
        
        # Create silence segment
        silence = AudioSegment.silent(duration=pause_seconds * 1000)  # pydub uses milliseconds
        
        # Add remaining files with pauses
        for i, audio_file in enumerate(audio_files[1:], 2):
            audio_segment = AudioSegment.from_mp3(audio_file)
            segment_duration = len(audio_segment) / 1000.0
            individual_durations.append(segment_duration)
            logger.info("Loaded audio file %d: %s (%.1fs)", i, audio_file, segment_duration)
            
            # Add silence, then the audio
            combined_audio += silence + audio_segment
        
        # Add final silence period after the last audio file to give time for bot response
        combined_audio += silence
        logger.info("Added final silence period (%.1fs) for bot response", pause_seconds)
        
        # Generate output filename
        temp_dir = Path(tempfile.gettempdir())
        combined_path = temp_dir / f"combined_audio_{int(time.time())}.mp3"
        
        # Export combined audio
        combined_audio.export(str(combined_path), format="mp3")
        
        # Calculate total duration (used for precise call timing)
        total_duration = len(combined_audio) / 1000.0
        logger.info("Combined audio created: %s (%.1fs total)", combined_path, total_duration)
        
        return str(combined_path), total_duration, individual_durations
        
    except Exception as e:
        logger.error("Failed to combine audio files: %s", e)
        raise RuntimeError(f"Audio combination failed: {e}") from e


def upload_mp3_to_twilio(client: Client, mp3_path: Path) -> str:
    """Upload MP3 to Google Cloud Storage and return public URL.

    Creates a GCS bucket for test assets if it doesn't exist, uploads the MP3 file,
    makes it publicly accessible, and returns the public URL.
    """
    from google.cloud import storage
    from google.oauth2 import service_account
    import os
    
    # Get GCP project ID from gcloud config
    try:
        project_id = _run_cmd("gcloud config get-value project", check=True).strip()
        if not project_id:
            raise ValueError("No GCP project configured")
    except Exception as e:
        logger.error("Failed to get GCP project ID: %s", e)
        raise RuntimeError("GCP project not configured. Run 'gcloud config set project <project-id>'") from e
    
    # Use Application Default Credentials
    try:
        storage_client = storage.Client(project=project_id)
    except Exception as e:
        logger.error("Failed to initialize GCS client: %s", e)
        raise RuntimeError("GCS authentication failed. Run 'gcloud auth application-default login'") from e
    
    # Bucket name for test assets
    bucket_name = f"{project_id}-test-assets"
    
    try:
        # Get or create bucket
        try:
            bucket = storage_client.bucket(bucket_name)
            bucket.reload()  # Check if it exists
            logger.info("Using existing GCS bucket: %s", bucket_name)
        except Exception:
            # Bucket doesn't exist, create it
            logger.info("Creating GCS bucket: %s", bucket_name)
            bucket = storage_client.create_bucket(bucket_name)
            logger.info("Created GCS bucket: %s", bucket_name)
        
        # Upload file
        blob_name = f"test-audio/{mp3_path.name}"
        blob = bucket.blob(blob_name)
        
        logger.info("Uploading %s to gs://%s/%s", mp3_path, bucket_name, blob_name)
        blob.upload_from_filename(str(mp3_path))
        
        # Make the blob publicly readable
        blob.make_public()
        
        public_url = blob.public_url
        logger.info("MP3 uploaded successfully: %s", public_url)
        return public_url
        
    except Exception as e:
        logger.error("Failed to upload MP3 to GCS: %s", e)
        raise RuntimeError(f"GCS upload failed: {e}") from e


def make_test_call(
    mp3_file: str,
    to_number: str = DEFAULT_TO_NUMBER,
    from_number: Optional[str] = None,
    project_id: Optional[str] = None,
    service_name: str = DEFAULT_SERVICE_NAME,
    region: str = DEFAULT_SERVICE_REGION,
    enable_log_monitoring: bool = True,
    debug: bool = False,
    *,
    expected_duration: float | None = None,  # predicted call length (secs)
) -> tuple[str, str, int]:
    """Make outbound test call with immediate hangup after audio.
    
    Plays the specified audio file and hangs up immediately after
    completion, with call duration exactly matching audio length.
    
    Returns:
        Tuple of (call SID, logs, local_duration_secs) from the completed call.
    """

    project_id = project_id or DEFAULT_PROJECT_ID

    # Get environment settings
    env = get_env()

    # Initialize Twilio client with auth from environment
    # The account SID is typically paired with the auth token
    # We'll need to get it from environment or Twilio console
    account_sid = env.twilio_account_sid
    if not account_sid:
        raise ValueError(
            "TWILIO_ACCOUNT_SID environment variable required. "
            "Get it from https://console.twilio.com"
        )

    client = Client(account_sid, env.twilio_auth_token)

    # Get a 'from' number if not specified
    if not from_number:
        # List available phone numbers and use the first one
        numbers = client.incoming_phone_numbers.list(limit=1)
        if not numbers:
            raise ValueError("No Twilio phone numbers found in account")
        from_number = numbers[0].phone_number
        logger.info("Using Twilio number: %s", from_number)

    # ------------------------------------------------------------------
    # Determine MP3 URL – local file → upload/host; http URL → use as-is
    # ------------------------------------------------------------------

    if mp3_file.lower().startswith("http"):
        mp3_url = mp3_file
    else:
        mp3_path = Path(mp3_file)
        if not mp3_path.exists():
            logger.error(
                "MP3 file %s not found. Upload it to a public location (e.g. Twilio Assets, "
                "GitHub raw link, S3) and pass that URL via --mp3, or place the file locally.",
                mp3_path
            )
            raise FileNotFoundError(mp3_path)

        # Local file exists – get (or upload) a public URL
        mp3_url = upload_mp3_to_twilio_util(client, mp3_path)

    # Create TwiML for the call
    twiml = create_test_twiml(mp3_url)

    # Option 1: Create a TwiML Bin (one-time setup in Twilio Console)
    # Option 2: Host TwiML on your server
    # For this test, we'll use inline TwiML

    logger.info(
        "Making test call from %s to %s (play MP3, immediate hang up)",
        from_number,
        to_number
    )

    try:
        # Make the call
        call = client.calls.create(
            to=to_number,
            from_=from_number,
            twiml=twiml,  # Inline TwiML
            # Alternatively, use url= parameter to point to hosted TwiML
        )

        logger.info("Call initiated with SID: %s", call.sid)
        logger.info("Call status: %s", call.status)

        # Monitor call status until completion
        start_time = time.time()
        call_start_datetime = dt.datetime.now(dt.timezone.utc)
        # Wait window: predicted duration + 30-second grace (or default 60s)
        timeout = (expected_duration + 30) if expected_duration else 60
        
        if enable_log_monitoring and not project_id:
            project_id = DEFAULT_PROJECT_ID

        # If project_id still not provided, try to detect from gcloud config
        if enable_log_monitoring and not project_id:
            try:
                detected_project = _run_cmd("gcloud config get-value project", check=False)
                if detected_project:
                    project_id = detected_project
                    logger.info("Using project from gcloud config: %s", project_id)
            except Exception:
                logger.warning("Could not detect project ID for log monitoring")
                enable_log_monitoring = False

        logger.info("Monitoring call status until completion (timeout %.0fs)...", timeout)
        while time.time() - start_time < timeout:
            # Refresh call status
            call = client.calls(call.sid).fetch()
            status = call.status

            logger.debug("Call status: %s (duration: %ss)", status, call.duration or 0)

            if status in ["completed", "failed", "busy", "no-answer", "canceled"]:
                logger.info("Call ended with status: %s", status)
                break

            time.sleep(2)  # Check every 2 seconds

        # Retrieve and display logs from the call
        logs_output = ""
        if enable_log_monitoring and project_id:
            logger.info("Retrieving Cloud Run logs from call...")
            # NOTE: In bot-to-bot testing, "interrupt" events are normal and expected.
            # When our test MP3 plays, Ringdown's ConversationRelay detects it as speech
            # and interrupts its own welcome greeting to listen for user input.
            # This is the intended behavior, not an error.
            try:
                logs_output = retrieve_cloudrun_logs(
                    project_id=project_id or DEFAULT_PROJECT_ID,
                    start_time=call_start_datetime,
                    limit=DEFAULT_LOG_ENTRY_LIMIT,
                    debug=debug,
                    service_name=service_name,
                    region=region,
                )
                print(logs_output)
            except Exception as e:
                logger.error("Log retrieval failed: %s", e)
                error_msg = f"\n=== Log retrieval error: {e} ===\n"
                print(error_msg)
                logs_output = error_msg

        # Final call details – compute duration locally instead of relying on
        # Twilio's `duration` field, which can lag by several seconds.
        local_duration = int(time.time() - start_time)

        call = client.calls(call.sid).fetch()
        logger.info(
            "Final call status: %s, Twilio duration: %s seconds, local duration: %s seconds",
            call.status,
            call.duration or "<pending>",
            local_duration,
        )

        return call.sid, logs_output, local_duration

    except Exception as e:
        logger.error("Call failed: %s", e)
        raise


def get_first_twilio_number(client: Client) -> str:
    """Get the first available Twilio phone number from the account."""
    numbers = client.incoming_phone_numbers.list(limit=1)
    if not numbers:
        raise ValueError("No Twilio phone numbers found in account")
    return numbers[0].phone_number


def make_chained_test_call(
    to_number: str,
    from_number: Optional[str],
    audio_files: list[str],
    silence_timeout: int,
    project_id: Optional[str],
    service_name: str,
    region: str,
    enable_log_monitoring: bool,
    debug: bool,
) -> tuple[str, str, int, float, list[float]]:
    """Make chained test call with combined audio and pauses.
    
    This approach combines all audio files into a single track with silence
    pauses between them, then makes one call. The call duration exactly matches
    the audio playback time (TTS + pauses) with immediate hangup after completion.
    This maintains conversation context between prompts while ensuring proper timing.
    
    Returns:
        Tuple of (call SID, logs, local call duration, combined audio duration, individual audio durations)
    """
    logger.info("=== Starting chained test call with %d audio files ===", len(audio_files))
    
    project_id = project_id or DEFAULT_PROJECT_ID

    try:
        # Combine all audio files with silence pauses
        combined_audio_path, total_audio_duration, individual_durations = combine_audio_files_with_pauses(audio_files, silence_timeout)
        
        # Make single call with combined audio - hang up immediately after audio finishes
        now_local = dt.datetime.now().astimezone()
        eta_local = now_local + dt.timedelta(seconds=total_audio_duration)
        print(f"[LiveTest] Call start: {now_local:%Y-%m-%d %H:%M:%S %Z}", flush=True)
        print(f"[LiveTest] Estimated completion: {eta_local:%Y-%m-%d %H:%M:%S %Z} (~{total_audio_duration:.1f}s)", flush=True)

        logger.info("Making test call with combined audio (%.1fs total)...", total_audio_duration)
        call_sid, logs_output, local_call_duration = make_test_call(
            mp3_file=combined_audio_path,
            to_number=to_number,
            from_number=from_number,
            project_id=project_id or DEFAULT_PROJECT_ID,
            service_name=service_name,
            region=region,
            enable_log_monitoring=enable_log_monitoring,
            debug=debug,
            expected_duration=total_audio_duration,
        )
        
        # Get call duration
        # Use locally-measured call duration rather than Twilio's
        call_duration = int(local_call_duration)
        
        logger.info("=== Chained test call completed ===")
        logger.info("Call SID: %s, Duration: %d seconds", call_sid, call_duration)
        
        # Clean up temporary combined audio file
        try:
            Path(combined_audio_path).unlink()
            logger.info("Cleaned up temporary combined audio file")
        except Exception as e:
            logger.warning("Failed to clean up temporary file %s: %s", combined_audio_path, e)
        
        return call_sid, logs_output, local_call_duration, total_audio_duration, individual_durations
        
    except Exception as e:
        logger.error("Chained test call failed: %s", e)
        raise


def prepare_log_evaluation_prompt(
    user_texts: list[str],
    silence_timeout: int,
    individual_durations: list[float],
    call_duration: int,
    logs: str
) -> str:
    """Prepare a detailed prompt for LLM evaluation of the test call logs.
    
    Args:
        user_texts: List of text prompts that were converted to TTS
        silence_timeout: Seconds of silence between prompts
        individual_durations: List of individual audio durations for each prompt
        call_duration: Actual call duration from Twilio
        logs: Cloud Run logs from the test call
        
    Returns:
        Detailed evaluation prompt for LLM analysis
    """
    
    # Calculate expected timing
    num_prompts = len(user_texts)
    num_pauses = max(0, num_prompts - 1)
    # Include final silence period for bot response time
    expected_total_pause_time = (num_pauses + 1) * silence_timeout  # +1 for final silence
    combined_audio_duration = sum(individual_durations) + expected_total_pause_time
    
    # Create detailed timeline
    timeline_sections = []
    timeline_sections.append("1. **Call Initiation & Bot Welcome** (~0-5s): Twilio call setup, WebSocket connection, bot says \"Hello caller.\"")
    
    current_time = 5.0  # Approximate welcome greeting duration
    section_num = 2
    
    for i, (text, duration) in enumerate(zip(user_texts, individual_durations)):
        prompt_num = i + 1
        start_time = current_time
        end_time = current_time + duration
        
        timeline_sections.append(f"{section_num}. **\"{text}\"** (~{start_time:.1f}-{end_time:.1f}s): User prompt {prompt_num} (Duration: {duration:.1f}s)")
        current_time = end_time
        section_num += 1
        
        # Add pause if not the last prompt
        if i < len(user_texts) - 1:
            pause_start = current_time
            pause_end = current_time + silence_timeout
            timeline_sections.append(f"{section_num}. **Pause Silently while Bot Responds** (~{pause_start:.1f}-{pause_end:.1f}s): {silence_timeout}s gap before next prompt")
            current_time = pause_end
            section_num += 1
    
    # Add final silence period for bot response
    final_silence_start = current_time
    final_silence_end = current_time + silence_timeout
    timeline_sections.append(f"{section_num}. **Final Silence Period** (~{final_silence_start:.1f}-{final_silence_end:.1f}s): {silence_timeout}s for bot response time")
    current_time = final_silence_end
    section_num += 1
    
    timeline_sections.append(f"{section_num}. **Call End** (~{current_time:.1f}s): Hang up after final silence completes")
    
    timeline_text = "\n".join(timeline_sections)

    # Print the timeline for visibility
    print("\n=== EXPECTED TIMELINE ===\n" + timeline_text + "\n========================\n")
    
    prompt = f"""Ringdown is a voice assistant. You are a part of an automated evaluation system. The system has just played one or more audio prompts to Ringdown, which should have responded to each one. You are analyzing the log files for errors or potential issues.

## TEST CONFIGURATION:
- **Number of prompts**: {num_prompts}
- **Audio prompts that were played for the test**: {json.dumps(user_texts, indent=2)}
- **Individual audio durations**: {[f"{d:.1f}s" for d in individual_durations]}
- **Pause time between prompts**: {silence_timeout} seconds
- **Total expected audio duration**: {combined_audio_duration:.1f} seconds
- **Actual call duration**: {call_duration} seconds

## EXPECTED TIMELINE:
{timeline_text}

Analyze the logs CAREFULLY. Produce a succinct timeline of the call, and call out any of the following that occur:
- Any errors or warnings
- Any text that we sent, which did not get transcribed (small transcription errors fine and need not be noted)
- Any text that we sent, where there was no response from the bot
And finally, using your best analysis of appropriate answers:
- Any response from the bot which does not make sense given the text we sent.

End with a brief summary of any problems, and if there were problems, suggestions as to what might have happened. Quote the relevant portion of the logs in explaining them.

If there were no problems, just say "No problems found."

## TIMING ANALYSIS:
- **Expected total duration**: {combined_audio_duration:.1f} seconds ({num_prompts} prompts + {expected_total_pause_time}s pauses including final silence + ~5s welcome)
- **Actual call duration**: {call_duration} seconds  


## LOGS TO ANALYZE:
```
{logs}
```

"""    
    return prompt


def evaluate_logs_with_llm(evaluation_prompt: str, model: str) -> str:
    """Send the evaluation prompt to an LLM and return the analysis.
    
    Args:
        evaluation_prompt: Detailed prompt for log evaluation
        model: The model to use for evaluation (e.g., gpt-4o, o3, etc.)
        
    Returns:
        LLM's analysis of the logs
    """
    import openai
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is required for log evaluation")
    
    try:
        client = openai.OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system", 
                    "content": "You are a python expert."
                },
                {
                    "role": "user", 
                    "content": evaluation_prompt
                }
            ],
            temperature=1.0
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        logger.error("Failed to evaluate logs with %s: %s", model, e)
        return f"❌ Log evaluation failed: {e}"


def create_chained_test_twiml(audio_urls: list[str], pause_seconds: int) -> str:
    """Create TwiML for chained audio testing.
    
    Instead of playing audio files before connecting to the bot, this connects
    to the bot conversation system immediately. The audio files will be injected
    during the conversation using Twilio's call update mechanism.
    
    This ensures the bot's conversation system is active and can transcribe
    all audio inputs properly.
    """
    response = VoiceResponse()
    
    # Connect directly to the bot conversation system
    # This activates the WebSocket and conversation transcription immediately
    redirect_url = DEFAULT_TWIML_REDIRECT
    if not redirect_url:
        raise RuntimeError(
            "Set RINGDOWN_TWIML_URL to your deployed /twiml endpoint before running chained tests."
        )

    response.redirect(redirect_url, method="GET")
    
    twiml_str = str(response)
    logger.debug("Generated chained TwiML: %s", twiml_str)
    return twiml_str


@click.command()
@click.option(
        "--to",
        default=DEFAULT_TO_NUMBER,
        help=f"Number to call (default: {DEFAULT_TO_NUMBER})"
    )
@click.option(
    "--from-number",
    "from_number",
        help="Twilio number to call from (default: auto-detect)"
    )
@click.option(
    "--text",
    multiple=True,
    default=["name an animal"],
    help="Text to convert to speech and play during call (can be used multiple times for chaining, mutually exclusive with --mp3). Default: 'name an animal'"
)
@click.option(
        "--mp3",
        help=f"MP3 file to play (mutually exclusive with --text)"
    )
@click.option(
    "--tts-voice",
    default="alloy",
    type=click.Choice(["alloy", "echo", "fable", "onyx", "nova", "shimmer"]),
    help="OpenAI TTS voice (default: alloy)"
)
@click.option(
    "--tts-model",
    default="tts-1",
    type=click.Choice(["tts-1", "tts-1-hd"]),
    help="OpenAI TTS model (default: tts-1)"
)
@click.option(
    "--silence-timeout",
    type=int,
    default=15,
    help="Seconds of silence pause between chained TTS prompts (default: 15)"
)
@click.option(
        "--project-id",
        help="GCP project ID for log monitoring (default: auto-detect from gcloud config)"
    )
@click.option(
        "--service-name",
        default=DEFAULT_SERVICE_NAME,
        help=f"Cloud Run service name for log monitoring (default: {DEFAULT_SERVICE_NAME})"
    )
@click.option(
        "--region",
        default=DEFAULT_SERVICE_REGION,
        help="GCP region for Cloud Run service (default: us-west1)"
    )
@click.option(
        "--no-logs",
    is_flag=True,
        help="Disable Cloud Run log monitoring during call"
    )
@click.option(
        "--debug",
    is_flag=True,
        help="Enable debug output for log monitoring"
    )
@click.option(
    "--evaluate-logs/--no-evaluate-logs",
    default=True,
    help="Enable/disable detailed log evaluation using LLM analysis (default: enabled)"
)
@click.option(
    "--log-model",
    default="o3",
    help="Model to use for log evaluation (default: o3)"
)
def main(
    to: str,
    from_number: Optional[str],
    text: tuple[str, ...],
    mp3: Optional[str],
    tts_voice: str,
    tts_model: str,
    silence_timeout: int,
    project_id: Optional[str],
    service_name: str,
    region: str,
    no_logs: bool,
    debug: bool,
    evaluate_logs: bool,
    log_model: str,
):
    """Make a test outbound call with Twilio and precise timing control.
    
    By default, generates TTS for "name an animal" and plays it during the call.
    
    Options:
    1. Use --text to specify custom text for TTS generation (supports chaining)
    2. Use --mp3 to play an existing audio file instead
    
    For chained TTS prompts, audio files are combined with configurable silence 
    gaps and the call hangs up immediately after all audio completes.
    
    Log evaluation with LLM analysis is enabled by default (using configurable model).
    """
    
    # Validate mutually exclusive options
    if text and mp3:
        logger.error("--text and --mp3 are mutually exclusive. Use one or the other.")

    project_id = project_id or DEFAULT_PROJECT_ID
    service_name = service_name or DEFAULT_SERVICE_NAME
    region = region or DEFAULT_SERVICE_REGION
    
    # If no explicit options provided, use default text
    if not text and not mp3:
        text = ("name an animal",)

    env = get_env()

    os.environ.setdefault('TWILIO_AUTH_TOKEN', env.twilio_auth_token)
    if env.twilio_account_sid:
        os.environ.setdefault('TWILIO_ACCOUNT_SID', env.twilio_account_sid)
    os.environ.setdefault('OPENAI_API_KEY', env.openai_api_key)

    # Ensure we have required environment variables
    if not os.environ.get("TWILIO_AUTH_TOKEN"):
        logger.error(
            "TWILIO_AUTH_TOKEN not found in environment. "
            "Please set it or create a .env file."
        )
        sys.exit(1)

    if not env.twilio_account_sid:
        logger.error(
            "TWILIO_ACCOUNT_SID not found. "
            "Add it to your .env file or set it in the environment."
        )
        sys.exit(1)

    # Handle text chaining vs single MP3
    if text:
        if not os.environ.get("OPENAI_API_KEY"):
            logger.error(
                "OPENAI_API_KEY not found in environment. "
                "Required for TTS generation."
            )
            sys.exit(1)
        
        # Generate audio files for all text inputs
        audio_files = []
        for i, text_input in enumerate(text):
            try:
                logger.info("Generating TTS audio %d/%d from text: %s", 
                           i+1, len(text), text_input[:100] + ("..." if len(text_input) > 100 else ""))
                generated_audio_path = generate_tts_audio(text_input, voice=tts_voice, model=tts_model)
                audio_files.append(str(generated_audio_path))
            except Exception as e:
                logger.error("TTS generation failed for text %d: %s", i+1, e)
                sys.exit(1)
        
        # Execute chained conversation
        try:
            call_sid, logs_output, local_call_duration, combined_audio_duration, individual_durations = make_chained_test_call(
                to_number=to,
                from_number=from_number,
                audio_files=audio_files,
                silence_timeout=silence_timeout,
                project_id=project_id or DEFAULT_PROJECT_ID,
                service_name=service_name,
                region=region,
                enable_log_monitoring=not no_logs,
                debug=debug,
            )
            
            # Perform detailed log evaluation if requested
            if evaluate_logs and logs_output and logs_output.strip():
                logger.info(f"\n=== Starting {log_model.upper()} Log Evaluation ===")
                try:
                    # Use locally-measured call duration rather than Twilio's
                    call_duration = int(local_call_duration)
                    
                    # Prepare evaluation prompt
                    evaluation_prompt = prepare_log_evaluation_prompt(
                        user_texts=list(text),
                        silence_timeout=silence_timeout,
                        individual_durations=individual_durations,
                        call_duration=call_duration,
                        logs=logs_output
                    )
                    
                    # Get LLM evaluation
                    evaluation_result = evaluate_logs_with_llm(evaluation_prompt, model=log_model)

                    print("\n" + "="*80)
                    print(f"🤖 {log_model.upper()} LOG EVALUATION PROMPT")
                    print("="*80)
                    print(evaluation_prompt)
                    print("="*80 + "\n")
                    print("\n" + "="*80)
                    print(f"🤖 {log_model.upper()} LOG EVALUATION RESULTS")
                    print("="*80)
                    print(evaluation_result)
                    print("="*80 + "\n")
                    
                except Exception as e:
                    logger.error("Log evaluation failed: %s", e)
                    print(f"\n❌ Log evaluation error: {e}\n")
            elif evaluate_logs:
                logger.warning("Log evaluation requested but no logs available")
                
        except Exception as e:
            logger.error("Chained test call failed: %s", e)
            sys.exit(1)
    else:
        # Single MP3 file - use original logic
        try:
            call_sid, logs_output, local_duration = make_test_call(
                mp3_file=mp3,
                to_number=to,
                from_number=from_number,
                project_id=project_id or DEFAULT_PROJECT_ID,
                service_name=service_name,
                region=region,
                enable_log_monitoring=not no_logs,
                debug=debug,
            )
            logger.info("Test call completed successfully. Call SID: %s", call_sid)
            
            # Note: Single MP3 evaluation not implemented yet - would need audio duration calculation
            if evaluate_logs:
                logger.warning("Log evaluation for single MP3 files not yet implemented")
                
        except Exception as e:
            logger.error("Test call failed: %s", e)
            sys.exit(1)


if __name__ == "__main__":
    main()


@pytest.mark.live
def test_live_call_cli():
    """Invoke the live call script end-to-end."""

    runner = CliRunner()
    result = runner.invoke(main, [], catch_exceptions=False)
    assert result.exit_code == 0, result.output
