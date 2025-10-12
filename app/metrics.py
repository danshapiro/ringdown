"""Health and metrics endpoints for Ringdown."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

try:  # pragma: no cover - optional dependency
    from prometheus_client import Counter, generate_latest
except ModuleNotFoundError:  # pragma: no cover - graceful fallback

    class _NoopCounter:
        def __init__(self, *_args, **_kwargs) -> None:
            return

        def labels(self, *_args, **_kwargs):
            return self

        def inc(self, *_args, **_kwargs):
            return None

    def Counter(*_args, **_kwargs):  # type: ignore[override]
        return _NoopCounter()

    def generate_latest() -> bytes:
        return b"# metrics disabled -- install prometheus_client to enable\n"


router = APIRouter()

# Shared counter used by the WebSocket handler to track message roles.
METRIC_MESSAGES = Counter("messages_total", "Messages processed", ["role"])


@router.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    """Liveness probe for container orchestrators."""

    return "ok"


@router.get("/healthz/", response_class=PlainTextResponse, include_in_schema=False)
def healthz_trailing_slash() -> str:
    """Handle load balancers that normalize the health check path with a slash."""

    return healthz()


@router.get("/metrics")
def metrics() -> PlainTextResponse:
    """Expose Prometheus metrics (or a no-op payload when disabled)."""

    return PlainTextResponse(generate_latest(), media_type="text/plain")
