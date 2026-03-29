"""Tests for model capabilities registry."""

from types import SimpleNamespace

import pytest

from app.model_capabilities import (
    _find_capabilities,
    _normalize_model_name,
    can_use_reasoning_effort_with_tools,
    get_max_tool_id_length,
    should_include_reasoning_effort,
    supports_reasoning_effort,
)


class TestNormalizeModelName:
    def test_strips_provider_prefix(self):
        assert _normalize_model_name("openai/gpt-5.4") == "gpt-5.4"
        assert _normalize_model_name("anthropic/claude-opus-4-6") == "claude-opus-4-6"

    def test_handles_no_prefix(self):
        assert _normalize_model_name("gpt-5.4") == "gpt-5.4"

    def test_normalizes_case(self):
        assert _normalize_model_name("GPT-5.4") == "gpt-5.4"
        assert _normalize_model_name("OpenAI/GPT-5.4-MINI") == "gpt-5.4-mini"


class TestFindCapabilities:
    def test_exact_match(self):
        caps = _find_capabilities("gpt-5.4")
        assert caps is not None
        assert caps.supports_reasoning_effort is True
        assert caps.reasoning_effort_with_tools is False

    def test_prefix_match(self):
        caps = _find_capabilities("gpt-5.4-2026-03-05")
        assert caps is not None
        assert caps.supports_reasoning_effort is True

    def test_unknown_model_returns_none(self):
        caps = _find_capabilities("unknown-model-xyz")
        assert caps is None

    def test_with_provider_prefix(self):
        caps = _find_capabilities("openai/gpt-5.4-mini")
        assert caps is not None
        assert caps.supports_reasoning_effort is True


class TestSupportsReasoningEffort:
    def test_gpt54_supports(self):
        assert supports_reasoning_effort("gpt-5.4") is True
        assert supports_reasoning_effort("openai/gpt-5.4") is True

    def test_gpt54_mini_supports(self):
        assert supports_reasoning_effort("gpt-5.4-mini") is True

    def test_gpt5_supports(self):
        assert supports_reasoning_effort("gpt-5") is True
        assert supports_reasoning_effort("gpt-5-mini") is True

    def test_claude_does_not_support(self):
        assert supports_reasoning_effort("claude-opus-4-6") is False
        assert supports_reasoning_effort("anthropic/claude-sonnet") is False

    def test_gemini_does_not_support(self):
        assert supports_reasoning_effort("gemini-pro") is False

    def test_unknown_model_returns_false(self):
        assert supports_reasoning_effort("unknown-model") is False


class TestCanUseReasoningEffortWithTools:
    def test_gpt54_cannot_use_with_tools(self):
        assert can_use_reasoning_effort_with_tools("gpt-5.4") is False
        assert can_use_reasoning_effort_with_tools("gpt-5.4-mini") is False
        assert can_use_reasoning_effort_with_tools("gpt-5.4-nano") is False

    def test_gpt5_can_use_with_tools(self):
        assert can_use_reasoning_effort_with_tools("gpt-5") is True
        assert can_use_reasoning_effort_with_tools("gpt-5-mini") is True
        assert can_use_reasoning_effort_with_tools("gpt-5-instant") is True

    def test_claude_can_use_with_tools(self):
        assert can_use_reasoning_effort_with_tools("claude-opus") is True

    def test_unknown_model_returns_true(self):
        assert can_use_reasoning_effort_with_tools("unknown-model") is True


class TestShouldIncludeReasoningEffort:
    def test_no_effort_level_returns_false(self):
        assert (
            should_include_reasoning_effort("gpt-5.4", has_tools=False, effort_level=None) is False
        )
        assert should_include_reasoning_effort("gpt-5.4", has_tools=False, effort_level="") is False

    def test_model_without_support_returns_false(self):
        assert (
            should_include_reasoning_effort("claude-opus", has_tools=False, effort_level="medium")
            is False
        )

    def test_gpt54_without_tools_includes(self):
        assert (
            should_include_reasoning_effort("gpt-5.4", has_tools=False, effort_level="medium")
            is True
        )

    def test_gpt54_with_tools_excludes(self):
        assert (
            should_include_reasoning_effort("gpt-5.4", has_tools=True, effort_level="medium")
            is False
        )

    def test_gpt5_with_tools_includes(self):
        assert (
            should_include_reasoning_effort("gpt-5", has_tools=True, effort_level="medium") is True
        )
        assert (
            should_include_reasoning_effort("gpt-5-mini", has_tools=True, effort_level="low")
            is True
        )

    def test_with_provider_prefix(self):
        assert (
            should_include_reasoning_effort("openai/gpt-5.4", has_tools=False, effort_level="high")
            is True
        )
        assert (
            should_include_reasoning_effort("openai/gpt-5.4", has_tools=True, effort_level="high")
            is False
        )


