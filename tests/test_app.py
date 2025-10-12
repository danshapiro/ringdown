#!/usr/bin/env python3
"""Comprehensive integration tests for the Ringdown service.

These tests spin up the real FastAPI application (no monkeypatching of
external calls) and verify:

1. Agent selection logic & TwiML generation
2. Fallback to `unknown-caller`
3. Live pricing fetch via LiteLLM price sheet
4. Cost calculation math

We do *not* hit the WebSocket endpoint because that requires Twilio
signatures; those are covered by runtime logs / manual tests.
"""

import contextlib
import logging

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app import pricing
from app import settings as app_settings
from app.api import twilio as twilio_api
from app.pricing import logger as pricing_logger
from app.settings import _load_config  # local import to avoid circular

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

# Signature is validated later – we hit fallback path.
auth_header = {"X-Twilio-Signature": "dummy"}

# Patch Twilio signature validator so we can focus on application logic.
@contextlib.contextmanager
def _twilio_ok():
    original = twilio_api._twilio_validator.validate
    twilio_api._twilio_validator.validate = lambda url, params, sig: True  # type: ignore[assignment]
    try:
        yield
    finally:
        twilio_api._twilio_validator.validate = original


client = TestClient(app_main.app)

_CONFIG = _load_config()
_AGENT_MAP = _CONFIG["agents"]

_PRIMARY_AGENT_NAME = next(
    (name for name, cfg in _AGENT_MAP.items() if (cfg.get("phone_numbers") or [])),
    None,
)
assert (
    _PRIMARY_AGENT_NAME is not None
), "Test configuration must define at least one agent with a phone number"

_PRIMARY_AGENT_CFG = app_settings.get_agent_config(_PRIMARY_AGENT_NAME)
PRIMARY_NUMBER = _PRIMARY_AGENT_CFG["phone_numbers"][0]
PRIMARY_GREETING = _PRIMARY_AGENT_CFG.get("welcome_greeting", "Hello!")
DEFAULT_VOICE = _CONFIG.get("defaults", {}).get("voice")

UNKNOWN_NUM = "+19999990000"

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_twilml_agent_selection():
    """Calling /twiml with a known number should embed the agent greeting and voice."""

    with _twilio_ok():
        resp = client.get("/twiml", headers=auth_header, params={"From": PRIMARY_NUMBER})
    assert resp.status_code == 200
    xml = resp.text

    # Check that the agent greeting appears
    assert PRIMARY_GREETING.split(".")[0] in xml
    # Voice tag from defaults in config.yaml
    if DEFAULT_VOICE:
        assert DEFAULT_VOICE in xml


def test_twilml_unknown_caller():
    """Unknown caller falls back to unknown-caller agent."""

    with _twilio_ok():
        resp = client.get("/twiml", headers=auth_header, params={"From": UNKNOWN_NUM})
    assert resp.status_code == 200
    xml = resp.text

    unknown_cfg = app_settings.get_agent_config("unknown-caller")
    assert unknown_cfg["welcome_greeting"] in xml


def test_live_pricing_fetch():
    """get_token_prices returns numeric costs (may be zero if provider lacks data)."""

    in_c, out_c = pricing.get_token_prices("gpt-3.5-turbo")
    assert isinstance(in_c, float)
    assert isinstance(out_c, float)
    assert in_c >= 0
    assert out_c >= 0


def test_cost_math():
    """Cost calculation matches manual computation."""

    model = "gpt-3.5-turbo"
    prompt_t, completion_t = 100, 200
    in_c, out_c = pricing.get_token_prices(model)

    expected = in_c * prompt_t + out_c * completion_t

    calc = pricing.calculate_llm_cost(model, prompt_t, completion_t)
    assert abs(calc - expected) < 1e-9


def test_zero_cost_models_do_not_trigger_fallback(monkeypatch):
    """Models legitimately reporting 0 cost should not pick fallback prices."""

    def fake_cost_per_token(*_args, **_kwargs):
        return {
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
        }

    monkeypatch.setattr(pricing.litellm, "cost_per_token", fake_cost_per_token)

    in_c, out_c = pricing.get_token_prices("gpt-3.5-turbo")

    assert in_c == 0.0
    assert out_c == 0.0


def test_incomplete_pricing_response_logs_and_defaults(monkeypatch):
    """Partial LiteLLM responses log errors but fall back to zeroed fields."""

    def missing_output_cost(*_args, **_kwargs):
        return {
            "input_cost_per_token": 0.0001,
            "output_cost_per_token": None,
        }

    monkeypatch.setattr(pricing.litellm, "cost_per_token", missing_output_cost)

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):  # type: ignore[override]
            records.append(record)

    handler = _Capture(level=logging.ERROR)
    pricing_logger.addHandler(handler)
    try:
        in_c, out_c = pricing.get_token_prices("gpt-3.5-turbo")
    finally:
        pricing_logger.removeHandler(handler)

    assert in_c == pytest.approx(0.0001)
    assert out_c == 0.0
    assert any("missing output_cost_per_token" in record.getMessage() for record in records)


def test_missing_api_keys_raise(monkeypatch):
    """get_env should fail noisily if required API keys are absent."""

    from app import settings as app_settings

    app_settings.get_env.cache_clear()

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)

    original_env_file = app_settings.EnvSettings.model_config.get("env_file")
    monkeypatch.setitem(app_settings.EnvSettings.model_config, "env_file", None)

    with pytest.raises(RuntimeError):
        app_settings.get_env()

    app_settings.get_env.cache_clear()
    if original_env_file is not None:
        app_settings.EnvSettings.model_config["env_file"] = original_env_file


def test_max_history_config():
    """Test that max_history is properly configured in config.yaml."""
    
    from app.settings import get_agent_config
    
    defaults = _CONFIG.get("defaults", {})
    default_max_history = defaults.get("max_history")
    assert default_max_history >= 1

    for agent_name in _AGENT_MAP:
        agent_config = get_agent_config(agent_name)
        agent_max_history = agent_config.get("max_history")
        assert agent_max_history >= default_max_history
