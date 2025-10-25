"""Centralised logging configuration for Ringdown components."""

from __future__ import annotations

import logging
import os
import re
import sys
import warnings
from collections.abc import Mapping
from typing import Any

__all__ = ["get_highest_caller_name", "logger", "redact_sensitive_data", "setup_logging"]

_DEFAULT_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}
_SENSITIVE_KEY_MARKERS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "ACCOUNT_SID")
_MIN_SECRET_LENGTH = 6
_REDACTED_PLACEHOLDER = "***REDACTED***"


def _collect_secret_values() -> set[str]:
    """Return likely secret values sourced from environment variables."""

    secrets: set[str] = set()
    for key, value in os.environ.items():
        if not value:
            continue
        if len(value) < _MIN_SECRET_LENGTH:
            continue
        upper_key = key.upper()
        if any(marker in upper_key for marker in _SENSITIVE_KEY_MARKERS):
            secrets.add(value)
    return secrets


def _redact_string(value: str, secrets: set[str]) -> str:
    """Replace occurrences of secrets within a string with a placeholder."""

    redacted = value
    for secret in secrets:
        if secret and secret in redacted:
            redacted = redacted.replace(secret, _REDACTED_PLACEHOLDER)
    return redacted


def _redact_data(value: Any, secrets: set[str]) -> Any:
    """Recursively redact secrets from mapping and sequence structures."""

    if not secrets:
        return value
    if isinstance(value, str):
        return _redact_string(value, secrets)
    if isinstance(value, Mapping):
        sanitized_items = {k: _redact_data(v, secrets) for k, v in value.items()}
        try:
            return value.__class__(sanitized_items)
        except Exception:
            return sanitized_items
    if isinstance(value, list):
        return [_redact_data(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_data(item, secrets) for item in value)
    if isinstance(value, set):
        return {_redact_data(item, secrets) for item in value}
    return value


def redact_sensitive_data(value: Any) -> Any:
    """Return a copy of value with sensitive tokens removed."""

    secrets = _collect_secret_values()
    if not secrets:
        return value
    return _redact_data(value, secrets)


class _SecretFilter(logging.Filter):
    """Logging filter that redacts sensitive tokens from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = _collect_secret_values()
        if not secrets:
            return True

        message = record.getMessage()
        redacted_message = _redact_string(message, secrets)
        record.msg = redacted_message
        record.args = ()
        record.__dict__["message"] = redacted_message

        for key, value in list(record.__dict__.items()):
            if key in _DEFAULT_LOG_RECORD_FIELDS:
                continue
            record.__dict__[key] = _redact_data(value, secrets)
        return True


_SECRET_FILTER = _SecretFilter()


def _attach_secret_filter(target: logging.Logger) -> None:
    """Ensure the secret-redaction filter is installed on loggers and handlers."""

    if not any(isinstance(existing, _SecretFilter) for existing in target.filters):
        target.addFilter(_SECRET_FILTER)
    for handler in target.handlers:
        if not any(isinstance(existing, _SecretFilter) for existing in handler.filters):
            handler.addFilter(_SECRET_FILTER)


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

    _attach_secret_filter(logger)
    _attach_secret_filter(logging.getLogger())

    return logger


logger = setup_logging()
