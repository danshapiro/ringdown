#!/usr/bin/env python3
"""Per-agent *greenlist* tests for the SendEmail tool.

These tests verify that different agents use the correct recipient greenlist
(email_greenlist) when validating addresses.  We intentionally use the term
"greenlist" in comments & docs to avoid greenlist/greenlist terminology.
"""

import pytest
from app import settings
from app.tools import email

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _validate(addr: str) -> None:
    """Helper to run the EmailArgs validator only (no network calls)."""
    email.EmailArgs(to=addr, subject="test", body="body")

# ---------------------------------------------------------------------------
# Fixtures clean up agent context to avoid cross-test leakage
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_agent_context():
    """Reset thread-local agent context before & after each test."""
    email.set_agent_context(None)
    yield
    email.set_agent_context(None)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_greenlist_for_ringdown_demo():
    """ringdown-demo agent should respect its configured recipients."""
    agent_cfg = settings.get_agent_config("ringdown-demo")
    email.set_agent_context(agent_cfg)

    # Allowed addresses (present in the agent greenlist)
    allowed = [
        "team@example.com",
        "user@example.com",
        "Another.User@Example.com",
    ]

    # Blocked addresses (NOT in the agent greenlist)
    blocked = [
        "test@gmail.com",
        "other@sample.org",
    ]

    for addr in allowed:
        _validate(addr)  # should NOT raise

    for addr in blocked:
        with pytest.raises(ValueError):
            _validate(addr)


def test_greenlist_fallback_for_unknown_caller():
    """unknown-caller agent uses the default greenlist."""
    agent_cfg = settings.get_agent_config("unknown-caller")
    email.set_agent_context(agent_cfg)

    allowed = [
        "team@example.com",
        "person@example.com",
    ]
    blocked = [
        "foo@sample.com",  # not in default greenlist
        "random@gmail.com",
    ]

    for addr in allowed:
        _validate(addr)

    for addr in blocked:
        with pytest.raises(ValueError):
            _validate(addr) 