import datetime
from unittest.mock import patch, MagicMock
import pytest
import time

from app.tools import google_calendar
from app import tool_framework as tf

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


from app.tool_framework import get_async_result  # after TF import for type clarity


def _mock_service():
    """Return a Google Calendar service mock with nested mocks for events()."""

    service = MagicMock()
    events = service.events.return_value

    # insert
    insert_call = events.insert.return_value
    insert_call.execute.return_value = {
        "id": "evt_123",
        "htmlLink": "https://cal/google/evt_123",
        "summary": "Test Event [Ringdown]",
    }

    # get
    get_call = events.get.return_value
    get_call.execute.return_value = {
        "id": "evt_123",
        "summary": "Test Event [Ringdown]",
        "start": {"dateTime": "2024-01-01T10:00:00Z"},
        "end": {"dateTime": "2024-01-01T11:00:00Z"},
        "location": "Somewhere",
    }

    # list
    list_call = events.list.return_value
    list_call.execute.return_value = {
        "items": [
            {
                "id": "evt_123",
                "summary": "Test Event [Ringdown]",
                "start": {"dateTime": "2024-01-01T10:00:00Z"},
                "end": {"dateTime": "2024-01-01T11:00:00Z"},
            },
            {
                "id": "evt_456",
                "summary": "External Meeting",
                "start": {"dateTime": "2024-01-02T12:00:00Z"},
                "end": {"dateTime": "2024-01-02T13:00:00Z"},
            },
        ]
    }

    # update
    update_call = events.update.return_value
    update_call.execute.return_value = {
        "id": "evt_123",
        "htmlLink": "https://cal/google/evt_123",
    }

    # delete no return
    events.delete.return_value.execute.return_value = {}

    return service

# ---------------------------------------------------------------------------
# Helpers for async-tool tests
# ---------------------------------------------------------------------------


def _wait_for_async(async_id: str, timeout: float = 1.0):
    """Poll ``get_async_result`` until the background thread stores a result.

    Raises TimeoutError if *timeout* seconds elapse without a result.
    The calendar mocks return almost instantly, so 1 s is generous.
    """

    start = time.perf_counter()
    while True:
        result = get_async_result(async_id)
        if result is not None:
            return result
        if time.perf_counter() - start > timeout:
            raise TimeoutError(f"Async result {async_id} not ready in {timeout}s")
        time.sleep(0.01)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_calendar_tools_registered():
    assert "CreateCalendarEvent" in tf.TOOL_REGISTRY
    assert "ReadCalendarEvent" in tf.TOOL_REGISTRY
    assert "SearchCalendarEvents" in tf.TOOL_REGISTRY
    assert "UpdateCalendarEvent" in tf.TOOL_REGISTRY
    assert "DeleteCalendarEvent" in tf.TOOL_REGISTRY


@patch("app.tools.google_calendar._get_calendar_service")
def test_create_event_normal(mock_get_service):
    mock_get_service.return_value = _mock_service()

    # set agent context with bot name
    google_calendar.set_agent_context({"bot_name": "Ringdown"})

    start = datetime.datetime(2025, 1, 1, 15, 0, 0, tzinfo=datetime.timezone.utc).isoformat()

    pending = tf.execute_tool(
        "CreateCalendarEvent",
        {
            "title": "Project Kickoff",
            "start_time": start,
            "duration_minutes": 60,
        },
    )
    # Immediate placeholder
    assert pending["success"] is True and pending["async_execution"] is True

    result = _wait_for_async(pending["async_id"])

    assert result["success"] is True
    assert result["event_id"] == "evt_123"

    # body passed to insert contains suffix
    service = mock_get_service.return_value
    body_used = service.events.return_value.insert.call_args.kwargs["body"]
    assert body_used["summary"].endswith(" [Ringdown]")

    google_calendar.set_agent_context(None)


@patch("app.tools.google_calendar._get_calendar_service")
def test_search_created_by_bot(mock_get_service):
    mock_get_service.return_value = _mock_service()
    google_calendar.set_agent_context({"bot_name": "Ringdown"})

    result = tf.execute_tool(
        "SearchCalendarEvents",
        {
            "calendar_id": "primary",
            "created_by_bot": True,
        },
    )
    assert result["success"] is True
    assert len(result["results"]) == 1  # only Ringdown-tagged event
    assert result["results"][0]["id"] == "evt_123"

    google_calendar.set_agent_context(None)


@patch("app.tools.google_calendar._get_calendar_service")
def test_update_event_not_bot(mock_get_service):
    svc = _mock_service()
    # make the get call return non-bot event
    svc.events.return_value.get.return_value.execute.return_value["summary"] = "External Meeting"
    mock_get_service.return_value = svc
    google_calendar.set_agent_context({"bot_name": "Ringdown"})

    pending = tf.execute_tool(
        "UpdateCalendarEvent",
        {
            "event_id": "evt_456",
            "title": "New Title",
        },
    )
    assert pending["success"] is True and pending["async_execution"] is True
    res = _wait_for_async(pending["async_id"])
    assert res["success"] is False
    assert "did not create" in res["error"].lower()
    google_calendar.set_agent_context(None)


@patch("app.tools.google_calendar._get_calendar_service")
def test_delete_event_bot(mock_get_service):
    mock_get_service.return_value = _mock_service()
    google_calendar.set_agent_context({"bot_name": "Ringdown"})

    pending = tf.execute_tool(
        "DeleteCalendarEvent",
        {
            "event_id": "evt_123",
        },
    )
    assert pending["success"] is True and pending["async_execution"] is True

    res = _wait_for_async(pending["async_id"])
    assert res["success"] is True

    # ensure delete called
    service = mock_get_service.return_value
    service.events.return_value.delete.assert_called()
    google_calendar.set_agent_context(None) 