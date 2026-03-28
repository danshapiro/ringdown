#!/usr/bin/env python3
"""End-to-end tests for Tavily tools using a local HTTP server."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import pytest

from app import tool_framework as tf
from app.tools import tavily  # noqa: F401 - ensures registration

pytestmark = pytest.mark.integration


class _ServerState:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[str, dict[str, Any]] = {}


class _TavilyHandler(BaseHTTPRequestHandler):
    server: _TavilyHttpServer

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        body = json.loads(raw_body)
        self.server.state.requests.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "json": body,
            },
        )

        response = self.server.state.responses[self.path]
        payload = response["json"]
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(response.get("status", 200))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


class _TavilyHttpServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, _TavilyHandler)
        self.state = _ServerState()


@pytest.fixture
def tavily_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[_TavilyHttpServer]:
    server = _TavilyHttpServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setattr(
        tavily,
        "_BASE_URL",
        f"http://127.0.0.1:{server.server_address[1]}",
    )
    monkeypatch.setattr(
        "app.settings.get_env",
        lambda: SimpleNamespace(tavily_api_key="test-key"),
    )

    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_search_end_to_end_posts_expected_payload(tavily_server: _TavilyHttpServer) -> None:
    tavily_server.state.responses["/search"] = {
        "json": {"results": [{"url": "https://openai.com"}]},
    }

    result = tf.execute_tool("TavilySearch", {"query": "OpenAI", "max_results": 1})

    assert result == {"results": [{"url": "https://openai.com"}]}
    assert len(tavily_server.state.requests) == 1
    request = tavily_server.state.requests[0]
    assert request["path"] == "/search"
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["headers"]["Content-Type"] == "application/json"
    assert request["json"] == {
        "query": "OpenAI",
        "max_results": 1,
        "include_answer": False,
        "topic": "general",
        "search_depth": "advanced",
        "chunks_per_source": 1,
        "timeout": 60,
    }


def test_extract_end_to_end_posts_expected_payload(tavily_server: _TavilyHttpServer) -> None:
    tavily_server.state.responses["/extract"] = {
        "json": {"results": [{"url": "https://docs.python.org"}]},
    }

    result = tf.execute_tool("TavilyExtract", {"urls": "https://docs.python.org"})

    assert result == {"results": [{"url": "https://docs.python.org"}]}
    assert len(tavily_server.state.requests) == 1
    request = tavily_server.state.requests[0]
    assert request["path"] == "/extract"
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["headers"]["Content-Type"] == "application/json"
    assert request["json"] == {
        "urls": ["https://docs.python.org"],
        "extract_depth": "advanced",
        "format": "markdown",
        "timeout": 60,
    }


def test_search_surfaces_real_http_errors(tavily_server: _TavilyHttpServer) -> None:
    tavily_server.state.responses["/search"] = {
        "status": 401,
        "json": {"error": "unauthorized"},
    }

    with pytest.raises(RuntimeError, match="TavilySearch HTTP 401"):
        tf.execute_tool("TavilySearch", {"query": "OpenAI"})
