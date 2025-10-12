"""Backward-compatible logging utilities shim."""

from __future__ import annotations

from app.logging_utils import get_highest_caller_name, setup_logging

__all__ = ["get_highest_caller_name", "setup_logging"]


def main() -> None:
    logger = setup_logging()
    logger.info("Testing!")


if __name__ == "__main__":
    main()
