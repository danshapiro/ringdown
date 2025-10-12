#!/usr/bin/env python3
"""Tests for automatic discovery of tool modules.

These tests verify that merely importing *app.tool_framework* triggers
import of all modules in *app.tools* so that their calls to
``register_tool`` populate **TOOL_REGISTRY**.
"""

from __future__ import annotations

import importlib

from app import tool_framework as tf
from app import settings


def test_auto_import_registry_contains_tools():
    """'TavilySearch', 'TavilyExtract', and 'SendEmail' should be registered automatically."""

    names = tf.list_tools()
    assert "TavilySearch" in names
    assert "TavilyExtract" in names
    assert "SendEmail" in names


def test_get_tools_for_unknown_caller():
    """get_tools_for_agent resolves correct schemas using defaults & overrides."""

    agent_cfg = settings.get_agent_config("unknown-caller")
    schemas = tf.get_tools_for_agent(agent_cfg)
    fn_names = {s["function"]["name"] for s in schemas}
    assert fn_names == {"TavilySearch"}  # override should limit to single tool


def test_get_tools_for_demo_agent():
    """Agents should have exactly the tools specified in their config."""

    agent_cfg = settings.get_agent_config("ringdown-demo")
    schemas = tf.get_tools_for_agent(agent_cfg)
    fn_names = {s["function"]["name"] for s in schemas}
    
    # Get expected tools from the actual agent configuration
    expected_tools = set(agent_cfg.get("tools", []))
    
    # The function names should match the tool names from config exactly
    assert fn_names == expected_tools, f"Tool mismatch - Expected: {expected_tools}, Got: {fn_names}" 