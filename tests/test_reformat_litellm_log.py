import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import reformat_litellm_log as formatter  # noqa: E402


def test_extract_messages_literal_payload():
    payload = (
        "INFO Message array content: "
        "[{'role': 'user', 'content': 'hello'}, {'role': 'assistant', 'content': 'hi'}]"
    )

    result = formatter.extract_messages(payload)

    assert result is not None

    messages, truncated = result

    assert truncated is False
    assert [msg["role"] for msg in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == "hi"


def test_extract_messages_partial_payload_marks_truncated():
    payload = "Message array content: [{'role': 'user', 'content': 'hello'}"

    result = formatter.extract_messages(payload)

    assert result is not None

    messages, truncated = result

    assert truncated is True
    assert len(messages) == 1
    assert messages[0]["content"] == "hello"


def test_wrap_content_breaks_long_lines():
    wrapped = formatter.wrap_content("hello world", width=5)

    lines = wrapped.splitlines()
    assert lines[0] == "hello"
    assert lines[-1].strip() == "world"
    assert len(lines) >= 2


def test_format_entry_includes_truncated_label():
    entry = {"timestamp": "2025-11-09T00:00:00Z"}
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi", "tool_calls": [1]},
    ]

    formatted = formatter.format_entry(1, entry, messages, width=80, truncated=True)

    assert "[TRUNCATED]" in formatted
    assert "01. USER" in formatted
    assert "02. ASSISTANT (tool_call)" in formatted


def test_normalize_content_handles_json_string():
    content = '{"foo": 1}'

    normalized = formatter.normalize_content(content)

    assert "\n" in normalized
    assert '"foo"' in normalized
    assert '1' in normalized
