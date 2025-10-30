from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml
from fastapi.testclient import TestClient

from app import settings
from app.main import app
from app.managed_av.client import ManagedAVSession
from app.managed_av.session_store import get_session_store
from app.mobile.smoke import SmokeResult, run_smoke_test
import app.api.mobile as mobile


@pytest.fixture(autouse=True)
def clear_session_store() -> Iterator[None]:
    store = get_session_store()
    asyncio.run(store.clear())
    yield
    asyncio.run(store.clear())


@pytest.fixture
def isolated_mobile_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    project_root = Path(__file__).resolve().parents[1]
    source_config = project_root / "config.example.yaml"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(source_config.read_text(encoding="utf-8"), encoding="utf-8")

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    devices = data.setdefault("mobile_devices", {})
    devices["device-123"] = {
        "label": "Pixel 9",
        "agent": "unknown-caller",
        "enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    monkeypatch.setenv("RINGDOWN_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("MANAGED_AV_API_KEY", "test-managed-key")
    settings.refresh_config_cache()
    try:
        yield "device-123"
    finally:
        settings.refresh_config_cache()


class StubManagedClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def start_session(
        self,
        *,
        device_id: str,
        agent_name: str,
        greeting: str | None,
        device_metadata: dict | None,
        session_metadata: dict | None = None,
    ) -> ManagedAVSession:
        self.calls.append((device_id, agent_name))
        combined_metadata: dict[str, Any] = {}
        if device_metadata:
            combined_metadata.update(device_metadata)
        if session_metadata:
            combined_metadata.update(session_metadata)

        return ManagedAVSession(
            session_id="session-abc",
            agent=agent_name,
            room_url="https://example.daily.co/ringdown",
            access_token="token-xyz",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            pipeline_session_id="pipeline-session",
            greeting=greeting,
            metadata=combined_metadata,
        )

    async def close_session(self, session_id: str) -> None:
        self.calls.append(("close", session_id))


def _patch_client(monkeypatch: pytest.MonkeyPatch, stub: StubManagedClient) -> None:
    monkeypatch.setattr(mobile, "_managed_client", stub)


def test_smoke_success(
    isolated_mobile_config: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubManagedClient()
    _patch_client(monkeypatch, stub)

    async def fake_stream_response(user_text, agent_cfg, messages):  # type: ignore[no-untyped-def]
        yield "assistant reply"

    monkeypatch.setattr(mobile, "stream_response", fake_stream_response)
    monkeypatch.setattr(mobile, "log_turn", lambda *args, **kwargs: None)

    client = TestClient(app)
    result = run_smoke_test(client, device_id=isolated_mobile_config, prompt_text="hello")

    assert isinstance(result, SmokeResult)
    assert result.success is True
    assert result.session_id == "session-abc"
    assert result.pipeline_session_id == "pipeline-session"
    assert result.response_text == "assistant reply"
    assert isinstance(result.metadata.get("realtime"), dict)
    assert stub.calls == [("device-123", "unknown-caller"), ("close", "session-abc")]
