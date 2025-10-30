from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket, status
from fastapi.testclient import TestClient
import yaml

from app import settings
from app.api import websocket as websocket_api
from app.api.websocket import websocket_endpoint
from app.call_state import pop_call
from app.main import app
from app.mobile import realtime as realtime_module


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twilio-test")


@pytest.fixture(autouse=True)
def _reset_realtime_store() -> None:
    store = realtime_module.get_realtime_store()
    store.clear()


@pytest.fixture
def isolated_mobile_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    project_root = Path(__file__).resolve().parents[1]
    source = project_root / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    devices = data.setdefault("mobile_devices", {})
    devices["device-xyz"] = {
        "label": "Device XYZ",
        "agent": "unknown-caller",
        "enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    defaults = data.setdefault("defaults", {})
    defaults.setdefault("realtime", {
        "model": "gpt-rt-default",
        "voice": "verse",
        "server_vad": {
            "activation_threshold": 0.55,
            "silence_duration_ms": 420,
        },
    })
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    monkeypatch.setenv("RINGDOWN_CONFIG_PATH", str(config_path))
    settings.refresh_config_cache()
    try:
        yield "device-xyz"
    finally:
        settings.refresh_config_cache()


class _StubResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return self._payload


class _StubAsyncClient:
    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self.requests: List[Tuple[str, Dict[str, Any], Dict[str, str]]] = []
        self._responses = responses

    async def __aenter__(self) -> "_StubAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001, D401
        return None

    async def post(
        self,
        url: str,
        *,
        json: Dict[str, Any],
        headers: Dict[str, str],
        timeout: float | None = None,  # noqa: ARG002
    ) -> _StubResponse:
        self.requests.append((url, json, headers))
        payload = self._responses.pop(0) if self._responses else {}
        return _StubResponse(payload)


def _client_factory(responses: List[Dict[str, Any]]) -> Callable[[], _StubAsyncClient]:
    def _factory() -> _StubAsyncClient:
        return _StubAsyncClient(responses.copy())

    return _factory


@pytest.mark.asyncio
async def test_send_assistant_audio_posts_expected_events() -> None:
    now = datetime.now(timezone.utc)
    session = realtime_module.RealtimeSession(
        session_id="sess-123",
        client_secret="rt-client-secret",
        websocket_url="wss://api.openai.com/v1/realtime?model=gpt-test&session=sess-123",
        expires_at=now + timedelta(minutes=5),
        model="gpt-test",
        voice="verse",
    )

    responses = [{"ok": True}, {"ok": True}]
    client = _StubAsyncClient(responses)

    await realtime_module.send_assistant_audio(
        session,
        "Hello world",
        "verse",
        client_factory=lambda: client,
    )

    assert len(client.requests) == 2

    first_url, first_payload, first_headers = client.requests[0]
    assert first_url.endswith("/sess-123/events")
    assert first_headers["Authorization"] == "Bearer rt-client-secret"
    assert first_payload["type"] == "conversation.item.create"
    content = first_payload["item"]["content"][0]
    assert content["type"] == "input_text"
    assert content["text"] == "Hello world"

    second_url, second_payload, _second_headers = client.requests[1]
    assert second_url == first_url
    assert second_payload["type"] == "response.create"
    response_payload = second_payload["response"]
    assert response_payload["modalities"] == ["audio"]
    assert response_payload["voice"] == "verse"
    assert response_payload["instructions"] == "Hello world"


def test_mobile_realtime_session_endpoint_returns_session_payload(
    isolated_mobile_config: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = realtime_module.RealtimeSession(
        session_id="sess-endpoint",
        client_secret="client-secret",
        websocket_url="wss://api.openai.com/v1/realtime?model=gpt-test&session=sess-endpoint",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        model="gpt-test",
        voice="verse",
        device_id=isolated_mobile_config,
        agent_name="unknown-caller",
    )

    async def _fake_create(**kwargs):
        return session

    monkeypatch.setattr(realtime_module, "create_realtime_session", _fake_create)
    monkeypatch.setattr("app.api.mobile.create_realtime_session", _fake_create)

    client = TestClient(app)
    response = client.post(
        "/v1/mobile/realtime/session",
        json={"deviceId": isolated_mobile_config},
    )

    assert response.status_code == 200
    body = response.json()
    session_id = body.get("sessionId") or body.get("session_id")
    assert session_id == "sess-endpoint"
    client_secret = body.get("clientSecret") or body.get("client_secret")
    assert client_secret == "client-secret"
    websocket_url = body.get("websocketUrl") or body.get("websocket_url")
    assert websocket_url.endswith("sess-endpoint")
    assert (body.get("voice") or body.get("voice")) == "verse"
    assert (body.get("model") or body.get("model")) == "gpt-test"
    call_sid = body.get("callSid") or body.get("call_sid")
    assert call_sid
    websocket_token_value = body.get("websocketToken") or body.get("websocket_token")
    assert websocket_token_value
    websocket_token = websocket_token_value
    assert websocket_token
    server_vad = body.get("serverVad") or body.get("server_vad")
    assert server_vad is not None
    assert server_vad["activation_threshold"] == pytest.approx(0.55, rel=1e-3)
    assert server_vad["silence_duration_ms"] == 420

    stored = realtime_module.get_realtime_store().get_session("sess-endpoint")
    assert stored is session
    assert stored.call_id == body["callSid"]
    assert stored.mobile_token == websocket_token
    assert stored.metadata.get("server_vad", {}).get("silence_duration_ms") == 420

    call = pop_call(body["callSid"])
    assert call is not None
    assert call[-1]["realtime_session_id"] == "sess-endpoint"
    assert call[-1]["mobile_token"] == websocket_token
    assert call[-1]["transport"] == "android-realtime"


@pytest.mark.asyncio
async def test_create_realtime_session_uses_openai_api(monkeypatch: pytest.MonkeyPatch) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    response_payload = {
        "id": "sess-abc",
        "client_secret": {"value": "client-secret-123", "expires_at": expires_at.timestamp()},
        "urls": {"realtime": "wss://api.openai.com/v1/realtime?model=gpt-demo&session=sess-abc"},
        "expires_at": expires_at.timestamp(),
    }

    factory = _client_factory([response_payload])
    session = await realtime_module.create_realtime_session(
        agent_name="Danbot Agent",
        model="gpt-demo",
        voice="verse",
        device_id="device-123",
        client_factory=factory,
    )

    assert session.session_id == "sess-abc"
    assert session.client_secret == "client-secret-123"
    assert session.websocket_url.endswith("sess-abc")
    assert session.expires_at == expires_at.replace(microsecond=0)


@pytest.mark.asyncio
async def test_websocket_mirrors_assistant_text_to_realtime(monkeypatch: pytest.MonkeyPatch) -> None:
    session = realtime_module.RealtimeSession(
        session_id="sess-android",
        client_secret="android-secret",
        websocket_url="wss://api.openai.com/v1/realtime?model=gpt-android&session=sess-android",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        model="gpt-android",
        voice="verse",
    )

    store = realtime_module.get_realtime_store()
    session.call_id = "call-123"
    store.upsert(session)

    token = "mobile-token-123"

    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.client = ("127.0.0.1", 12345)
    mock_ws.headers = {"x-ringdown-mobile-token": token}
    mock_ws.scope = {}

    messages = [
        '{"type": "setup", "callSid": "call-123"}',
        '{"type": "prompt", "voicePrompt": "How are you?"}',
    ]

    async def _iter_text():
        for payload in messages:
            yield payload

    mock_ws.iter_text = _iter_text

    monkeypatch.setattr(websocket_api, "is_from_twilio", lambda ws: False)
    monkeypatch.setattr(websocket_api, "agent_is_active", lambda agent: False)
    monkeypatch.setattr(websocket_api, "mark_agent_active", lambda agent: None)
    monkeypatch.setattr(websocket_api, "release_agent", lambda agent: None)
    logged_turns: List[Tuple[str, str, str | None]] = []

    def _log_turn(role: str, text: str, *, source: str | None = None) -> None:
        logged_turns.append((role, text, source))

    monkeypatch.setattr(websocket_api, "log_turn", _log_turn)
    monkeypatch.setattr(websocket_api, "save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(websocket_api, "delete_state", lambda *args, **kwargs: None)

    async def _run_in_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(websocket_api, "run_in_threadpool", _run_in_threadpool)
    monkeypatch.setattr(websocket_api.litellm, "token_counter", lambda **kwargs: 1)

    class _MetricStub:
        def labels(self, **kwargs):
            return self

        def inc(self):
            return None

    monkeypatch.setattr(websocket_api, "METRIC_MESSAGES", _MetricStub())

    agent_cfg = {
        "prompt": "System prompt",
        "model": "gpt-android",
        "voice": "verse",
        "tts_provider": "",
        "max_disconnect_seconds": 60,
    }
    extras = {
        "realtime_session_id": session.session_id,
        "mobile_token": token,
        "transport": "android-realtime",
    }
    monkeypatch.setattr(
        websocket_api,
        "pop_call",
        lambda call_sid: ("Danbot Agent", agent_cfg.copy(), None, False, "android-device", extras),
    )

    async def _fake_stream_response(*args, **kwargs):
        yield "Assistant"
        yield " response"

    monkeypatch.setattr(websocket_api, "stream_response", lambda *args, **kwargs: _fake_stream_response())

    mirrored: List[Tuple[str, str, str]] = []

    async def _fake_send_audio(session_obj, text: str, voice: str, **_kwargs):
        mirrored.append((session_obj.session_id, text, voice))

    monkeypatch.setattr(realtime_module, "send_assistant_audio", _fake_send_audio)

    await websocket_endpoint(mock_ws)

    assert mirrored == [("sess-android", "Assistant response", "verse")]
    assert ("user", "How are you?", "android-realtime") in logged_turns


@pytest.mark.asyncio
async def test_websocket_rejects_mismatched_mobile_token(monkeypatch: pytest.MonkeyPatch) -> None:
    session = realtime_module.RealtimeSession(
        session_id="sess-mobile",
        client_secret="secret-mobile",
        websocket_url="wss://api.openai.com/v1/realtime?model=gpt-android&session=sess-mobile",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        model="gpt-android",
        voice="verse",
    )

    store = realtime_module.get_realtime_store()
    session.call_id = "call-xyz"
    store.upsert(session)

    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.client = ("127.0.0.1", 54321)
    mock_ws.headers = {"x-ringdown-mobile-token": "bad-token"}
    mock_ws.scope = {}
    mock_ws.accept = AsyncMock()
    mock_ws.send_json = AsyncMock()
    mock_ws.close = AsyncMock()

    async def _iter_text():
        yield '{"type": "setup", "callSid": "call-xyz"}'

    mock_ws.iter_text = _iter_text

    monkeypatch.setattr(websocket_api, "is_from_twilio", lambda ws: False)
    monkeypatch.setattr(websocket_api, "agent_is_active", lambda agent: False)
    monkeypatch.setattr(websocket_api, "mark_agent_active", lambda agent: None)
    monkeypatch.setattr(websocket_api, "release_agent", lambda agent: None)
    monkeypatch.setattr(websocket_api, "log_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(websocket_api, "save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(websocket_api, "delete_state", lambda *args, **kwargs: None)

    async def _run(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(websocket_api, "run_in_threadpool", _run)
    monkeypatch.setattr(websocket_api.litellm, "token_counter", lambda **kwargs: 1)

    class _MetricStub:
        def labels(self, **kwargs):
            return self

        def inc(self):
            return None

    monkeypatch.setattr(websocket_api, "METRIC_MESSAGES", _MetricStub())

    agent_cfg = {
        "prompt": "System prompt",
        "model": "gpt-android",
        "voice": "verse",
        "tts_provider": "",
        "max_disconnect_seconds": 60,
    }
    extras = {
        "realtime_session_id": session.session_id,
        "mobile_token": "expected-token",
        "transport": "android-realtime",
    }
    monkeypatch.setattr(
        websocket_api,
        "pop_call",
        lambda call_sid: ("Danbot Agent", agent_cfg.copy(), None, False, "android-device", extras),
    )

    async def _no_send(*args, **kwargs):
        raise AssertionError("send_assistant_audio should not be called")

    monkeypatch.setattr(realtime_module, "send_assistant_audio", _no_send)

    await websocket_endpoint(mock_ws)

    mock_ws.close.assert_called()
    assert mock_ws.close.call_args.kwargs.get("code") == status.WS_1008_POLICY_VIOLATION


@pytest.mark.asyncio
async def test_realtime_session_response_includes_camelcase_server_vad(
    isolated_mobile_config: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = realtime_module.RealtimeSession(
        session_id="sess-json-shape",
        client_secret="secret-json",
        websocket_url="wss://api.openai.com/v1/realtime?model=gpt-test&session=sess-json-shape",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        model="gpt-test",
        voice="verse",
        agent_name="unknown-caller",
        device_id=isolated_mobile_config,
        metadata={},
    )

    captured_metadata: List[Dict[str, Any]] = []

    async def _fake_create(**kwargs):  # type: ignore[no-untyped-def]
        metadata = dict(kwargs.get("metadata") or {})
        captured_metadata.append(metadata)
        session.metadata = metadata
        session.agent_name = kwargs.get("agent_name")
        session.device_id = kwargs.get("device_id")
        return session

    monkeypatch.setattr(realtime_module, "create_realtime_session", _fake_create)
    monkeypatch.setattr("app.api.mobile.create_realtime_session", _fake_create)

    client = TestClient(app)
    response = client.post("/v1/mobile/realtime/session", json={"deviceId": isolated_mobile_config})
    assert response.status_code == 200

    body = response.json()
    assert "serverVad" in body
    assert "server_vad" not in body
    assert body["serverVad"]["activation_threshold"] == pytest.approx(0.55, rel=1e-3)

    extras = pop_call(body["callSid"])
    assert extras is not None
    assert extras[-1]["server_vad"]["activation_threshold"] == pytest.approx(0.55, rel=1e-3)
    assert captured_metadata and captured_metadata[0]["server_vad"]["silence_duration_ms"] == 420


def test_realtime_refresh_requires_call_sid() -> None:
    client = TestClient(app)
    response = client.post("/v1/mobile/realtime/session/refresh", json={"callSid": ""})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "callSid required"


def test_realtime_refresh_unknown_session_returns_404() -> None:
    client = TestClient(app)
    response = client.post("/v1/mobile/realtime/session/refresh", json={"callSid": "missing-call"})
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Session not found"


@pytest.mark.asyncio
async def test_realtime_refresh_allows_multiple_rotations(
    isolated_mobile_config: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    initial_session = realtime_module.RealtimeSession(
        session_id="sess-initial",
        client_secret="secret-initial",
        websocket_url="wss://api.openai.com/v1/realtime?model=gpt-test&session=sess-initial",
        expires_at=now + timedelta(minutes=5),
        model="gpt-test",
        voice="verse",
        agent_name="unknown-caller",
        device_id=isolated_mobile_config,
        metadata={},
    )
    refresh_one = realtime_module.RealtimeSession(
        session_id="sess-refresh-1",
        client_secret="secret-refresh-1",
        websocket_url="wss://api.openai.com/v1/realtime?model=gpt-test&session=sess-refresh-1",
        expires_at=now + timedelta(minutes=8),
        model="gpt-test",
        voice="verse",
        agent_name="unknown-caller",
        device_id=isolated_mobile_config,
        metadata={},
    )
    refresh_two = realtime_module.RealtimeSession(
        session_id="sess-refresh-2",
        client_secret="secret-refresh-2",
        websocket_url="wss://api.openai.com/v1/realtime?model=gpt-test&session=sess-refresh-2",
        expires_at=now + timedelta(minutes=12),
        model="gpt-test",
        voice="verse",
        agent_name="unknown-caller",
        device_id=isolated_mobile_config,
        metadata={},
    )

    responses = [initial_session, refresh_one, refresh_two]

    async def _fake_create(**kwargs):  # type: ignore[no-untyped-def]
        session_obj = responses.pop(0)
        session_obj.metadata = dict(kwargs.get("metadata") or {})
        session_obj.agent_name = kwargs.get("agent_name")
        session_obj.device_id = kwargs.get("device_id")
        return session_obj

    class _FakeUUID:
        def __init__(self, value: str) -> None:
            self.hex = value

    uuid_values = iter(["call-001", "token-start", "token-refresh-1", "token-refresh-2"])

    def _fake_uuid4():
        return _FakeUUID(next(uuid_values))

    monkeypatch.setattr(realtime_module, "create_realtime_session", _fake_create)
    monkeypatch.setattr("app.api.mobile.create_realtime_session", _fake_create)
    monkeypatch.setattr("app.api.mobile.uuid4", _fake_uuid4)

    client = TestClient(app)
    start_response = client.post("/v1/mobile/realtime/session", json={"deviceId": isolated_mobile_config})
    assert start_response.status_code == 200
    start_body = start_response.json()
    assert start_body["sessionId"] == "sess-initial"
    assert start_body["websocketToken"] == "token-start"
    call_sid = start_body["callSid"]
    assert call_sid == "android-call-001"

    first_refresh = client.post("/v1/mobile/realtime/session/refresh", json={"callSid": call_sid})
    assert first_refresh.status_code == 200
    first_body = first_refresh.json()
    assert first_body["sessionId"] == "sess-refresh-1"
    assert first_body["websocketToken"] == "token-refresh-1"

    second_refresh = client.post("/v1/mobile/realtime/session/refresh", json={"callSid": call_sid})
    assert second_refresh.status_code == 200
    second_body = second_refresh.json()
    assert second_body["sessionId"] == "sess-refresh-2"
    assert second_body["websocketToken"] == "token-refresh-2"

    store = realtime_module.get_realtime_store()
    assert store.get_session("sess-initial") is None
    assert store.get_session("sess-refresh-1") is None
    assert store.get_session("sess-refresh-2") is refresh_two

    extras = pop_call(call_sid)
    assert extras is not None
    assert extras[-1]["realtime_session_id"] == "sess-refresh-2"
    assert extras[-1]["mobile_token"] == "token-refresh-2"
    assert extras[-1]["server_vad"]["activation_threshold"] == pytest.approx(0.55, rel=1e-3)


@pytest.mark.asyncio
async def test_realtime_session_uses_realtime_config_overrides(
    isolated_mobile_config: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path(os.environ["RINGDOWN_CONFIG_PATH"])
    original = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    updated = copy.deepcopy(original)

    defaults_rt = updated.setdefault("defaults", {}).setdefault("realtime", {})
    defaults_rt["model"] = "gpt-default"
    defaults_rt["voice"] = "default-voice"
    defaults_rt["server_vad"] = {
        "activation_threshold": 0.61,
        "silence_duration_ms": 510,
    }

    agent_rt = updated.setdefault("agents", {}).setdefault("unknown-caller", {}).setdefault("realtime", {})
    agent_rt["model"] = "gpt-agent-override"
    agent_rt["voice"] = "voice-override"

    config_path.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")
    settings.refresh_config_cache()

    captured_calls: List[Dict[str, Any]] = []

    async def _fake_create(**kwargs):  # type: ignore[no-untyped-def]
        captured_calls.append(kwargs)
        return realtime_module.RealtimeSession(
            session_id="sess-config",
            client_secret="secret-config",
            websocket_url="wss://api.openai.com/v1/realtime?model=gpt-agent-override&session=sess-config",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            model=kwargs["model"],
            voice=kwargs["voice"],
            agent_name=kwargs["agent_name"],
            device_id=kwargs["device_id"],
            metadata=dict(kwargs.get("metadata") or {}),
        )

    monkeypatch.setattr(realtime_module, "create_realtime_session", _fake_create)
    monkeypatch.setattr("app.api.mobile.create_realtime_session", _fake_create)

    try:
        client = TestClient(app)
        response = client.post("/v1/mobile/realtime/session", json={"deviceId": isolated_mobile_config})
        assert response.status_code == 200
        assert captured_calls, "Expected create_realtime_session to be invoked"

        call_kwargs = captured_calls[0]
        assert call_kwargs["model"] == "gpt-agent-override"
        assert call_kwargs["voice"] == "voice-override"
        assert call_kwargs["metadata"]["server_vad"]["activation_threshold"] == pytest.approx(0.61, rel=1e-3)

        body = response.json()
        assert body["model"] == "gpt-agent-override"
        assert body["voice"] == "voice-override"
        assert body["serverVad"]["activation_threshold"] == pytest.approx(0.61, rel=1e-3)
    finally:
        config_path.write_text(yaml.safe_dump(original, sort_keys=False), encoding="utf-8")
        settings.refresh_config_cache()


@pytest.mark.asyncio
async def test_realtime_refresh_updates_session(isolated_mobile_config: str, monkeypatch: pytest.MonkeyPatch) -> None:
    first_session = realtime_module.RealtimeSession(
        session_id="sess-initial",
        client_secret="secret-initial",
        websocket_url="wss://api.openai.com/v1/realtime?model=gpt-test&session=sess-initial",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        model="gpt-test",
        voice="verse",
        agent_name="unknown-caller",
        device_id=isolated_mobile_config,
        metadata={},
    )
    refresh_session = realtime_module.RealtimeSession(
        session_id="sess-refresh",
        client_secret="secret-refresh",
        websocket_url="wss://api.openai.com/v1/realtime?model=gpt-test&session=sess-refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=8),
        model="gpt-test",
        voice="verse",
        agent_name="unknown-caller",
        device_id=isolated_mobile_config,
        metadata={},
    )

    responses = [first_session, refresh_session]

    async def _fake_create(**kwargs):  # type: ignore[no-untyped-def]
        return responses.pop(0)

    monkeypatch.setattr(realtime_module, "create_realtime_session", _fake_create)
    monkeypatch.setattr("app.api.mobile.create_realtime_session", _fake_create)

    client = TestClient(app)
    start_response = client.post("/v1/mobile/realtime/session", json={"deviceId": isolated_mobile_config})
    assert start_response.status_code == 200
    start_body = start_response.json()
    call_sid = start_body.get("callSid") or start_body.get("call_sid")

    store = realtime_module.get_realtime_store()
    current = store.get_by_call(call_sid)
    assert current is first_session

    monkeypatch.setattr(realtime_module, "create_realtime_session", _fake_create)
    monkeypatch.setattr("app.api.mobile.create_realtime_session", _fake_create)

    refresh_response = client.post("/v1/mobile/realtime/session/refresh", json={"callSid": call_sid})
    assert refresh_response.status_code == 200
    refresh_body = refresh_response.json()
    refresh_session_id = refresh_body.get("sessionId") or refresh_body.get("session_id")
    assert refresh_session_id == "sess-refresh"
    refresh_token = refresh_body.get("websocketToken") or refresh_body.get("websocket_token")
    start_token = start_body.get("websocketToken") or start_body.get("websocket_token")
    assert refresh_token != start_token
    server_vad = refresh_body.get("serverVad") or refresh_body.get("server_vad")
    assert server_vad is not None
    assert server_vad["activation_threshold"] == pytest.approx(0.55, rel=1e-3)

    updated = store.get_by_call(call_sid)
    assert updated is refresh_session
    assert store.get_session("sess-initial") is None
    assert store.get_session("sess-refresh") is refresh_session

    extras = pop_call(call_sid)
    assert extras[-1]["realtime_session_id"] == "sess-refresh"
    assert extras[-1]["server_vad"]["silence_duration_ms"] == 420
    assert extras[-1]["mobile_token"] == refresh_body["websocketToken"]
