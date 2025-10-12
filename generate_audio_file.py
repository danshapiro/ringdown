#!/usr/bin/env python
"""generate_audio_file.py

Command-line utility to synthesize speech via the OpenAI Text-to-Speech endpoint.

Example usage:
    python generate_audio_file.py --text "Hello world" --output hello.mp3
    python generate_audio_file.py --file prompt.txt
    echo "Streamed from stdin" | python generate_audio_file.py -o stream.mp3

This intentionally uses a direct HTTP request (via *requests*) instead of the
`openai` Python client so it works even when that package is absent or broken.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path

import requests

_API_URL = "https://api.openai.com/v1/audio/speech"
_DEFAULT_MODEL = "tts-1"
_DEFAULT_VOICE = "alloy"
_SUPPORTED_FORMATS = {"mp3", "aac", "flac", "wav", "ogg", "pcm", "mp4"}


class _CliError(SystemExit):
    """Raised when the CLI encounters an unrecoverable, user-facing error."""


def _parse_args() -> argparse.Namespace:  # noqa: D401
    """Build and parse the command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="generate_audio_file.py",
        description="Generate an audio file from text using OpenAI TTS.",
    )

    text_group = parser.add_mutually_exclusive_group()
    text_group.add_argument("--text", "-t", help="Text to be spoken (UTF-8 string).")
    text_group.add_argument(
        "--file",
        "-f",
        type=Path,
        help="Path to a UTF-8 text file containing the prompt.",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Destination audio file. Defaults to ./tts_<timestamp>.<format>",
    )
    parser.add_argument(
        "--model",
        default=_DEFAULT_MODEL,
        help=f"TTS model name (default: {_DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--voice",
        default=_DEFAULT_VOICE,
        help=f"Voice preset (default: {_DEFAULT_VOICE})",
    )
    parser.add_argument(
        "--format",
        default="mp3",
        choices=sorted(_SUPPORTED_FORMATS),
        help="Audio format/codec (default: mp3).",
    )
    parser.add_argument(
        "--api-key",
        dest="api_key",
        help="Explicit OpenAI API key. Overrides $OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Request timeout seconds (default: 300).",
    )

    args = parser.parse_args()
    return args


def _resolve_text(args: argparse.Namespace) -> str:
    if args.text:
        return args.text

    if args.file:
        try:
            return args.file.read_text(encoding="utf-8").strip()
        except Exception as exc:  # noqa: BLE001
            raise _CliError(f"Failed to read file '{args.file}': {exc}") from exc

    # Fallback to stdin
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()

    raise _CliError("No input text provided. Use --text, --file, or pipe via stdin.")


def _main() -> None:  # noqa: C901,E501
    args = _parse_args()

    try:
        text = _resolve_text(args)
    except _CliError as error:
        sys.exit(str(error))

    if not text:
        sys.exit("ERROR: Provided text is empty.")

    audio_format = args.format.lower()
    if audio_format not in _SUPPORTED_FORMATS:
        sys.exit(f"ERROR: Unsupported format '{audio_format}'.")

    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path: Path = args.output or Path(f"tts_{timestamp}.{audio_format}")

    api_key = args.api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("ERROR: OPENAI_API_KEY environment variable not set and --api-key not provided.")

    payload = {
        "model": args.model,
        "input": text,
        "voice": args.voice,
        "format": audio_format,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        print("[generate_audio_file] Requesting TTS synthesis…", flush=True)
        response = requests.post(
            _API_URL,
            json=payload,
            headers=headers,
            stream=True,
            timeout=args.timeout,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:  # noqa: BLE001
        # Detailed error from the API if available.
        msg = exc.response.text if exc.response is not None else str(exc)
        sys.exit(f"ERROR: TTS HTTP {exc.response.status_code if exc.response else ''}: {msg}")
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"ERROR: Failed to contact TTS endpoint: {exc}")

    try:
        with open(output_path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"ERROR: Unable to write audio file '{output_path}': {exc}")

    print(f"[generate_audio_file] Audio saved to {output_path}")


if __name__ == "__main__":  # pragma: no cover
    _main() 