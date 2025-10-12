"""Global pytest configuration for Ringdown tests."""

import os
from pathlib import Path

# Ensure required secrets exist so modules importing get_env() during test
# collection do not fail before individual tests monkeypatch them.
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-twilio-token")
# Force Tavily tests to skip unless an explicit real key is provided in the
# environment this session was launched with.
os.environ.setdefault("TAVILY_API_KEY", "")
os.environ.setdefault("RINGDOWN_ASYNC_START_WAIT", "0.05")


def _ensure_sqlite_path() -> None:
    """Point SQLite persistence at a test-local path and ensure parent exists."""

    project_root = Path(__file__).resolve().parent.parent
    db_path = project_root / "data" / "test_memory.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SQLITE_PATH", str(db_path))


def _ensure_test_config_path() -> None:
    """Pin tests to a dedicated config file with the demo agents enabled."""

    project_root = Path(__file__).resolve().parent.parent
    test_config = project_root / "tests" / "fixtures" / "config.test.yaml"
    if not test_config.exists():
        raise FileNotFoundError("tests/fixtures/config.test.yaml is required for pytest runs")

    os.environ.setdefault("RINGDOWN_CONFIG_PATH", str(test_config))


_ensure_sqlite_path()
_ensure_test_config_path()
