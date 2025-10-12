"""FastAPI lifespan context for the Ringdown application."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from .logging_utils import logger
from .memory import AgentState, Turn, engine
from .settings import get_project_name


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise and tear down application resources."""

    project_name = get_project_name()
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info(
        "\n=============================="
        "==============================\n"
        "  %s service reload @ %s\n"
        "=============================="
        "==============================",
        project_name.title(),
        stamp,
    )

    # Ensure SQLite schema exists before serving requests.
    Turn.metadata.create_all(engine)
    AgentState.metadata.create_all(engine)

    yield
    # No shutdown hooks yet; placeholder for future cleanup.
