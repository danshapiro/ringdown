from __future__ import annotations

from typing import Any, Dict

import pytest

from tests.live import managed_session_helper as helper


class DummyResponse:
    def __init__(self, status_code: int, payload: Dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if 400 <= self.status_code:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_fetch_active_session_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(*_: Any, **__: Any) -> DummyResponse:
        return DummyResponse(404, {})

    monkeypatch.setattr(helper.requests, "get", fake_get)
    session = helper.fetch_active_session("https://example.test", "device-1", "token")
    assert session is None


def test_ensure_active_session_reuses_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"sessionId": "session-1", "metadata": {"control": {"key": "abc"}}}

    def fake_get(*_: Any, **__: Any) -> DummyResponse:
        return DummyResponse(200, payload)

    def fake_post(*_: Any, **__: Any) -> None:
        raise AssertionError("create_session should not be called when reuse succeeds")

    monkeypatch.setattr(helper.requests, "get", fake_get)
    monkeypatch.setattr(helper.requests, "post", fake_post)

    session = helper.ensure_active_session("https://example.test", "device-1", "token")
    assert session["sessionId"] == "session-1"
    assert session["metadata"]["control"]["key"] == "abc"


def test_ensure_active_session_creates_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: Dict[str, int] = {"get": 0, "post": 0}

    def fake_get(*_: Any, **__: Any) -> DummyResponse:
        calls["get"] += 1
        return DummyResponse(404, {})

    def fake_post(*_: Any, **__: Any) -> DummyResponse:
        calls["post"] += 1
        return DummyResponse(200, {"sessionId": "session-created", "metadata": {"control": {"key": "def"}}})

    monkeypatch.setattr(helper.requests, "get", fake_get)
    monkeypatch.setattr(helper.requests, "post", fake_post)

    session = helper.ensure_active_session("https://example.test", "device-1", "token")
    assert session["sessionId"] == "session-created"
    assert calls["get"] == 1
    assert calls["post"] == 1


def test_ensure_active_session_raises_without_control_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(*_: Any, **__: Any) -> DummyResponse:
        return DummyResponse(404, {})

    def fake_post(*_: Any, **__: Any) -> DummyResponse:
        return DummyResponse(200, {"sessionId": "no-control", "metadata": {}})

    monkeypatch.setattr(helper.requests, "get", fake_get)
    monkeypatch.setattr(helper.requests, "post", fake_post)

    with pytest.raises(RuntimeError):
        helper.ensure_active_session(
            "https://example.test",
            "device-1",
            "token",
            retries=0,
        )
