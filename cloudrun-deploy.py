import argparse
import datetime as _dt
import logging
import os
import shlex
import subprocess
import sys
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml
from log_love import setup_logging
import time
from tenacity import retry, stop_after_attempt, wait_fixed
# DEFER HEAVY GOOGLE CLOUD IMPORTS UNTIL NEEDED
# Importing google.cloud.run_v2 at module import time pulls in a large dependency
# tree (aiohttp, attrs, etc.) which can appear to "hang" on Windows / networked
# drives before any logs are emitted. We lazy-import inside the functions that
# need these clients.
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:  # Fallback: don't crash if python-dotenv isn't installed yet
    def load_dotenv(*_: object, **__: object) -> None:  # type: ignore
        return None

# Load environment early so module-level defaults pick up .env overrides.
load_dotenv(override=False)

###############################################################################
# Configurable defaults (override via env-vars or edit here)                  #
###############################################################################

def _env_default(*keys: str, default: str = "") -> str:
    """Return the first non-empty environment value from *keys*."""

    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return default


DEFAULT_REGION: str = _env_default(
    "DEPLOY_DEFAULT_REGION",
    "LIVE_TEST_SERVICE_REGION",
    default="us-central1",
)
DEFAULT_SERVICE: str = _env_default(
    "DEPLOY_DEFAULT_SERVICE",
    "LIVE_TEST_SERVICE_NAME",
    default="ringdown",
)

# Default GCP project ID (override with --project-id or env var)
DEFAULT_PROJECT_ID: str = _env_default(
    "DEPLOY_PROJECT_ID",
    "LIVE_TEST_PROJECT_ID",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
)
DEFAULT_ALWAYS_WARM: bool = True  # keep one instance warm
DEFAULT_CPU: str = "1"
DEFAULT_MEMORY: str = "512Mi"
DEFAULT_PORT: int = 8000
DEFAULT_CLOUDRUN_TIMEOUT: int = 3600  # 60 minutes for WebSocket connections

# Revision & readiness behaviour
DEFAULT_REVISION_HISTORY_LIMIT: int = 10
DEFAULT_READINESS_ATTEMPTS: int = 10
DEFAULT_READINESS_WAIT_SECONDS: int = 10

# Final health-check
DEFAULT_HEALTH_TIMEOUT_SECONDS: int = 10

SECRET_ACCESSOR_ROLE: str = "roles/secretmanager.secretAccessor"


@dataclass
class SecretPlan:
    """Declarative representation of a secret to upload to Secret Manager."""

    secret_id: str
    payload: bytes
    env_var: str | None = None
    mount_path: str | None = None


def _load_secret_plans(config_path: Path | None) -> list[SecretPlan]:
    """Load secret configuration specifications from YAML."""

    if not config_path:
        return []

    if not config_path.exists():
        log.debug("Secret configuration %s not found; skipping secret uploads", config_path)
        return []

    with config_path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp) or {}

    entries = raw.get("secrets", [])
    plans: list[SecretPlan] = []

    for entry in entries:
        secret_id = entry.get("secret_id")
        if not secret_id:
            raise SystemExit("Each secret entry must include 'secret_id'.")

        optional = bool(entry.get("optional"))
        payload: bytes | None = None

        if "value_from_env" in entry:
            env_name = entry["value_from_env"]
            env_value = os.environ.get(env_name)
            if env_value is None:
                if optional:
                    log.info("Skipping optional secret %s because %s is unset", secret_id, env_name)
                    continue
                raise SystemExit(
                    f"Secret '{secret_id}' requires environment variable '{env_name}' to be set."
                )
            payload = env_value.encode("utf-8")
        elif "source" in entry:
            src = Path(entry["source"])
            if not src.is_absolute():
                src = (config_path.parent / src).resolve()
            if not src.exists():
                if optional:
                    log.info("Skipping optional secret %s because %s is missing", secret_id, src)
                    continue
                raise SystemExit(f"Secret '{secret_id}' source file not found: {src}")
            payload = src.read_bytes()
        else:
            raise SystemExit(
                f"Secret '{secret_id}' must define either 'value_from_env' or 'source'."
            )

        plans.append(
            SecretPlan(
                secret_id=secret_id,
                payload=payload,
                env_var=entry.get("env_var"),
                mount_path=entry.get("mount_path"),
            )
        )

    return plans

###############################################################################
# Logging setup                                                               #
###############################################################################

# Initialise root logging once, then grab module-specific logger
os.environ.setdefault("LOG_LOVE_SKIP_LITELLM_PATCH", "1")
setup_logging()
log = logging.getLogger("cloudrun-deploy")
log.info("cloudrun-deploy starting up...")

###############################################################################
# Helpers                                                                     #
###############################################################################


def _run_cmd(cmd: str, *, check: bool = True, capture: bool = True) -> str:
    """Run *cmd* in the shell and return stdout (stripped). Raises on error."""
    log.info("$ %s", cmd)
    capture_mode = subprocess.PIPE if capture else None
    # On Windows Docker emits bytes that are not valid in cp1252 which is the default
    # console encoding.  Force UTF-8 decoding and *replace* invalid byte sequences so
    # background reader threads never raise UnicodeDecodeError (see GH-402).
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
        log.error("Command failed (%s)\nstdout: %s\nstderr: %s", proc.returncode, out, err)
        raise RuntimeError(f"Command failed: {cmd}\n{err}")
    return (proc.stdout or "").strip()


