"""Ringdown FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
import litellm

from app.api import mobile, twilio, websocket
from app.api.websocket import websocket_endpoint as websocket_endpoint
from app.audio import (
    _build_prosody_tag,
    apply_prosody,
    merge_prosody,
    prosody_is_useful,
    provider_supports_speed,
    rate_to_speed_factor,
    voice_supports_ssml,
)
from app.lifespan import lifespan
from app.logging_utils import logger, setup_logging
from app.metrics import METRIC_MESSAGES, router as metrics_router
from app.settings import get_project_name
from app import settings
from app.validators import validator as _twilio_validator

__all__ = [
    "app",
    "METRIC_MESSAGES",
    "websocket_endpoint",
    "_build_prosody_tag",
    "_merge_prosody",
    "apply_prosody",
    "merge_prosody",
    "prosody_is_useful",
    "provider_supports_speed",
    "rate_to_speed_factor",
    "voice_supports_ssml",
    "logger",
    "settings",
    "_twilio_validator",
    "litellm",
]

# Ensure logging handlers are initialised once when the module loads.
setup_logging()


def _create_app() -> FastAPI:
    """Construct the FastAPI application and mount routers."""

    title = get_project_name().replace("-", " ").title()
    application = FastAPI(title=title, lifespan=lifespan)

    # Core health/metrics endpoints.
    application.include_router(metrics_router)

    # Webhook routers for Twilio Voice and ConversationRelay streaming.
    application.include_router(twilio.router)
    application.include_router(mobile.router)
    application.include_router(websocket.router)

    return application


app = _create_app()

# Preserve the legacy helper name expected by tests/importers.
_merge_prosody = merge_prosody
