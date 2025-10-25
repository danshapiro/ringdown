from datetime import datetime
from pathlib import Path

import logging

from sqlalchemy.exc import OperationalError
from sqlmodel import SQLModel, create_engine, Field, Session
import json

from log_love import setup_logging
from .settings import get_env


# Module logger
logger = setup_logging()

env = get_env()
_db_path = Path(env.sqlite_path).expanduser()
_db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{_db_path}", echo=False)


class Turn(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow)
    who: str = Field(index=True)  # "user" | "bot"
    text: str
    source: str | None = Field(default=None, index=True)


_TURN_SOURCE_COLUMN_READY = False


def _ensure_turn_source_column() -> None:
    """Ensure the ``turn`` table has a ``source`` column (adds it lazily)."""

    global _TURN_SOURCE_COLUMN_READY
    if _TURN_SOURCE_COLUMN_READY:
        return

    table_name = Turn.__tablename__ or "turn"

    with engine.connect() as connection:  # type: ignore[no-redef]
        result = connection.exec_driver_sql(f"PRAGMA table_info({table_name})")
        columns = {row[1] for row in result.fetchall()}
        if "source" not in columns:
            try:
                connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN source TEXT")
            except OperationalError as exc:  # pragma: no cover - defensive
                if "duplicate column name" not in str(exc).lower():
                    raise

    _TURN_SOURCE_COLUMN_READY = True


def log_turn(who: str, text: str, *, source: str | None = None) -> None:
    """Persist a single conversational turn."""

    _ensure_turn_source_column()

    with Session(engine) as sess:
        turn = Turn(who=who, text=text, source=source)
        sess.add(turn)
        sess.commit()

        logger.debug(f"Added turn to db with id={turn.id}")

# ---------------------------------------------------------------------------
# Persistent per-agent conversation state
# ---------------------------------------------------------------------------


class AgentState(SQLModel, table=True):
    """Latest conversation state for each agent (one row per agent)."""

    agent_name: str = Field(primary_key=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    # JSON-encoded snapshots to avoid complex relational schema for now.
    settings_json: str
    messages_json: str


# -------- helper API --------------------------------------------------------


_STATE_MAX_AGE_SEC: int = 5 * 60  # 5 minutes freshness window


def load_state(agent_name: str) -> tuple[dict | None, list | None]:
    """Return (settings_dict, messages_list) if state exists and is fresh (<5 min)."""

    with Session(engine) as sess:
        st = sess.get(AgentState, agent_name)
        if not st:
            return None, None

        age_sec = (datetime.utcnow() - st.updated_at).total_seconds()
        if age_sec > _STATE_MAX_AGE_SEC:
            # Stale – treat as missing so caller starts fresh.
            return None, None

        return json.loads(st.settings_json), json.loads(st.messages_json)


def save_state(agent_name: str, settings: dict, messages: list) -> None:
    """Upsert the latest *settings* and *messages* snapshot for *agent_name*."""

    rec = AgentState(
        agent_name=agent_name,
        settings_json=json.dumps(
            {
                "model": settings.get("model"),
                "temperature": settings.get("temperature"),
                "max_tokens": settings.get("max_tokens"),
            },
            ensure_ascii=False,
        ),
        messages_json=json.dumps(messages, ensure_ascii=False),
    )

    with Session(engine) as sess:
        sess.merge(rec)
        sess.commit()


def delete_state(agent_name: str) -> None:
    """Remove any stored state for *agent_name* (no-op if absent)."""

    with Session(engine) as sess:
        st = sess.get(AgentState, agent_name)
        if st:
            sess.delete(st)
            sess.commit() 