def _ensure_gcloud_on_path() -> None:
    """Windows convenience: add Google Cloud SDK to PATH if missing."""
    if os.name != "nt":
        return
    from shutil import which

    if which("gcloud"):
        return

    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return
    candidate = Path(local_appdata) / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin"
    if candidate.is_dir():
        os.environ["PATH"] = str(candidate) + os.pathsep + os.environ["PATH"]
        if which("gcloud"):
            log.info("gcloud found at %s", candidate)


###############################################################################
# GCP project helpers                                                          #
###############################################################################


def _ensure_gcp_project(project_id: str) -> None:
    """Verify that *project_id* exists before continuing.

    The deploy script no longer auto-creates projects; if the target project is
    missing we stop early so it can be provisioned manually.
    """

    try:
        _run_cmd(f"gcloud projects describe {project_id}")
    except RuntimeError as exc:
        log.error("GCP project %s not found", project_id)
        raise SystemExit(
            f"GCP project '{project_id}' not found. Create it manually and rerun the deploy."
        ) from exc

    log.info("GCP project %s verified", project_id)


###############################################################################
# Google API helpers                                                          #
###############################################################################


def _ensure_service_enabled(project_id: str, service_name: str) -> None:
    """Enable *service_name* for *project_id* if it is not already enabled.

    The call is safe to run on every deploy - it is effectively idempotent.
    """

    try:
        enabled = _run_cmd(
            " ".join(
                [
                    "gcloud services list --enabled",
                    f"--project {project_id}",
                    f"--filter={service_name}",
                    '--format="value(config.name)"',
                ]
            )
        )
        if service_name in enabled:
            log.info("%s already enabled for %s", service_name, project_id)
            return
    except RuntimeError as exc:
        # If the listing fails we will still attempt to enable the service -
        # worst case the subsequent call will surface the underlying issue.
        log.debug("Service check failed for %s: %s", service_name, exc)

    _confirm_once(f"Enable API '{service_name}' for project '{project_id}'? This may incur charges.")
    log.info("Enabling %s for %s", service_name, project_id)
    try:
        _run_cmd(
            f"gcloud services enable {service_name} --project {project_id} --quiet"
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "FAILED_PRECONDITION" in msg and "billing" in msg.lower():
            log.warning("Project billing not enabled - attempting to link automatically ...")
            _ensure_project_billing(project_id)
            # Retry once after linking billing
            _run_cmd(
                f"gcloud services enable {service_name} --project {project_id} --quiet"
            )
            return
        raise


def _ensure_gmail_api_enabled(project_id: str) -> None:
    """Ensure the Gmail API is enabled for the deploy project."""

    _ensure_service_enabled(project_id, "gmail.googleapis.com")


def _ensure_google_docs_api_enabled(project_id: str) -> None:
    """Ensure the Google Docs API is enabled for the deploy project."""
    _ensure_service_enabled(project_id, "docs.googleapis.com")


def _ensure_google_drive_api_enabled(project_id: str) -> None:
    """Ensure the Google Drive API is enabled for the deploy project."""
    _ensure_service_enabled(project_id, "drive.googleapis.com")


def _ensure_secret_manager_api_enabled(project_id: str) -> None:
    """Ensure the Secret Manager API is enabled for the deploy project."""

    _ensure_service_enabled(project_id, "secretmanager.googleapis.com")


def _ensure_secret_exists(project_id: str, secret_id: str) -> None:
    """Create *secret_id* if it does not exist in Secret Manager."""

    try:
        _run_cmd(
            f'gcloud secrets describe {secret_id} --project {project_id} --format="value(name)"'
        )
    except RuntimeError:
        log.info("Creating secret %s", secret_id)
        _run_cmd(
            " ".join(
                [
                    "gcloud secrets create",
                    secret_id,
                    f"--project {project_id}",
                    "--replication-policy=automatic",
                    "--quiet",
                ]
            )
        )


def _add_secret_version(project_id: str, secret_id: str, payload: bytes) -> None:
    """Upload *payload* as a new version for *secret_id*."""

    cmd = [
        _gcloud_executable(),
        "secrets",
        "versions",
        "add",
        secret_id,
        f"--project={project_id}",
        "--data-file=-",
    ]
    log.info("Uploading new version for secret %s", secret_id)
    proc = subprocess.run(  # noqa: S603
        cmd,
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
        raise RuntimeError(f"Failed to upload secret {secret_id}: {stderr.strip()}")


def _gcloud_executable() -> str:
    """Return an invocable gcloud command for the current platform."""

    from shutil import which

    exe = which("gcloud")
    if exe:
        return exe
    if os.name == "nt":
        exe = which("gcloud.cmd")
        if exe:
            return exe
    return "gcloud"


def _apply_secret_plans(
    project_id: str,
    plans: list[SecretPlan],
    service_account_email: str,
) -> tuple[list[str], set[str]]:
    """Ensure secrets exist, upload payloads, and return update flags."""

    updates: list[str] = []
    remove_env: set[str] = set()
    for plan in plans:
        _ensure_secret_exists(project_id, plan.secret_id)
        _add_secret_version(project_id, plan.secret_id, plan.payload)
        _ensure_secret_accessor(project_id, plan.secret_id, service_account_email)

        if plan.env_var:
            updates.append(f"{plan.env_var}={plan.secret_id}:latest")
            remove_env.add(plan.env_var)
        if plan.mount_path:
            updates.append(f"{plan.mount_path}={plan.secret_id}:latest")

    return updates, remove_env


###############################################################################
# Artifact Registry helper                                                   #
###############################################################################


def _ensure_artifact_registry_enabled(project_id: str) -> None:
    """Ensure Artifact Registry API is enabled for *project_id*.

    This is separated from the Gmail helper because Artifact Registry is used
    for every build push, regardless of whether e-mail sending is required.
    """

    _ensure_service_enabled(project_id, "artifactregistry.googleapis.com")



def _fetch_existing_env_config(project_id: str, region: str, service: str) -> dict[str, dict[str, str]]:
    """Return current Cloud Run env var definitions for *service*."""
    try:
        raw = _run_cmd(
            f"gcloud run services describe {service} --platform managed --project {project_id} --region {region} --format=json"
        )
    except RuntimeError:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.debug("Unable to parse existing service config for %s", service)
        return {}

    containers = (
        data.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    if not containers:
        return {}

    env_entries = containers[0].get("env") or []
    env_config: dict[str, dict[str, str]] = {}
    for entry in env_entries:
        name = entry.get("name")
        if not name:
            continue
        if "valueFrom" in entry:
            secret_ref = entry["valueFrom"].get("secretKeyRef")
            if secret_ref:
                env_config[name] = {
                    "type": "secret",
                    "secret": secret_ref.get("name", ""),
                    "version": secret_ref.get("key", "latest"),
                }
                continue
        env_config[name] = {
            "type": "value",
            "value": entry.get("value", ""),
        }

    return env_config


###############################################################################
# Git helpers (remote / local branch handling)                                #
###############################################################################


def _git_current_branch() -> str:
    return _run_cmd("git rev-parse --abbrev-ref HEAD")


def _git_has_remote() -> bool:
    try:
        return bool(_run_cmd("git remote -v"))
    except Exception:
        return False


def _git_checkout(branch: str) -> None:
    try:
        _run_cmd(f"git checkout {shlex.quote(branch)}")
    except RuntimeError:
        # Fallback: try to check out remote branch locally
        _run_cmd(f"git checkout -b {shlex.quote(branch)} origin/{shlex.quote(branch)}")


def _git_pull(branch: str) -> None:
    _run_cmd("git fetch origin")
    _run_cmd(f"git pull origin {shlex.quote(branch)}")


def _git_tag_and_push(tag: str, message: str) -> None:
    _run_cmd(f"git tag -a {tag} -m \"{message}\"")
    _run_cmd("git push origin --tags")


###############################################################################
# Docker helpers                                                              #
###############################################################################


def _docker_is_running() -> bool:
    try:
        _run_cmd("docker info >NUL" if os.name == "nt" else "docker info > /dev/null")
        return True
    except Exception:
        return False


def _docker_build(image: str, *, build_args: Optional[List[str]] = None, labels: Optional[List[str]] = None, extra_args: Optional[List[str]] = None, no_cache: bool = False) -> None:
    args_parts: List[str] = []
    if no_cache:
        args_parts.append("--no-cache")
    if build_args:
        args_parts.extend([f"--build-arg {a}" for a in build_args])
    if labels:
        args_parts.extend([f"--label {l}" for l in labels])
    if extra_args:
        args_parts.extend(extra_args)
    args = " " + " ".join(args_parts) if args_parts else ""
    _run_cmd(f"docker build{args} -t {image} .")


def _docker_push(image: str) -> None:
    _run_cmd(f"docker push {image}")


###############################################################################
# Auth helpers                                                               #
###############################################################################


def _verify_gcloud_auth() -> None:
    """Ensure there is an active gcloud account configured and gcloud itself is usable.

    We intentionally *do not* attempt any automated login - the user must
    perform `gcloud auth login` (browser flow) themselves.  Instead we fail
    fast with actionable guidance.
    """

    try:
        active = _run_cmd(
            "gcloud auth list --filter=status:ACTIVE --format=\"value(account)\""
        )
    except RuntimeError as exc:
        raise SystemExit(
            "\n".join(
                [
                    "The Google Cloud CLI ('gcloud') is either not installed or not accessible in this shell.",
                    "Download & install it from:",
                    "  https://cloud.google.com/sdk/docs/install",
                    "",
                    "After installation restart your terminal or ensure the install directory is added to PATH,",
                    "then run:\n",
                    "  gcloud init",
                    "  gcloud auth login",
                ]
            )
        ) from exc

    if not active:
        raise SystemExit(
            "\n".join(
                [
                    "No active gcloud account detected.",
                    "Authenticate by running:",
                    "",
                    "  gcloud auth login",
                    "",
                    "and follow the browser prompts.  Then rerun the deployment script.",
                ]
            )
        )


def _docker_login(region: str) -> None:
    """Authenticate Docker to Artifact Registry for *region*."""
    registry = f"{region}-docker.pkg.dev"
    _run_cmd(f"gcloud auth print-access-token | docker login -u oauth2accesstoken --password-stdin https://{registry}")


###############################################################################
# MP3 upload helpers                                                           #
###############################################################################

def _ensure_mp3_uploaded(project_id: str, mp3_path: Path) -> str:
    """Return public URL for *mp3_path*, uploading to GCS if necessary.

    Compares the local file's MD5 checksum against any existing object in the
    `{project_id}-test-assets` bucket to avoid redundant uploads. Uses the
    shared utils.mp3_uploader.upload_mp3_to_twilio helper for the actual
    upload and to make the object public.
    """
    import base64
    import hashlib
    from google.cloud import storage  # type: ignore
    from utils.mp3_uploader import upload_mp3_to_twilio
    from twilio.rest import Client

    if not mp3_path.exists():
        raise FileNotFoundError(mp3_path)

    # Compute MD5 in base64 to match GCS metadata
    local_md5 = base64.b64encode(hashlib.md5(mp3_path.read_bytes()).digest()).decode()

    storage_client = storage.Client(project=project_id)
    bucket_name = f"{project_id}-test-assets"
    bucket = storage_client.bucket(bucket_name)
    blob_name = f"test-audio/{mp3_path.name}"
    blob = bucket.blob(blob_name)

    if blob.exists():
        blob.reload()
        if blob.md5_hash == local_md5:
            # Already uploaded and identical - ensure it's public then return URL
            blob.make_public()
            return blob.public_url

    # Upload (or overwrite) via helper - the Twilio Client isn't used by helper
    dummy_client = Client("", "")
    return upload_mp3_to_twilio(dummy_client, mp3_path)

###############################################################################
# Core deploy logic                                                           #
###############################################################################


def deploy(
    *,
    project_id: str,
    region: str,
    service: str,
    env_vars: dict[str, str],
    env_overrides: Optional[set[str]] = None,
    remote_branch: Optional[str] = None,
    local_branch: Optional[str] = None,
    always_warm: bool = DEFAULT_ALWAYS_WARM,
    cpu: str = DEFAULT_CPU,
    memory: str = DEFAULT_MEMORY,
    port: int = DEFAULT_PORT,
    timeout: int = DEFAULT_CLOUDRUN_TIMEOUT,
    no_cache: bool = False,
    health_endpoint: Optional[str] = None,
    build_args: Optional[List[str]] = None,
    extra_args: Optional[List[str]] = None,
    labels: Optional[List[str]] = None,
    secret_config: Optional[Path] = None,
) -> None:
    """Build, push and deploy the service to Cloud Run."""

    env_overrides = env_overrides or set()

    # 1. Git branch handling --------------------------------------------------
    original_branch = _git_current_branch()
    deploy_branch = original_branch

    if remote_branch and local_branch:
        raise SystemExit("--remote-branch and --local-branch are mutually exclusive")

    if remote_branch:
        deploy_branch = remote_branch
        _git_checkout(remote_branch)
        _git_pull(remote_branch)
    elif local_branch and local_branch != original_branch:
        deploy_branch = local_branch
        _git_checkout(local_branch)

    # Clean failed revisions before deployment
    _delete_failed_revisions(project_id, region, service)

    # Ensure ADC credentials
    _ensure_adc(project_id)

    # Ensure we are authenticated with gcloud before continuing
    _verify_gcloud_auth()

    secret_plans = _load_secret_plans(secret_config)
    gmail_required = any(key.startswith("GMAIL_") for key in env_vars)
    if not gmail_required:
        gmail_required = any(
            (
                (plan.env_var and plan.env_var.startswith("GMAIL_"))
                or (plan.mount_path and "gmail" in plan.mount_path.lower())
            )
            for plan in secret_plans
        )

    # ------------------------------------------------------------------
    # Upload MP3 assets (thinking/finished sounds) and inject URLs
    # ------------------------------------------------------------------
    sounds_dir = Path(__file__).resolve().parent / "sounds"
    env_vars.setdefault("SOUND_THINKING_URL", _ensure_mp3_uploaded(project_id, sounds_dir / "thinking.mp3"))
    env_vars.setdefault("SOUND_FINISHED_URL", _ensure_mp3_uploaded(project_id, sounds_dir / "finished.mp3"))

    # 2. Generate image URI ---------------------------------------------------
    # Make sure gcloud is targeting the correct project
    _ensure_gcp_project(project_id)
    # Ensure billing is linked before enabling any service APIs
    _ensure_project_billing(project_id)
    if gmail_required:
        _ensure_gmail_api_enabled(project_id)
        _ensure_google_docs_api_enabled(project_id)
        _ensure_google_drive_api_enabled(project_id)
    if secret_plans:
        _ensure_secret_manager_api_enabled(project_id)
    # Ensure the Cloud Run service account can *read* the Gmail key secret.
    project_number = _run_cmd(
        f'gcloud projects describe {project_id} --format="value(projectNumber)"'
    )
    service_account_email = f"{project_number}-compute@developer.gserviceaccount.com"

    secret_updates_from_config: list[str] = []
    if secret_plans:
        updates, remove_env = _apply_secret_plans(project_id, secret_plans, service_account_email)
        secret_updates_from_config.extend(updates)
        for key in remove_env:
            env_vars.pop(key, None)
    # Ensure Cloud Run API is enabled - required for the deploy step
    _ensure_cloud_run_api_enabled(project_id)
    _ensure_vertex_ai_api_enabled(project_id)
    _ensure_artifact_registry_enabled(project_id)
    _run_cmd(f"gcloud config set project {project_id}")

    timestamp = _dt.datetime.utcnow().strftime("%Y%m%d%H%M")
    repo = f"{region}-docker.pkg.dev/{project_id}/{service}/{service}"

    # Ensure repository exists (idempotent)
    try:
        _run_cmd(f"gcloud artifacts repositories describe {service} --location {region}")
    except RuntimeError:
        log.info("Creating Artifact Registry repository %s in %s", service, region)
        _confirm_once(
            f"Create Artifact Registry repository '{service}' in region '{region}' for project '{project_id}'?"
        )
        _run_cmd(
            " ".join(
                [
                    "gcloud artifacts repositories create",
                    service,
                    "--repository-format=docker",
                    f"--location {region}",
                    "--quiet",
                ]
            )
        )

    image = f"{repo}:{timestamp}"

    # 3. Build & push ---------------------------------------------------------
    if not _docker_is_running():
        raise SystemExit("Docker daemon not running - please start Docker Desktop.")

    build_args_full = (build_args or []) + [f"BUILD_VERSION={timestamp}"]
    labels_full = (labels or []) + [f"build_version={timestamp}"]

    _docker_build(
        image,
        build_args=build_args_full,
        labels=labels_full,
        extra_args=extra_args,
        no_cache=no_cache,
    )

    # Ensure docker auth helper is configured for Artifact Registry
    _run_cmd(f"gcloud auth configure-docker {region}-docker.pkg.dev")

    # Log in docker with access token for current region
    _docker_login(region)

    _docker_push(image)

    # 4. Deploy ---------------------------------------------------------------
    existing_env = _fetch_existing_env_config(project_id, region, service)
    secret_updates: list[str] = list(secret_updates_from_config)
    env_assignments: list[str] = []

    def _append_unique(items: list[str], value: str) -> None:
        if value and value not in items:
            items.append(value)

    for key, value in env_vars.items():
        existing = existing_env.get(key)
        if existing and existing.get("type") == "secret" and key not in env_overrides:
            secret_name = existing.get("secret", "")
            version = existing.get("version", "latest") or "latest"
            if secret_name:
                _append_unique(secret_updates, f"{key}={secret_name}:{version}")
            continue
        env_assignments.append(f"{key}={value}")

    env_flag = ",".join(env_assignments)
    secrets_flag = ",".join(secret_updates)

    min_instances = 1 if always_warm else 0

    # Keep the service single-threaded: one request per instance, one instance warm.
    cmd_parts = [
        "gcloud run deploy",
        service,
        f"--image {image}",
        f"--region {region}",
        "--platform managed",
        f"--min-instances {min_instances}",
        "--max-instances 1",
        "--concurrency 1",
        f"--cpu {cpu}",
        f"--memory {memory}",
        f"--port {port}",
        f"--timeout {timeout}",
        "--allow-unauthenticated",
    ]
    if env_flag:
        cmd_parts.append(f"--set-env-vars {env_flag}")
    if secrets_flag:
        cmd_parts.append(f"--update-secrets {secrets_flag}")

    _run_cmd(" ".join(cmd_parts), capture=False)

    url = _run_cmd(
        f"gcloud run services describe {service} --region {region} --format \"value(status.url)\""
    )
    log.info("Deployed to: %s", url)

    # ------------------------------------------------------------------
    # Post-deploy guidance - Twilio Voice webhook setup
    # ------------------------------------------------------------------
    webhook_url = f"{url.rstrip('/')}/twiml"
    guidance = "\n".join(
        [
            "\nNext steps - connect Twilio:\n",  # leading blank line for readability
            "1. Log in to the Twilio Console (https://console.twilio.com).",
            "2. Navigate to \"Phone Numbers -> Manage -> Active numbers\" and select the number you want to use.",
            "3. In the 'Voice & Fax' tab, under 'A CALL COMES IN', choose 'Webhook'.",
            "4. Set the URL to:  %s" % webhook_url,
            "   (Method: GET - Twilio will request the TwiML with a simple GET)",
            "5. Click 'Save'.",
            "\nTo verify the service is running: call the number and ensure your Ringdown agent greets you, or open the webhook URL in a browser - it should return valid TwiML.\n",
        ]
    )
    print(guidance)

    # 4b. Wait for new revision to be ready -----------------------------------
    try:
        # Lazy import here as well
        from google.cloud import run_v2  # type: ignore
        svc_client = run_v2.ServicesClient()
        svc_path = f"projects/{project_id}/locations/{region}/services/{service}"
        svc_obj = svc_client.get_service(name=svc_path)
        new_rev = (svc_obj.latest_created_revision or '').split('/')[-1]
        if new_rev:
            log.info("Waiting for revision %s to become Ready ...", new_rev)
            _wait_for_revision_ready(project_id, region, service, new_rev)
            log.info("Revision ready.")
    except Exception as exc:
        log.error("Error while waiting for revision readiness: %s", exc)
        raise

    # 4b.2  Post-revision cleanup - remove any older failed revisions --------
    # A new revision now exists, so the previous *failed* latest revision (if
    # any) is no longer protected by Cloud Run and can be deleted.  Running
    # the helper here keeps the failed-revision count bounded at one.
    _delete_failed_revisions(project_id, region, service)

    # 4c. Verify image label integrity ---------------------------------------
    try:
        _verify_image_label(image, timestamp)
    except Exception as exc:
        log.error("Image label verification failed: %s", exc)
        raise

    # 4d. Final health check --------------------------------------------------
    if health_endpoint:
        _perform_final_health_check(url, health_endpoint)

    # 5. Tag git --------------------------------------------------------------
    if _git_has_remote():
        tag_name = f"deploy-{timestamp}"
        _git_tag_and_push(tag_name, f"Cloud Run deploy {timestamp} from {deploy_branch}")

    # 6. Switch back ----------------------------------------------------------
    if original_branch != deploy_branch:
        _git_checkout(original_branch)


###############################################################################
# CLI                                                                         #
###############################################################################


def _parse_env_vars(values: List[str]) -> dict[str, str]:
    env_dict: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise argparse.ArgumentTypeError(f"{item!r} is not in KEY=VALUE format")
        k, v = item.split("=", 1)
        env_dict[k] = v
    return env_dict


def main(argv: Optional[List[str]] = None) -> None:
    print("[cloudrun-deploy] initializing...")
    load_dotenv(override=False)
    
    # Check if virtual environment is active
    if not os.environ.get("VIRTUAL_ENV"):
        print("WARNING: Virtual environment (.venv) is not active.")
        print("Consider activating it with: .venv\\Scripts\\Activate.ps1")
        print()
    
    print("[cloudrun-deploy] ensuring gcloud on PATH...")
    _ensure_gcloud_on_path()

    parser = argparse.ArgumentParser(description="Deploy the service to Cloud Run")
    parser.add_argument("--project-id", help="GCP project ID (default: gcloud config value)")
    parser.add_argument("--region", default=DEFAULT_REGION, help="GCP region (default: %(default)s)")
    parser.add_argument("--service", default=DEFAULT_SERVICE, help="Cloud Run service name (default: %(default)s)")

    branch = parser.add_mutually_exclusive_group()
    branch.add_argument("--remote-branch", help="Deploy branch from origin & pull latest")
    branch.add_argument("--local-branch", help="Deploy local branch as is (no pull)")

    parser.add_argument(
        "--env", nargs="*", default=[], metavar="KEY=VAL", help="Additional env vars for Cloud Run"
    )
    parser.add_argument(
        "--secret-config",
        default="secret-manager.yaml",
        help="Path to secret configuration YAML (default: %(default)s)",
    )

    parser.add_argument("--no-cache", action="store_true", help="Build Docker image with --no-cache")
    parser.add_argument("--health-endpoint", help="Relative URL path to perform health check after deploy")
    parser.add_argument("--build-arg", nargs="*", default=[], metavar="KEY=VAL", help="Additional --build-arg for docker build")
    parser.add_argument("--docker-arg", nargs="*", default=[], metavar="ARG", help="Extra raw arg to pass to docker build (e.g. --platform linux/amd64)")
    parser.add_argument("--label", nargs="*", default=[], metavar="KEY=VAL", help="Additional image label key=value pairs")
    parser.add_argument("--timeout", type=int, default=DEFAULT_CLOUDRUN_TIMEOUT, help=f"Request timeout in seconds (default: {DEFAULT_CLOUDRUN_TIMEOUT}s/60min)")

    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmations (assume yes)")

    print("[cloudrun-deploy] parsing args...")
    args = parser.parse_args(argv)

    global _AUTO_APPROVE
    _AUTO_APPROVE = args.yes

    print("[cloudrun-deploy] resolving project id...")
    project_id = (
        args.project_id
        or DEFAULT_PROJECT_ID
        or _run_cmd("gcloud config get-value project")
    )
    if not project_id:
        raise SystemExit(
            "No project ID specified. Pass --project-id, set DEPLOY_PROJECT_ID or "
            "LIVE_TEST_PROJECT_ID, or configure a default with 'gcloud config set project <id>'."
        )

    # Include common LLM provider keys so they propagate to Cloud Run if present.
    _DEFAULT_ENV_KEYS = (
        "OPENAI_API_KEY",    # OpenAI models
        "GOOGLE_API_KEY",   # Gemini / Google Generative AI
        "ANTHROPIC_API_KEY",  # Claude models
        "TAVILY_API_KEY",     # Tavily search
        "TWILIO_AUTH_TOKEN",  # Twilio webhook validation
        "GMAIL_IMPERSONATE_EMAIL",  # Gmail impersonation
        "GMAIL_SA_KEY_PATH",  # Gmail service account path
    )

    env_vars = {k: os.environ[k] for k in _DEFAULT_ENV_KEYS if k in os.environ}
    cli_env = _parse_env_vars(args.env)
    env_vars.update(cli_env)
    cli_env_keys = set(cli_env)

    # Ensure Vertex AI identification is always available for LiteLLM
    env_vars.setdefault("VERTEXAI_PROJECT", project_id)
    env_vars.setdefault("VERTEXAI_LOCATION", args.region)

    print("[cloudrun-deploy] starting deploy...")
    secret_config_path = Path(args.secret_config).expanduser() if args.secret_config else None

    deploy(
        project_id=project_id,
        region=args.region,
        service=args.service,
        env_vars=env_vars,
        env_overrides=cli_env_keys,
        remote_branch=args.remote_branch,
        local_branch=args.local_branch,
        timeout=args.timeout,
        no_cache=args.no_cache,
        health_endpoint=args.health_endpoint,
        build_args=args.build_arg,
        extra_args=args.docker_arg,
        labels=args.label,
        secret_config=secret_config_path,
    )
    # Print completion time in Pacific Time
    pt_time = _dt.datetime.now(ZoneInfo("America/Los_Angeles"))
    print(f"Deployment completed at {pt_time:%Y-%m-%d %H:%M:%S %Z}")


###############################################################################
# Cloud Run revision / health helpers                                         #
###############################################################################


def _show_recent_revisions(project_id: str, region: str, service: str, *, limit: int = DEFAULT_REVISION_HISTORY_LIMIT) -> None:
    """Log the *limit* most recent revisions with their traffic allocation and readiness."""
    # Lazy import to avoid heavy dependency load at module import time
    from google.cloud import run_v2  # type: ignore
    try:
        svc_path = f"projects/{project_id}/locations/{region}/services/{service}"
        svc_client = run_v2.ServicesClient()
        rev_client = run_v2.RevisionsClient()

        service_obj = svc_client.get_service(name=svc_path)
        traffic_map: dict[str, int] = {}
        if service_obj.traffic:
            for t in service_obj.traffic:
                if t.revision:
                    traffic_map[t.revision.split("/")[-1]] = t.percent

        revisions = list(rev_client.list_revisions(parent=svc_path))
        revisions.sort(key=lambda r: r.create_time, reverse=True)

        log.info("Recent revisions (newest first):")
        for rev in revisions[:limit]:
            rev_id = rev.name.split("/")[-1]
            percent = traffic_map.get(rev_id, 0)
            ready_cond = next((c for c in rev.conditions if c.type == "Ready"), None)
            healthy = ready_cond and ready_cond.state == run_v2.Condition.State.CONDITION_SUCCEEDED
            log.info("  %s  traffic=%3s%%  status=%s", rev_id, percent, "healthy" if healthy else "failed")
    except Exception as exc:
        log.error("Unable to list revisions: %s", exc)


def _delete_failed_revisions(project_id: str, region: str, service: str) -> None:
    """Delete failed revisions that have 0% traffic allocation.

    Cloud Run does not allow deleting the *latest created* revision even if it
    failed.  Attempting to do so results in a 400 error:

        The latest created Revision "<id>" cannot be directly deleted.

    We therefore skip that specific revision and rely on a future deploy (once
    a newer revision exists) to clean it up.  This makes the cleanup logic
    idempotent and avoids unnecessary noise in the logs while still removing
    all other failed revisions.
    """
    # Lazy import to avoid heavy dependency load at module import time
    from google.cloud import run_v2  # type: ignore

    svc_path = f"projects/{project_id}/locations/{region}/services/{service}"
    svc_client = run_v2.ServicesClient()
    rev_client = run_v2.RevisionsClient()

    try:
        service_obj = svc_client.get_service(name=svc_path)
    except Exception:
        return  # Service doesn't exist yet

    latest_created: str = ""
    if getattr(service_obj, "latest_created_revision", None):
        latest_created = service_obj.latest_created_revision.split("/")[-1]

    active = {t.revision.split("/")[-1] for t in service_obj.traffic if t.percent > 0 and t.revision}

    to_delete: list[str] = []
    for rev in rev_client.list_revisions(parent=svc_path):
        rev_id = rev.name.split("/")[-1]
        # Skip active revisions and the most recent (latest_created) revision
        if rev_id in active or rev_id == latest_created:
            continue
        ready_cond = next((c for c in rev.conditions if c.type == "Ready"), None)
        if ready_cond and ready_cond.state != run_v2.Condition.State.CONDITION_SUCCEEDED:
            to_delete.append(rev.name)

    for name in to_delete:
        rev_id = name.split("/")[-1]
        log.info("Deleting failed revision %s", rev_id)
        try:
            rev_client.delete_revision(name=name)
        except Exception as exc:
            log.error("Failed to delete revision %s: %s", rev_id, exc)

    if to_delete:
        _show_recent_revisions(project_id, region, service)


@retry(stop=stop_after_attempt(DEFAULT_READINESS_ATTEMPTS), wait=wait_fixed(DEFAULT_READINESS_WAIT_SECONDS))
def _wait_for_revision_ready(project_id: str, region: str, service: str, revision: str) -> None:
    """Block until *revision*'s Ready condition succeeds, else raise."""
    # Lazy import to avoid heavy dependency load at module import time
    from google.cloud import run_v2  # type: ignore

    rev_client = run_v2.RevisionsClient()
    rev_path = f"projects/{project_id}/locations/{region}/services/{service}/revisions/{revision}"
    rev = rev_client.get_revision(name=rev_path)
    ready = next((c for c in rev.conditions if c.type == "Ready"), None)
    if ready and ready.state == run_v2.Condition.State.CONDITION_SUCCEEDED:
        return
    if ready and ready.message:
        raise RuntimeError(ready.message)
    raise RuntimeError("Revision not ready yet")


def _verify_image_label(image: str, expected_version: str) -> None:
    """Ensure the Docker *image* carries a build_version label matching *expected_version*."""
    # Reliable quoting across shells is tricky; on Windows we skip this check
    if os.name == "nt":
        log.debug("Skipping image label verification on Windows host")
        return

    version = _run_cmd(
        f"docker inspect --format '{{{{ index .Config.Labels \"build_version\" }}}}' {image}"
    )
    if version != expected_version:
        raise RuntimeError(
            f"Image build_version label mismatch: expected {expected_version}, got {version}"
        )


def _ensure_adc(project_id: str) -> None:
    """Ensure Application Default Credentials exist, otherwise launch login flow."""
    try:
        _run_cmd("gcloud auth application-default print-access-token")
    except RuntimeError:
        log.info("Configuring Application Default Credentials ...")
        _run_cmd(f"gcloud auth application-default login --project {project_id}")


def _perform_final_health_check(base_url: str, endpoint: str, *, status: int = 200, timeout: int = DEFAULT_HEALTH_TIMEOUT_SECONDS) -> None:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    log.info("Final health check: %s", url)
    # Lazy import to avoid heavy dependency load at module import time
    import requests  # type: ignore
    resp = requests.get(url, timeout=timeout)
    if resp.status_code != status:
        raise RuntimeError(f"Final health check failed ({resp.status_code})")
    log.info("Service health check OK (%s)", resp.status_code)


###############################################################################
# Billing helpers                                                            #
###############################################################################


def _ensure_project_billing(project_id: str) -> None:
    """Ensure *project_id* is linked to an open billing account.

    If the env-var DEPLOY_BILLING_ACCOUNT_ID is set, that account is used.
    Otherwise we pick the first open billing account returned by gcloud.
    """

    # 1. Check if already linked
    try:
        linked = _run_cmd(
            f'gcloud beta billing projects describe {project_id} --format="value(billingAccountName)"'
        )
        if linked:
            log.info("Project %s already linked to billing account %s", project_id, linked.split("/")[-1])
            return
    except RuntimeError as exc:
        # Not fatal - may happen if API not enabled yet.
        log.debug("Billing describe failed: %s", exc)

    # 2. Determine candidate billing account
    candidate = os.environ.get("DEPLOY_BILLING_ACCOUNT_ID", "")

    if not candidate:
        try:
            accounts_raw = _run_cmd(
                'gcloud beta billing accounts list --filter=open=true --format="value(name)"'
            )
            accounts = [a for a in accounts_raw.splitlines() if a]
            if accounts:
                candidate = accounts[0]
        except RuntimeError as exc:
            log.debug("Unable to list billing accounts: %s", exc)

    if not candidate:
        raise SystemExit(
            "No billing account linked to project and none could be selected automatically. "
            "Set DEPLOY_BILLING_ACCOUNT_ID or link via Cloud Console."
        )

    acct_id = candidate.split("/")[-1]
    log.info("Linking project %s to billing account %s", project_id, acct_id)
    _confirm_once(
        f"About to link project '{project_id}' to billing account '{acct_id}'."
    )
    _run_cmd(
        f"gcloud beta billing projects link {project_id} --billing-account {acct_id} --quiet"
    )


###############################################################################
# Interactive confirmation handling                                          #
###############################################################################


_AUTO_APPROVE = False  # set from CLI --yes or env var


def _confirm_once(message: str) -> None:
    """Prompt the user before performing a *one-time/destructive* action.

    Skipped automatically when the global _AUTO_APPROVE flag is True or when
    the process is running non-interactive (e.g. CI) - detected via the
    CI/DEPLOY_AUTO_APPROVE environment variables.
    """

    if _AUTO_APPROVE or os.environ.get("CI") or os.environ.get("DEPLOY_AUTO_APPROVE"):
        return

    try:
        input(f"{message}\nPress <Enter> to continue or Ctrl+C to abort ... ")
    except KeyboardInterrupt:
        raise SystemExit("Aborted by user.")


###############################################################################
# Cloud Run helper
###############################################################################


def _ensure_cloud_run_api_enabled(project_id: str) -> None:
    """Ensure the Cloud Run API is enabled for *project_id*."""

    _ensure_service_enabled(project_id, "run.googleapis.com")


###############################################################################
# Vertex AI helper
###############################################################################


def _ensure_vertex_ai_api_enabled(project_id: str) -> None:
    """Ensure the Vertex AI (AI Platform) API is enabled for *project_id*."""

    _ensure_service_enabled(project_id, "aiplatform.googleapis.com")


def _ensure_secret_accessor(project_id: str, secret_name: str, service_account: str) -> None:
    """Grant *service_account* the Secret Manager accessor role on *secret_name*.

    The binding is added only if it does not already exist, making the call
    safe and idempotent for every deploy.
    """

    try:
        policy_json = _run_cmd(
            " ".join([
                "gcloud secrets get-iam-policy",
                secret_name,
                f"--project {project_id}",
                "--format=json",
            ])
        )
        policy = json.loads(policy_json or "{}")
        for binding in policy.get("bindings", []):
            if binding.get("role") == SECRET_ACCESSOR_ROLE and \
               f"serviceAccount:{service_account}" in binding.get("members", []):
                log.info("Service account %s already has access to secret %s", service_account, secret_name)
                return  # Already bound
    except Exception as exc:  # pylint: disable=broad-except
        log.debug("Unable to inspect IAM policy for secret %s: %s", secret_name, exc)

    _confirm_once(
        f"Grant {SECRET_ACCESSOR_ROLE} on secret '{secret_name}' to service account '{service_account}'?"
    )
    _run_cmd(
        " ".join([
            "gcloud secrets add-iam-policy-binding",
            secret_name,
            f"--project {project_id}",
            f"--member serviceAccount:{service_account}",
            f"--role {SECRET_ACCESSOR_ROLE}",
            "--quiet",
        ])
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1) 
