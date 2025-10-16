from __future__ import annotations

"""Google Calendar integration tools.

Implements Create/Read/Search/Update/Delete operations that comply with the
specification in `todo-calendar-tool.md`.

Key design points
-----------------
* Service-account delegated auth – identical to Gmail/Docs tools.
* Ownership tracking via title suffix " [{bot_name}]".
* Validation performed via pydantic.
* Start-time input **must** be RFC3339 / ISO-8601.
* Duration supplied as integer minutes (default 60, min 1, max 240).
* All Google-Meet creation is implicit – not exposed to the LLM.
* Reminder handling for *reminder* mode (duration==30 & reminders==0).
"""

import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, validator
from google.oauth2 import service_account  # type: ignore
from googleapiclient.discovery import build  # type: ignore
from googleapiclient.errors import HttpError  # type: ignore
import json
from functools import lru_cache

from ..tool_framework import register_tool
from app import settings as _app_settings  # to fetch config defaults

logger = logging.getLogger(__name__)


class CalendarIntegrationDisabled(RuntimeError):
    """Raised when Google Calendar credentials are unavailable."""

# ---------------------------------------------------------------------------
# Thread-local agent context
# ---------------------------------------------------------------------------

_agent_context = threading.local()

def set_agent_context(agent_config: Dict[str, Any] | None) -> None:
    """Store agent config (contains bot_name, etc.) in thread-local."""
    _agent_context.config = agent_config

def get_agent_context() -> Dict[str, Any] | None:
    return getattr(_agent_context, "config", None)

# ---------------------------------------------------------------------------
# Config-driven constants
# ---------------------------------------------------------------------------

from app.settings import get_calendar_user_name as _get_cal_name

_CAL_USER_NAME = _get_cal_name()

_SUFFIX_TEMPLATE = " [{bot_name}]"

# The API scope required for full calendar access.
_SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Max/min duration limits (minutes)
_MIN_DURATION = 1
_MAX_DURATION = 240

# cache default timezone
try:
    _DEFAULT_TZ = _app_settings._load_config()["defaults"].get("timezone", "UTC")
except Exception:  # pragma: no cover
    _DEFAULT_TZ = "UTC"


