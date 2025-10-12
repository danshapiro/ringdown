"""Utility helper to upload MP3 files to Google Cloud Storage and return a public URL.

Moved from ``app/utils`` to a top-level ``utils`` package so it can be imported
with ``import utils.mp3_uploader`` throughout the project.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from twilio.rest import Client

logger = logging.getLogger(__name__)

__all__ = ["upload_mp3_to_twilio"]


def _run_cmd(cmd: str, *, check: bool = True) -> str:
    """Run *cmd* in the shell and return its *stdout*.

    This is a minimal re-implementation of the helper that existed in the test
    file; it avoids importing heavyweight modules just to execute a shell
    command.
    """
    logger.info("$ %s", cmd)
    proc = subprocess.run(
        cmd,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )

    if check and proc.returncode != 0:
        logger.error(
            "Command failed (%s)\nstdout: %s\nstderr: %s",
            proc.returncode,
            proc.stdout or "",
            proc.stderr or "",
        )
        raise RuntimeError(f"Command failed: {cmd}\n{proc.stderr}")

    return (proc.stdout or "").strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upload_mp3_to_twilio(client: Client, mp3_path: Path) -> str:  # noqa: D401
    """Upload *mp3_path* to GCS and return a publicly accessible URL."""

    from google.cloud import storage  # type: ignore

    if not mp3_path.exists():
        raise FileNotFoundError(mp3_path)

    try:
        project_id = _run_cmd("gcloud config get-value project").strip()
        if not project_id:
            raise ValueError("No GCP project configured")
    except Exception as exc:
        raise RuntimeError(
            "GCP project not configured. Run 'gcloud config set project <project-id>'."
        ) from exc

    try:
        storage_client = storage.Client(project=project_id)
    except Exception as exc:
        raise RuntimeError(
            "GCS authentication failed. Run 'gcloud auth application-default login'."
        ) from exc

    bucket_name = f"{project_id}-test-assets"
    bucket = storage_client.bucket(bucket_name)

    try:
        bucket.reload()
        logger.info("Using existing GCS bucket: %s", bucket_name)
    except Exception:
        logger.info("Creating GCS bucket: %s", bucket_name)
        bucket = storage_client.create_bucket(bucket_name)
        logger.info("Created GCS bucket: %s", bucket_name)

    blob_name = f"test-audio/{mp3_path.name}"
    blob = bucket.blob(blob_name)

    logger.info("Uploading %s to gs://%s/%s", mp3_path, bucket_name, blob_name)
    blob.upload_from_filename(str(mp3_path))

    blob.make_public()

    public_url: str = blob.public_url
    logger.info("MP3 uploaded successfully: %s", public_url)
    return public_url
