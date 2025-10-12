"""Centralised logging configuration for Ringdown components."""

from __future__ import annotations

import logging
import os
import re
import sys
import warnings

__all__ = ["get_highest_caller_name", "logger", "setup_logging"]


def get_highest_caller_name() -> str:
    """Return the top-level caller module name for structured logging."""

    depth = 1
    highest_caller_name: str | None = None
    while True:
        try:
            frame = sys._getframe(depth)
        except ValueError:
            break
        calling_file_path = frame.f_code.co_filename
        calling_file_name = os.path.basename(calling_file_path)
        if calling_file_name == "pydevd_runpy.py":
            break
        calling_file_name_without_ext = os.path.splitext(calling_file_name)[0]
        highest_caller_name = calling_file_name_without_ext
        depth += 1
    return highest_caller_name or "unknown"


def _silence_pydantic_warnings() -> None:
    """Filter noisy serialization warnings emitted by the OpenAI SDK."""

    try:
        from pydantic import PydanticSerializationWarning  # type: ignore
    except Exception:  # pragma: no cover - pydantic optional at runtime
        warnings.filterwarnings(
            "ignore",
            message="Pydantic serializer warnings",
            category=UserWarning,
        )
    else:
        warnings.filterwarnings("ignore", category=PydanticSerializationWarning)


def setup_logging(logger: logging.Logger | None = None) -> logging.Logger:
    """Initialise consistent logging handlers across the application."""

    _silence_pydantic_warnings()

    logging.getLogger().setLevel(logging.DEBUG)
    logging.getLogger("swagger_spec_validator").setLevel(logging.WARNING)

    if not os.environ.get("LOG_LOVE_SKIP_LITELLM_PATCH"):
        try:
            from litellm import _logging as litellm_internal_logging  # type: ignore
        except Exception:  # pragma: no cover - LiteLLM optional
            pass
        else:
            if hasattr(litellm_internal_logging, "_disable_debugging"):
                litellm_internal_logging._disable_debugging()

    for noisy in ("httpx", "urllib3", "httpcore", "h5py", "git", "git.cmd"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    litellm_logger = logging.getLogger("litellm")
    litellm_logger.setLevel(logging.CRITICAL)
    litellm_logger.handlers.clear()
    litellm_logger.addHandler(logging.NullHandler())
    litellm_logger.propagate = False

    caller_name = get_highest_caller_name()
    logger = logger or logging.getLogger(caller_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    safe_name = re.sub(r'[<>:"/\\|?*]', "_", caller_name)
    if not safe_name:
        safe_name = "app"
    if not logger.handlers:
        try:
            if not os.path.exists("logs"):
                os.makedirs("logs")

            log_path = os.path.join("logs", "app.log")
            file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s - %(message)s",
                "%Y-%m-%d %I:%M:%S %p",
            )
            file_handler.setFormatter(file_formatter)

            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_formatter = logging.Formatter("%(message)s")
            console_handler.setFormatter(console_formatter)

            logger.addHandler(file_handler)
            logger.addHandler(console_handler)

            root_logger = logging.getLogger()
            if not any(
                isinstance(h, logging.FileHandler) and h.baseFilename == file_handler.baseFilename
                for h in root_logger.handlers
            ):
                root_logger.addHandler(file_handler)
            if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
                root_logger.addHandler(console_handler)
        except Exception as exc:  # pragma: no cover - filesystem edge cases
            print(f"Error creating file handler and/or console handler: {exc}")

    return logger


logger = setup_logging()