def _get_calendar_service():
    """Authenticate and return a Google Calendar service object."""

    key_path = os.getenv("GMAIL_SA_KEY_PATH")
    impersonate = os.getenv("GMAIL_IMPERSONATE_EMAIL")

    missing: list[str] = []
    if not key_path:
        missing.append("GMAIL_SA_KEY_PATH")
    if not impersonate:
        missing.append("GMAIL_IMPERSONATE_EMAIL")
    if missing:
        raise CalendarIntegrationDisabled(
            "Google Calendar integration is disabled because the following "
            f"environment variables are not set: {', '.join(missing)}."
        )
    if not os.path.exists(key_path):
        raise CalendarIntegrationDisabled(
            f"Service-account key file not found at {key_path}."
        )

    creds = (
        service_account.Credentials.from_service_account_file(key_path, scopes=_SCOPES)
        .with_subject(impersonate)
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


@lru_cache(maxsize=1)
def _get_service_account_identity() -> Dict[str, str | None]:
    """Return identifying fields for the configured service account."""

    key_path = os.getenv("GMAIL_SA_KEY_PATH")
    if not key_path or not os.path.exists(key_path):
        return {"client_email": None, "client_id": None}

    try:
        with open(key_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"client_email": None, "client_id": None}

    return {
        "client_email": data.get("client_email"),
        "client_id": data.get("client_id"),
    }


def _bot_name() -> str:
    ctx = get_agent_context()
    if ctx and ctx.get("bot_name"):
        return ctx["bot_name"]

    # Fallback – derive from config defaults
    from app import settings as _settings  # local to avoid circular import during startup

    return _settings.get_default_bot_name()


def _suffix() -> str:
    return _SUFFIX_TEMPLATE.format(bot_name=_bot_name())


def _append_suffix(title: str) -> str:
    if title.endswith(_suffix()):
        return title
    return f"{title}{_suffix()}"


def _is_bot_event(event: Dict[str, Any]) -> bool:
    return event.get("summary", "").endswith(_suffix())


def _strip_suffix_from_title(title: str) -> str:
    if title.endswith(_suffix()):
        return title[: -len(_suffix())]
    return title

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class _BaseArgs(BaseModel):
    """Shared configuration for all arg models."""

    class Config:
        populate_by_name = True
        extra = "forbid"


class CreateEventArgs(_BaseArgs):
    title: str = Field(..., description="Event summary/title")
    start_time: datetime = Field(..., description="RFC3339 start time")
    duration_minutes: int = Field(60, gt=0, le=_MAX_DURATION, description="Event length in minutes")
    description: Optional[str] = Field(None, description="Event description")
    location: Optional[str] = Field(None, description="Physical or virtual location")
    attendees: Optional[List[str]] = Field(None, description="List of attendee email addresses")
    reminders: Optional[int] = Field(10, ge=0, le=1440, description="Popup reminder minutes before start")
    calendar_id: str = Field("primary", description="Target calendar ID")

    @field_validator("attendees")
    @classmethod
    def _validate_emails(cls, v):  # type: ignore[override]
        if not v:
            return v
        email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        for email in v:
            if not email_pattern.match(email):
                raise ValueError(f"Invalid email address: {email}")
        return v


class ReadEventArgs(_BaseArgs):
    event_id: str = Field(..., description="Event ID")
    calendar_id: str = Field("primary", description="Calendar ID")
    detail_level: str = Field("basic", description="basic or detailed")

    @field_validator("detail_level")
    @classmethod
    def _detail_level_ok(cls, v):  # type: ignore[override]
        if v not in {"basic", "detailed"}:
            raise ValueError("detail_level must be 'basic' or 'detailed'")
        return v


class SearchEventsArgs(_BaseArgs):
    calendar_id: str = Field("primary", description="Calendar ID or 'all'")
    starting_after: Optional[datetime] = Field(None, description="Start of window (RFC3339)")
    starting_before: Optional[datetime] = Field(None, description="End of window (RFC3339)")
    query: Optional[str] = Field(None, description="Search text")
    created_by_bot: bool = Field(False, description="Return only bot-created events")


class UpdateEventArgs(_BaseArgs):
    event_id: str
    calendar_id: str = Field("primary")
    # Optional fields for modifications
    title: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(None, gt=0, le=_MAX_DURATION)
    description: Optional[str] = None
    location: Optional[str] = None
    add_attendees: Optional[List[str]] = None
    remove_attendees: Optional[List[str]] = None
    reminders: Optional[int] = Field(None, ge=0, le=1440)


class DeleteEventArgs(_BaseArgs):
    event_id: str
    calendar_id: str = Field("primary")
    send_notifications: bool = Field(True)

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _dt_to_rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        # Assume default timezone from config
        if _DEFAULT_TZ.upper() == "UTC":
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            # Fallback: naive dt interpreted as local offset 0; RFC3339 requires tz.
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _build_reminders(minutes: int) -> Dict[str, Any]:
    return {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": minutes}],
    }


@register_tool(
    name="CreateCalendarEvent",
    description=f"Create a calendar event (or reminder). Use {_DEFAULT_TZ} unless you know {_CAL_USER_NAME} is somewhere else or they say otherwise.",
    param_model=CreateEventArgs,
    async_execution=True,
    category="output",
)
def create_calendar_event(args: CreateEventArgs) -> Dict[str, Any]:
    try:
        svc = _get_calendar_service()
    except CalendarIntegrationDisabled as exc:
        logger.warning("Calendar integration disabled: %s", exc)
        return {
            "success": False,
            "disabled": True,
            "reason": "integration_disabled",
            "message": str(exc),
        }

    # Build event body
    end_time = args.start_time + timedelta(minutes=args.duration_minutes)

    event_body: Dict[str, Any] = {
        "summary": _append_suffix(args.title),
        "start": {"dateTime": _dt_to_rfc3339(args.start_time)},
        "end": {"dateTime": _dt_to_rfc3339(end_time)},
        "reminders": _build_reminders(args.reminders or 10),
    }
    if args.description:
        event_body["description"] = args.description
    if args.location:
        event_body["location"] = args.location
    if args.attendees:
        event_body["attendees"] = [{"email": e} for e in args.attendees]

    # Determine if this is a "reminder" pattern
    is_reminder = args.duration_minutes == 30 and args.reminders == 0
    if is_reminder:
        event_body["transparency"] = "transparent"
    else:
        # Add Google Meet conference
        event_body["conferenceData"] = {
            "createRequest": {
                "requestId": f"{datetime.utcnow().timestamp()}-{_bot_name()}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    try:
        created = (
            svc.events()
            .insert(
                calendarId=args.calendar_id,
                body=event_body,
                conferenceDataVersion=1,
                sendUpdates="none",
            )
            .execute()
        )
        return {
            "success": True,
            "event_id": created["id"],
            "htmlLink": created.get("htmlLink"),
        }
    except HttpError as e:
        logger.error("Failed to create event: %s", e)
        parsed_error: dict[str, Any] = {}
        error_reason: str | None = None
        error_message: str = str(e)

        try:
            payload = json.loads(e.content.decode("utf-8")) if getattr(e, "content", None) else None
            if isinstance(payload, dict):
                parsed_error = payload
                error_reason = payload.get("error") or payload.get("error_description")
                if isinstance(error_reason, str):
                    error_reason = error_reason.strip()
                detail = payload.get("error_description") or payload.get("message")
                if isinstance(detail, str) and detail.strip():
                    error_message = detail.strip()
        except (ValueError, AttributeError):
            parsed_error = {}

        response: Dict[str, Any] = {
            "success": False,
            "error": error_message,
            "status_code": getattr(e, "status_code", None),
            "raw_error": parsed_error,
        }

        if error_reason == "unauthorized_client":
            sa_identity = _get_service_account_identity()
            client_email = sa_identity.get("client_email")
            client_id = sa_identity.get("client_id")
            response.update(
                {
                    "reason": "unauthorized_client",
                    "action_required": "authorize_calendar_scope",
                    "message": (
                        "Calendar access is not authorized for the configured service account. "
                        "An administrator must grant domain-wide delegation for the scope "
                        "https://www.googleapis.com/auth/calendar to the service account."
                    ),
                    "service_account_email": client_email,
                    "service_account_client_id": client_id,
                }
            )

        return response


@register_tool(
    name="ReadCalendarEvent",
    description="Read a calendar event's details.",
    param_model=ReadEventArgs,
)
def read_calendar_event(args: ReadEventArgs) -> Dict[str, Any]:
    try:
        svc = _get_calendar_service()
    except CalendarIntegrationDisabled as exc:
        logger.warning("Calendar integration disabled: %s", exc)
        return {
            "success": False,
            "disabled": True,
            "reason": "integration_disabled",
            "message": str(exc),
        }
    try:
        evt = svc.events().get(calendarId=args.calendar_id, eventId=args.event_id).execute()
        basic = {
            "success": True,
            "event_id": evt["id"],
            "title": _strip_suffix_from_title(evt.get("summary", "")),
            "start": evt["start"],
            "end": evt["end"],
            "location": evt.get("location"),
        }
        if args.detail_level == "basic":
            return basic
        # detailed
        basic.update(
            {
                "attendees": evt.get("attendees", []),
                "reminders": evt.get("reminders"),
                "conferenceData": evt.get("conferenceData"),
                "description": evt.get("description"),
            }
        )
        return basic
    except HttpError as e:
        return {"success": False, "error": str(e)}


@register_tool(
    name="SearchCalendarEvents",
    description="Search calendar events within a time window.",
    param_model=SearchEventsArgs,
)
def search_calendar_events(args: SearchEventsArgs) -> Dict[str, Any]:
    try:
        svc = _get_calendar_service()
    except CalendarIntegrationDisabled as exc:
        logger.warning("Calendar integration disabled: %s", exc)
        return {
            "success": False,
            "disabled": True,
            "reason": "integration_disabled",
            "message": str(exc),
        }
    cal_ids = [args.calendar_id]
    if args.calendar_id.lower() == "all":
        # fetch all calendars the user can see
        cal_ids = [
            c["id"]
            for c in svc.calendarList().list(minAccessRole="reader").execute().get("items", [])
        ]
    results: List[Dict[str, Any]] = []
    for cid in cal_ids:
        try:
            resp = (
                svc.events()
                .list(
                    calendarId=cid,
                    timeMin=_dt_to_rfc3339(
                        args.starting_after or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                    ),
                    timeMax=_dt_to_rfc3339(
                        args.starting_before
                        or (datetime.now(timezone.utc) + timedelta(days=30))
                    ),
                    q=args.query or None,
                    singleEvents=True,
                    orderBy="startTime",
                    showDeleted=False,
                    maxResults=20,
                )
                .execute()
            )
            for item in resp.get("items", []):
                if args.created_by_bot and not _is_bot_event(item):
                    continue
                results.append(
                    {
                        "id": item["id"],
                        "title": _strip_suffix_from_title(item.get("summary", "")),
                        "start": item.get("start"),
                        "end": item.get("end"),
                        "calendar": cid,
                        "created_by_bot": _is_bot_event(item),
                    }
                )
        except HttpError as e:
            logger.warning("Search failed for calendar %s: %s", cid, e)
            continue
    # Already ordered by API
    return {"success": True, "results": results}


@register_tool(
    name="UpdateCalendarEvent",
    description="Update a bot-created calendar event.",
    param_model=UpdateEventArgs,
    async_execution=True,
    category="output",
)
def update_calendar_event(args: UpdateEventArgs) -> Dict[str, Any]:
    try:
        svc = _get_calendar_service()
    except CalendarIntegrationDisabled as exc:
        logger.warning("Calendar integration disabled: %s", exc)
        return {
            "success": False,
            "disabled": True,
            "reason": "integration_disabled",
            "message": str(exc),
        }
    try:
        event = svc.events().get(calendarId=args.calendar_id, eventId=args.event_id).execute()
        if not _is_bot_event(event):
            return {
                "success": False,
                "error": f"{_bot_name()} did not create this meeting, so it cannot be modified",
            }
        # Apply updates
        if args.title is not None:
            event["summary"] = _append_suffix(args.title)
        if args.duration_minutes and args.start_time is not None:
            # both provided – respect start_time & duration
            end_dt = args.start_time + timedelta(minutes=args.duration_minutes)
            event["start"]["dateTime"] = _dt_to_rfc3339(args.start_time)
            event["end"]["dateTime"] = _dt_to_rfc3339(end_dt)
        elif args.start_time is not None:
            # shift start & end preserving original duration
            # RFC3339 times may include trailing 'Z' which datetime.fromisoformat
            # in Python <3.11 does not accept. Normalise 'Z' → '+00:00'.
            def _parse_rfc3339(s: str) -> datetime:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))

            orig_start = _parse_rfc3339(event["start"]["dateTime"])
            orig_end = _parse_rfc3339(event["end"]["dateTime"])
            delta = orig_end - orig_start
            new_end = args.start_time + delta
            event["start"]["dateTime"] = _dt_to_rfc3339(args.start_time)
            event["end"]["dateTime"] = _dt_to_rfc3339(new_end)
        elif args.duration_minutes is not None:
            # adjust duration preserving start
            def _parse_rfc3339(s: str) -> datetime:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            start_dt = _parse_rfc3339(event["start"]["dateTime"])
            event["end"]["dateTime"] = _dt_to_rfc3339(start_dt + timedelta(minutes=args.duration_minutes))
        if args.description is not None:
            event["description"] = args.description
        if args.location is not None:
            event["location"] = args.location
        if args.add_attendees:
            existing = {a["email"] for a in event.get("attendees", [])}
            for em in args.add_attendees:
                if em not in existing:
                    event.setdefault("attendees", []).append({"email": em})
        if args.remove_attendees and event.get("attendees"):
            event["attendees"] = [a for a in event["attendees"] if a["email"] not in args.remove_attendees]
        if args.reminders is not None:
            event["reminders"] = _build_reminders(args.reminders)

        updated = (
            svc.events()
            .update(calendarId=args.calendar_id, eventId=args.event_id, body=event, sendUpdates="none")
            .execute()
        )
        return {"success": True, "event_id": updated["id"], "htmlLink": updated.get("htmlLink")}
    except HttpError as e:
        return {"success": False, "error": str(e)}


@register_tool(
    name="DeleteCalendarEvent",
    description="Delete a bot-created calendar event.",
    param_model=DeleteEventArgs,
    async_execution=True,
    category="output",
)
def delete_calendar_event(args: DeleteEventArgs) -> Dict[str, Any]:
    try:
        svc = _get_calendar_service()
    except CalendarIntegrationDisabled as exc:
        logger.warning("Calendar integration disabled: %s", exc)
        return {
            "success": False,
            "disabled": True,
            "reason": "integration_disabled",
            "message": str(exc),
        }
    try:
        evt = svc.events().get(calendarId=args.calendar_id, eventId=args.event_id).execute()
        if not _is_bot_event(evt):
            return {
                "success": False,
                "error": f"{_bot_name()} did not create this meeting, so it cannot be modified",
            }
        svc.events().delete(
            calendarId=args.calendar_id,
            eventId=args.event_id,
            sendUpdates="none" if not args.send_notifications else "all",
        ).execute()
        return {"success": True}
    except HttpError as e:
        return {"success": False, "error": str(e)} 
