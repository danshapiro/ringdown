import asyncio
from types import SimpleNamespace

import pytest

from app import memory
from app import tool_framework as tf
from app.chat import stream_response


def test_log_turn_persists_id(tmp_path, monkeypatch):
    """log_turn should commit a row with a non-null id and log it."""

    # Swap the persistent DB for an in-memory one
    from sqlmodel import SQLModel, create_engine, Session, select

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(memory, "engine", engine)
    monkeypatch.setattr(memory, "Session", Session)

    memory.log_turn("user", "hello world")

    with Session(engine) as sess:
        row = sess.exec(select(memory.Turn).order_by(memory.Turn.id.desc())).first()
        assert row is not None
        assert row.id is not None and row.id > 0
        assert row.text == "hello world"


def test_execute_tool_invalid_args_raises():
    """execute_tool must raise ValidationError when args fail validation."""

    with pytest.raises(Exception):
        tf.execute_tool("TavilySearch", {"topic": "news"})  # missing required 'query'


@pytest.mark.asyncio
async def test_stream_response_handles_tool_error(monkeypatch):
    """stream_response should not raise even when tool execution validates bad args."""

    # Prepare two fake LLM responses: one that requests a tool with bad args, then a plain text reply.
    first_msg = {
        "role": "assistant",
        "content": "Searching…",
        "tool_calls": [
            {
                "id": "call_1",
                "function": {
                    "name": "TavilySearch",
                    "arguments": "{\"topic\": \"news\"}",
                },
                "type": "function",
            }
        ],
    }

    second_msg = {"role": "assistant", "content": "Sorry, query needed."}

    messages_queue = [
        SimpleNamespace(choices=[SimpleNamespace(message=first_msg)]),
        SimpleNamespace(choices=[SimpleNamespace(message=second_msg)]),
    ]

    async def fake_acompletion(*args, **kwargs):  # noqa: D401
        """Return an async iterator yielding a single pre-canned response.

        stream_response expects litellm.acompletion(stream=True) to return an
        async iterator.  The real SDK does this, so our stub must mirror that
        contract.  Raising early if the queue is empty gives a clearer error
        than silently returning a non-iterable object.
        """

        if not messages_queue:
            raise RuntimeError("No more fake responses queued – test logic error")

        next_resp = messages_queue.pop(0)

        async def _gen():  # noqa: D401 – simple stub iterator
            yield next_resp

        return _gen()

    import litellm, app.chat as chat_module

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion, raising=True)
    monkeypatch.setattr(chat_module, "acompletion", fake_acompletion, raising=True)

    agent_cfg = {
            "model": "gpt-4.1-mini",
            "temperature": 0.1,
            "max_tokens": 16,
            "max_history": 1000,
            "tools": ["TavilySearch"],
        }

    token_list = []
    async for tok in stream_response("search the news", agent_cfg, []):
        token_list.append(tok)

    # We should have streamed both the pre-tool content and the final apology.
    assert any("Searching" in t for t in token_list)
    assert any("Sorry" in t for t in token_list) 