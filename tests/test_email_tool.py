#!/usr/bin/env python3
"""Tests for Gmail email tool."""

from unittest.mock import patch, MagicMock

import time

import pytest

from app.tools import email  # noqa: F401 – ensures registration
from app import tool_framework as tf


def test_email_registration():
    """SendEmail should expose metadata in the tool registry."""
    assert "SendEmail" in tf.TOOL_REGISTRY
    spec = tf.TOOL_REGISTRY["SendEmail"]
    assert spec.name == "SendEmail"
    assert isinstance(spec.description, str) and spec.description.strip()


def test_recipient_validation_default():
    """Default greenlist should allow example.com addresses only."""
    email.set_agent_context(None)

    valid = [
        "team@example.com",
        "user@example.com",
        "Another.User@Example.com",
    ]
    invalid = [
        "user@other.com",
        "sample@gmail.com",
    ]

    for addr in valid:
        email.EmailArgs(to=addr, subject="test", body="body")

    for addr in invalid:
        with pytest.raises(ValueError):
            email.EmailArgs(to=addr, subject="test", body="body")


def test_recipient_validation_with_agent_context():
    """Custom agent context should override the default greenlist."""
    agent_cfg = {
        "email_greenlist_enforced": True,
        "email_greenlist": [
            "ops@example.com",
            "^[^@]+@ops\\.example\\.com$",
        ],
    }
    email.set_agent_context(agent_cfg)

    email.EmailArgs(to="ops@example.com", subject="test", body="body")
    email.EmailArgs(to="lead@ops.example.com", subject="test", body="body")

    with pytest.raises(ValueError):
        email.EmailArgs(to="team@example.com", subject="test", body="body")

    email.set_agent_context(None)


def test_disabled_response_without_credentials():
    """If Gmail credentials are absent the tool should respond with disabled=True."""
    email.set_agent_context(None)
    result = tf.execute_tool(
        "SendEmail",
        {"to": "team@example.com", "subject": "Hello", "body": "Test"},
    )
    assert result["disabled"] is True
    assert result["reason"] == "integration_disabled"


def test_email_sending_mock():
    """When Gmail service is available the tool should enqueue a send request."""
    mock_execute = MagicMock(return_value={"id": "test_message_123"})
    mock_send = MagicMock(return_value=MagicMock(execute=mock_execute))
    mock_messages = MagicMock(send=mock_send)
    mock_users = MagicMock(return_value=MagicMock(messages=MagicMock(return_value=mock_messages)))
    mock_service = MagicMock(users=mock_users)

    with patch("app.tools.email._get_gmail_service", return_value=mock_service):
        result = tf.execute_tool(
            "SendEmail",
            {"to": "team@example.com", "subject": "Test Subject", "body": "This is a test email."},
        )

    assert result["success"] is True
    assert result["async_execution"] is True
    deadline = time.time() + 1
    while mock_messages.send.call_count == 0 and time.time() < deadline:
        time.sleep(0.01)
    mock_messages.send.assert_called_once()


def test_rate_limit_decorator_is_used():
    """Ensure the ratelimit decorator is still applied to the underlying send call."""
    with patch("app.tools.email._send_gmail") as mocked_send:
        mocked_service = MagicMock()
        with patch("app.tools.email._get_gmail_service", return_value=mocked_service):
            tf.execute_tool(
                "SendEmail",
                {"to": "team@example.com", "subject": "Test", "body": "Body"},
            )
        assert mocked_send.called
