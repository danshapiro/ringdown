#!/usr/bin/env python3
"""Tests for Tavily tools (search & extract). Requires TAVILY_API_KEY env."""

import os
import pytest

pytestmark = pytest.mark.integration

from app.tools import tavily  # noqa: F401 – ensures registration
from app import tool_framework as tf


HAS_KEY = bool(os.getenv("TAVILY_API_KEY"))

def _skip_if_no_key():
    if not HAS_KEY:
        pytest.skip("TAVILY_API_KEY not set – skipping live Tavily tests", allow_module_level=True)


_skip_if_no_key()


def test_search_live():
    args = {"query": "OpenAI", "max_results": 1}
    res = tf.execute_tool("TavilySearch", args)
    assert "results" in res and res["results"], res


def test_extract_live():
    # First get a url
    search = tf.execute_tool("TavilySearch", {"query": "Python programming", "max_results": 1})
    url = search["results"][0]["url"]
    res = tf.execute_tool("TavilyExtract", {"urls": url})
    assert "results" in res
    assert res["results"], res 