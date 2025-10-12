"""Gmail email sending tool.

Authentication:

**Delegated service-account credential** – production path.
• Cloud Run mounts Secret-Manager secret `gmail-sa-key` at
  `/var/secrets/gmail-sa-key.json` (handled automatically by
  `cloudrun-deploy.py`).
• Env vars must be present:
    - `GMAIL_SA_KEY_PATH=/var/secrets/gmail-sa-key.json`
    - `GMAIL_IMPERSONATE_EMAIL=<your mailbox>`

The code loads that JSON key, impersonates the mailbox via
:pymeth:`Credentials.with_subject`, and sends with the Gmail API.

Rate limiting (1 email / 10 s) and per-agent recipient *greenlist* validation
are enforced.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
from email.message import EmailMessage
from typing import Any, Dict, Optional
import os

from pydantic import BaseModel, Field, field_validator

from ..tool_framework import register_tool

# Third-party helper – handles sleep & concurrency for rate limiting
from ratelimit import limits, sleep_and_retry

from app.settings import get_admin_emails, get_default_email
from app import settings as _settings


class GmailIntegrationDisabled(RuntimeError):
    """Raised when Gmail API credentials are not configured."""


# Load default allowed recipients from config
_DEFAULT_ALLOWED_RECIPIENTS = get_admin_emails()
_DEFAULT_ENFORCED = bool(
    _settings._load_config()["defaults"].get("email_greenlist_enforced", False)
)


def _get_agent_context() -> Dict[str, Any] | None:
    return getattr(_agent_context, 'config', None)

logger = logging.getLogger(__name__)

# Rate limiter state
_RATE_LIMIT_SECONDS = int(os.getenv("RINGDOWN_EMAIL_RATE_LIMIT_SECONDS", "10"))

# Decorator-based limiter: 1 call per _RATE_LIMIT_SECONDS.
# We wrap the Gmail API execute call with this limiter so the blocking happens
# only around the network request – not the local processing time.

@sleep_and_retry
@limits(calls=1, period=_RATE_LIMIT_SECONDS)
def _send_gmail(service, raw: str):
    """Helper: invoke Gmail API respecting global rate limit."""

    return service.users().messages().send(userId="me", body={"raw": raw}).execute()

# Thread-local storage for agent context
_agent_context = threading.local()


def set_agent_context(agent_config: Dict[str, Any] | None) -> None:
    """Set the current agent configuration in thread-local storage."""
    _agent_context.config = agent_config


def get_agent_context() -> Dict[str, Any] | None:
    """Get the current agent configuration from thread-local storage."""
    return _get_agent_context()


def _resolve_greenlist() -> tuple[list[str] | None, bool]:
    """Return (allowed_list, enforce_bool)."""

    agent_config = _get_agent_context()
    if agent_config is None:
        return _DEFAULT_ALLOWED_RECIPIENTS, _DEFAULT_ENFORCED

    enforce_value = agent_config.get("email_greenlist_enforced")
    enforce = _DEFAULT_ENFORCED if enforce_value is None else bool(enforce_value)
    if not enforce:
        return None, False

    greenlist = agent_config.get('email_greenlist')
    if greenlist:
        return list(greenlist), True

    return _DEFAULT_ALLOWED_RECIPIENTS, True


def _integration_preflight() -> tuple[bool, str | None, Any | None]:
    """Check Gmail credentials without performing API requests."""

    key_path = os.getenv("GMAIL_SA_KEY_PATH")
    impersonate = os.getenv("GMAIL_IMPERSONATE_EMAIL")

    missing: list[str] = []
    if not key_path:
        missing.append("GMAIL_SA_KEY_PATH")
    if not impersonate:
        missing.append("GMAIL_IMPERSONATE_EMAIL")

    if missing:
        if _get_gmail_service is not _ORIGINAL_GET_GMAIL_SERVICE:
            try:
                service = _get_gmail_service()
            except GmailIntegrationDisabled:
                service = None
            if service is not None:
                return True, None, service
        message = (
            "Gmail integration is disabled because the following environment "
            f"variables are not set: {', '.join(missing)}."
        )
        return False, message, None

    try:
        _resolve_service_account_credentials(key_path, impersonate, scopes=["https://www.googleapis.com/auth/gmail.send"], dry_run=True)
    except GmailIntegrationDisabled as exc:
        if _get_gmail_service is not _ORIGINAL_GET_GMAIL_SERVICE:
            try:
                service = _get_gmail_service()
            except GmailIntegrationDisabled:
                service = None
            if service is not None:
                return True, None, service
        return False, str(exc), None

    return True, None, None


def _is_recipient_allowed(email: str) -> bool:
    """Check if email matches any greenlist pattern for the current agent."""
    allowed, enforce = _resolve_greenlist()
    if not enforce or allowed is None:
        return True
    for pattern in allowed:
        if pattern.startswith("^"):  # It's a regex pattern
            if re.fullmatch(pattern, email, re.IGNORECASE):
                return True
        elif pattern.lower() == email.lower():  # Exact match
            return True
    return False


def _get_gmail_service():
    """Get authenticated Gmail service.

    Uses a delegated service-account credential built from the JSON key file and
    impersonating the given mailbox. This is the recommended production path for
    Cloud Run when the Gmail API scope has been granted via domain-wide delegation.
    
    Required env vars:
    - GMAIL_SA_KEY_PATH: Path to service account JSON key file
    - GMAIL_IMPERSONATE_EMAIL: Email address to impersonate
    """

    key_path = os.getenv("GMAIL_SA_KEY_PATH")
    impersonate = os.getenv("GMAIL_IMPERSONATE_EMAIL")

    missing: list[str] = []
    if not key_path:
        missing.append("GMAIL_SA_KEY_PATH")
    if not impersonate:
        missing.append("GMAIL_IMPERSONATE_EMAIL")
    if missing:
        raise GmailIntegrationDisabled(
            "Gmail integration is disabled because the following environment "
            f"variables are not set: {', '.join(missing)}."
        )

    scopes = ["https://www.googleapis.com/auth/gmail.send"]

    creds = _resolve_service_account_credentials(key_path, impersonate, scopes=scopes)

    from googleapiclient.discovery import build  # type: ignore

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


_ORIGINAL_GET_GMAIL_SERVICE = _get_gmail_service


def _resolve_service_account_credentials(
    key_reference: str | None,
    impersonate: str | None,
    *,
    scopes: list[str],
    dry_run: bool = False,
):
    if not key_reference or not impersonate:
        missing = []
        if not key_reference:
            missing.append("GMAIL_SA_KEY_PATH")
        if not impersonate:
            missing.append("GMAIL_IMPERSONATE_EMAIL")
        raise GmailIntegrationDisabled(
            "Gmail integration is disabled because the following environment "
            f"variables are not set: {', '.join(missing)}."
        )

    from google.oauth2 import service_account  # type: ignore

    credentials = None
    if os.path.exists(key_reference):
        credentials = service_account.Credentials.from_service_account_file(
            key_reference, scopes=scopes
        )
    else:
        try:
            key_data = json.loads(key_reference)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive branch
            raise GmailIntegrationDisabled(
                f"GMAIL_SA_KEY_PATH points to missing file or invalid JSON; value begins with {key_reference[:32]!r}."
            ) from exc
        credentials = service_account.Credentials.from_service_account_info(
            key_data, scopes=scopes
        )

    if dry_run:
        return credentials
    return credentials.with_subject(impersonate)


class EmailArgs(BaseModel):
    to: str = Field(..., description="Recipient email address")
    subject: str = Field(..., description="Email subject line")
    body: str = Field(..., description="Email body content (plain text)")

    @field_validator("to")
    @classmethod
    def _validate_recipient(cls, value: str) -> str:
        if not _is_recipient_allowed(value):
            raise ValueError("recipient not permitted for this agent")
        return value
    


@register_tool(
    name="SendEmail",
    description="Send an email.",
    param_model=EmailArgs,
    prompt=f"## send_email\nIf asked to email something, use send_email. Choose an appropriate subject. Default recipient is {get_default_email()} unless otherwise specified.",
    async_execution=True
)
def send_email(args: EmailArgs) -> Dict[str, Any]:
    """Send email via Gmail API with rate limiting."""
    
    try:
        # Create message
        message = EmailMessage()
        message['To'] = args.to
        # Prepend bot name to subject if provided in the agent configuration.
        agent_cfg: Dict[str, Any] | None = get_agent_context()
        bot_name: str | None = None
        if agent_cfg is not None:
            bot_name = agent_cfg.get("bot_name")

        subject: str = f"[{bot_name}] {args.subject}" if bot_name else args.subject

        message['Subject'] = subject
        message.set_content(args.body)
        
        # In production, the 'From' will be the service account email
        # You may want to set a friendly from address if your service account
        # has send-as permissions configured
        
        # Enforce recipient policy AFTER building the message so we can return
        # consistent response objects.
        if not _is_recipient_allowed(args.to):
            logger.info("Email not sent – recipient %s is outside the allowed green-list", args.to)
            return {
                "success": False,
                "rejected": True,
                "reason": "recipient_not_allowed",
                "to": args.to,
                "subject": subject,
            }

        # Send via Gmail API
        service = getattr(args, "_preflight_payload", None)
        if service is None:
            try:
                service = _get_gmail_service()
            except GmailIntegrationDisabled as exc:
                logger.warning("Email integration disabled: %s", exc)
                return {
                    "success": False,
                    "disabled": True,
                    "reason": "integration_disabled",
                    "message": str(exc),
                    "async_execution": False,
                    "to": args.to,
                    "subject": subject,
                }
        if service is None:
            message = "Gmail integration unavailable"
            logger.warning("Email integration disabled: %s", message)
            return {
                "success": False,
                "disabled": True,
                "reason": "integration_disabled",
                "message": message,
                "async_execution": False,
                "to": args.to,
                "subject": subject,
            }

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        result = _send_gmail(service, raw)
        
        logger.info(f"Email sent successfully to {args.to}, id: {result['id']}")
        
        return {
            "success": True,
            "message_id": result['id'],
            "to": args.to,
            "subject": subject
        }
        
    except GmailIntegrationDisabled as exc:
        logger.warning("Email integration disabled: %s", exc)
        return {
            "success": False,
            "disabled": True,
            "reason": "integration_disabled",
            "message": str(exc),
            "async_execution": False,
        }
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# Allow the tool framework to run configuration preflight checks before queuing
# asynchronous execution.
send_email.preflight_check = _integration_preflight  # type: ignore[attr-defined]
if hasattr(send_email, "__wrapped__"):
    send_email.__wrapped__.preflight_check = _integration_preflight  # type: ignore[attr-defined]
