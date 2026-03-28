#!/usr/bin/env python3
"""Tests for Tavily tools."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import tool_framework as tf
from app.tools import tavily  # noqa: F401 – ensures registration


def test_search_posts_expected_payload() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"results": [{"url": "https://openai.com"}]}

    with (
        patch("app.settings.get_env", return_value=SimpleNamespace(tavily_api_key="test-key")),
        patch("app.tools.tavily.requests.post", return_value=response) as mock_post,
    ):
        result = tf.execute_tool("TavilySearch", {"query": "OpenAI", "max_results": 1})

    assert result == {"results": [{"url": "https://openai.com"}]}
    assert mock_post.call_count == 1
    assert mock_post.call_args.args[0] == "https://api.tavily.com/search"
    assert mock_post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
    }
    assert mock_post.call_args.kwargs["json"] == {
        "query": "OpenAI",
        "max_results": 1,
        "include_answer": False,
        "topic": "general",
        "search_depth": "advanced",
        "chunks_per_source": 1,
        "timeout": 60,
    }
    assert mock_post.call_args.kwargs["timeout"] == 60


def test_extract_posts_expected_payload() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"results": [{"url": "https://docs.python.org"}]}

    with (
        patch("app.settings.get_env", return_value=SimpleNamespace(tavily_api_key="test-key")),
        patch("app.tools.tavily.requests.post", return_value=response) as mock_post,
    ):
        result = tf.execute_tool("TavilyExtract", {"urls": "https://docs.python.org"})

    assert result == {"results": [{"url": "https://docs.python.org"}]}
    assert mock_post.call_count == 1
    assert mock_post.call_args.args[0] == "https://api.tavily.com/extract"
    assert mock_post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
    }
    assert mock_post.call_args.kwargs["json"] == {
        "urls": ["https://docs.python.org"],
        "extract_depth": "advanced",
        "format": "markdown",
        "timeout": 60,
    }
    assert mock_post.call_args.kwargs["timeout"] == 60