class TestGetMaxToolIdLength:
    def test_openai_models_have_40_char_limit(self):
        assert get_max_tool_id_length("gpt-5.4") == 40
        assert get_max_tool_id_length("gpt-5") == 40
        assert get_max_tool_id_length("openai/gpt-5.4-mini") == 40

    def test_non_openai_models_no_limit(self):
        assert get_max_tool_id_length("claude-opus") is None
        assert get_max_tool_id_length("gemini-pro") is None

    def test_unknown_model_no_limit(self):
        assert get_max_tool_id_length("unknown-model") is None


class TestChatIntegration:
    """Integration tests verifying reasoning_effort behavior in stream_response."""

    @pytest.mark.asyncio
    async def test_gpt54_excludes_reasoning_effort_with_tools(self):
        """GPT-5.4 should NOT include reasoning_effort when tools are present."""
        from unittest.mock import patch

        from app.chat import stream_response

        agent = {
            "model": "gpt-5.4",
            "prompt": "You are helpful.",
            "temperature": 0.7,
            "max_tokens": 100,
            "max_history": 100,
            "reasoning_effort": "medium",
            "tools": ["TavilySearch"],
        }

        captured_kwargs = {}

        async def mock_acompletion(**kwargs):
            captured_kwargs.update(kwargs)

            async def gen():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta={"content": "Hello"}, finish_reason="stop")]
                )

            gen.return_value = None
            return gen()

        with patch("app.chat.acompletion", side_effect=mock_acompletion), patch(
            "app.chat.tf.get_tools_for_agent",
            return_value=[{"type": "function", "function": {"name": "test"}}],
        ), patch("app.chat.tf.execute_tool", return_value={}):
            result = []
            async for token in stream_response("Hi", agent):
                if isinstance(token, str):
                    result.append(token)

        assert "reasoning_effort" not in captured_kwargs, (
            "reasoning_effort should NOT be included for GPT-5.4 with tools"
        )

    @pytest.mark.asyncio
    async def test_gpt54_includes_reasoning_effort_without_tools(self):
        """GPT-5.4 SHOULD include reasoning_effort when no tools are present."""
        from unittest.mock import patch

        from app.chat import stream_response

        agent = {
            "model": "gpt-5.4",
            "prompt": "You are helpful.",
            "temperature": 0.7,
            "max_tokens": 100,
            "max_history": 100,
            "reasoning_effort": "medium",
        }

        captured_kwargs = {}

        async def mock_acompletion(**kwargs):
            captured_kwargs.update(kwargs)

            async def gen():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta={"content": "Hello"}, finish_reason="stop")]
                )

            gen.return_value = None
            return gen()

        with patch("app.chat.acompletion", side_effect=mock_acompletion):
            result = []
            async for token in stream_response("Hi", agent):
                if isinstance(token, str):
                    result.append(token)

        assert captured_kwargs.get("reasoning_effort") == "medium", (
            "reasoning_effort should be included for GPT-5.4 without tools"
        )

    @pytest.mark.asyncio
    async def test_gpt5_includes_reasoning_effort_with_tools(self):
        """GPT-5 CAN include reasoning_effort even when tools are present."""
        from unittest.mock import patch

        from app.chat import stream_response

        agent = {
            "model": "gpt-5",
            "prompt": "You are helpful.",
            "temperature": 0.7,
            "max_tokens": 100,
            "max_history": 100,
            "reasoning_effort": "high",
            "tools": ["TavilySearch"],
        }

        captured_kwargs = {}

        async def mock_acompletion(**kwargs):
            captured_kwargs.update(kwargs)

            async def gen():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta={"content": "Hello"}, finish_reason="stop")]
                )

            gen.return_value = None
            return gen()

        with patch("app.chat.acompletion", side_effect=mock_acompletion), patch(
            "app.chat.tf.get_tools_for_agent",
            return_value=[{"type": "function", "function": {"name": "test"}}],
        ), patch("app.chat.tf.execute_tool", return_value={}):
            result = []
            async for token in stream_response("Hi", agent):
                if isinstance(token, str):
                    result.append(token)

        assert captured_kwargs.get("reasoning_effort") == "high", (
            "reasoning_effort should be included for GPT-5 with tools"
        )

    @pytest.mark.asyncio
    async def test_thinking_level_alias_works(self):
        """thinking_level should work as an alias for reasoning_effort."""
        from unittest.mock import patch

        from app.chat import stream_response

        agent = {
            "model": "gpt-5.4",
            "prompt": "You are helpful.",
            "temperature": 0.7,
            "max_tokens": 100,
            "max_history": 100,
            "thinking_level": "low",
        }

        captured_kwargs = {}

        async def mock_acompletion(**kwargs):
            captured_kwargs.update(kwargs)

            async def gen():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta={"content": "Hello"}, finish_reason="stop")]
                )

            gen.return_value = None
            return gen()

        with patch("app.chat.acompletion", side_effect=mock_acompletion):
            result = []
            async for token in stream_response("Hi", agent):
                if isinstance(token, str):
                    result.append(token)

        assert captured_kwargs.get("reasoning_effort") == "low", (
            "thinking_level should work as alias for reasoning_effort"
        )

    @pytest.mark.asyncio
    async def test_change_llm_applies_thinking_level_to_agent(self):
        """change_llm should update the active agent reasoning level."""
        from unittest.mock import patch

        from app.chat import stream_response
        from app.tool_runner import ToolEvent

        agent = {
            "model": "claude-opus-4-6",
            "prompt": "You are helpful.",
            "temperature": 0.7,
            "max_tokens": 100,
            "max_history": 100,
            "tools": ["change_llm"],
        }

        async def mock_acompletion(**kwargs):
            async def gen():
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta={
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_change_model",
                                        "function": {
                                            "name": "change_llm",
                                            "arguments": '{"model_choice":"gpt-5-high"}',
                                        },
                                    }
                                ]
                            },
                            finish_reason="tool_calls",
                        )
                    ]
                )

            return gen()

        async def fake_run(self, tool_name, call_id, args, exec_fn):
            yield ToolEvent(
                "result",
                data={
                    "action": "model_changed",
                    "previous_model": "claude-opus-4-6",
                    "new_model": "openai/gpt-5.2",
                    "model_label": "gpt-5-high",
                    "settings": {
                        "temperature": 1.0,
                        "max_tokens": 16000,
                        "thinking_level": "high",
                    },
                    "message": "Switched to gpt-5-high.\n\n",
                },
            )

        with patch("app.chat.acompletion", side_effect=mock_acompletion), patch(
            "app.chat.tf.get_tools_for_agent",
            return_value=[{"type": "function", "function": {"name": "change_llm"}}],
        ), patch("app.chat.ToolRunner.run", new=fake_run):
            output = []
            async for token in stream_response("switch to gpt-5-high", agent):
                output.append(token)

        assert agent["model"] == "openai/gpt-5.2"
        assert agent["reasoning_effort"] == "high"
        assert any(isinstance(token, str) and "Switched to gpt-5-high" in token for token in output)

    @pytest.mark.asyncio
    async def test_change_llm_clears_reasoning_level_when_missing(self):
        """change_llm should clear stale reasoning_effort when new model has none."""
        from unittest.mock import patch

        from app.chat import stream_response
        from app.tool_runner import ToolEvent

        agent = {
            "model": "openai/gpt-5.2",
            "prompt": "You are helpful.",
            "temperature": 0.7,
            "max_tokens": 100,
            "max_history": 100,
            "reasoning_effort": "high",
            "tools": ["change_llm"],
        }

        async def mock_acompletion(**kwargs):
            async def gen():
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta={
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_change_model",
                                        "function": {
                                            "name": "change_llm",
                                            "arguments": '{"model_choice":"sonnet"}',
                                        },
                                    }
                                ]
                            },
                            finish_reason="tool_calls",
                        )
                    ]
                )

            return gen()

        async def fake_run(self, tool_name, call_id, args, exec_fn):
            yield ToolEvent(
                "result",
                data={
                    "action": "model_changed",
                    "previous_model": "openai/gpt-5.2",
                    "new_model": "claude-sonnet-4-6",
                    "model_label": "sonnet",
                    "settings": {
                        "temperature": 1.0,
                        "max_tokens": 12000,
                    },
                    "message": "Switched to sonnet.\n\n",
                },
            )

        with patch("app.chat.acompletion", side_effect=mock_acompletion), patch(
            "app.chat.tf.get_tools_for_agent",
            return_value=[{"type": "function", "function": {"name": "change_llm"}}],
        ), patch("app.chat.ToolRunner.run", new=fake_run):
            output = []
            async for token in stream_response("switch to sonnet", agent):
                output.append(token)

        assert agent["model"] == "claude-sonnet-4-6"
        assert "reasoning_effort" not in agent
        assert any(isinstance(token, str) and "Switched to sonnet" in token for token in output)
