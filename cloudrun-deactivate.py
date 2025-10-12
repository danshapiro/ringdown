#!/usr/bin/env python
"""Tear down a Cloud Run service and its resources.

Usage::

    python cloudrun-deactivate.py --service ringdown --region us-west1 --yes

The script removes the Cloud Run service. Optionally it can clean up the
associated Artifact Registry repository when --purge-images is supplied.
"""

from __future__ import annotations

import argparse
import os
import sys
import logging

from pathlib import Path

# Re-use helpers from the deploy script to avoid duplication
from cloudrun_deploy import (
    _run_cmd,
    _ensure_gcloud_on_path,
    _verify_gcloud_auth,
    _confirm_once,
    DEFAULT_PROJECT_ID,
    DEFAULT_REGION,
    DEFAULT_SERVICE,
)

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:  # Graceful fallback if python-dotenv absent
    def load_dotenv(*_: object, **__: object) -> None:  # type: ignore
        return None

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

from log_love import setup_logging  # local helper – keeps logging consistent

setup_logging()
log = logging.getLogger("cloudrun-deactivate")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _delete_service(project_id: str, region: str, service: str) -> None:
    """Delete *service* in *project_id*/*region* (idempotent)."""

    log.info("Deleting Cloud Run service %s in %s/%s", service, project_id, region)
    try:
        _run_cmd(
            " ".join(
                [
                    "gcloud run services delete",
                    service,
                    f"--region {region}",
                    "--platform managed",
                    "--quiet",
                ]
            )
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "NOT_FOUND" in msg or "not found" in msg.lower():
            log.warning("Service %s does not exist (already deleted)", service)
            return
        raise


def _delete_artifact_repo(project_id: str, region: str, repo: str) -> None:
    """Delete Artifact Registry *repo* in *region* (if it exists)."""

    log.info("Deleting Artifact Registry repo %s in %s", repo, region)
    try:
        _run_cmd(
            " ".join(
                [
                    "gcloud artifacts repositories delete",
                    repo,
                    f"--location {region}",
                    "--quiet",
                ]
            )
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "NOT_FOUND" in msg or "not found" in msg.lower():
            log.warning("Repository %s does not exist (already deleted)", repo)
            return
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deactivate Cloud Run service")
    parser.add_argument("--project-id", help="GCP project ID (default: gcloud config value)")
    parser.add_argument("--region", default=None, help="GCP region (default: gcloud config or us-west1)")
    parser.add_argument("--service", default=DEFAULT_SERVICE, help="Cloud Run service name (default: %(default)s)")

    parser.add_argument(
        "--purge-images",
        action="store_true",
        help="Also delete the Artifact Registry repository containing built images",
    )

    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmations (assume yes)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    load_dotenv(override=False)
    _ensure_gcloud_on_path()

    args = _parse_args(argv)

    # Re-use the deploy module's global auto-approve flag by setting env var
    if args.yes:
        os.environ["DEPLOY_AUTO_APPROVE"] = "1"

    project_id = (
        args.project_id
        or os.environ.get("DEPLOY_PROJECT_ID")
        or os.environ.get("LIVE_TEST_PROJECT_ID")
        or DEFAULT_PROJECT_ID
        or _run_cmd("gcloud config get-value project")
    )

    region = (
        args.region
        or os.environ.get("DEPLOY_REGION")
        or os.environ.get("LIVE_TEST_SERVICE_REGION")
    )
    if not region:
        try:
            region = _run_cmd("gcloud config get-value run/region")
        except RuntimeError:
            region = DEFAULT_REGION

    # Ensure gcloud auth is in place before destructive operations
    _verify_gcloud_auth()

    # Confirm destructive action
    _confirm_once(
        f"About to delete Cloud Run service '{args.service}' in project '{project_id}' (region {region})."
    )

    # Delete Cloud Run service
    _delete_service(project_id, region, args.service)

    # Optionally delete Artifact Registry repo (same name as service)
    if args.purge_images:
        _confirm_once(
            f"Also delete Artifact Registry repo '{args.service}' in {region}?"
        )
        _delete_artifact_repo(project_id, region, args.service)

    log.info("Deactivation complete.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1) 
