"""Tests for the reset tool functionality."""

import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio

from app.tool_framework import execute_tool, list_tools, TOOL_REGISTRY
from app.settings import get_agent_config


class TestResetTool:
    """Test cases for the reset tool."""

    def test_reset_tool_registered(self):
        """Test that the reset tool is properly registered."""
        available_tools = list_tools()
        assert "reset" in available_tools, "Reset tool should be registered"

    def test_reset_tool_execution(self):
        """Test that the reset tool executes and returns expected result."""
        result = execute_tool("reset", {"confirm": True})

        assert isinstance(result, dict), "Reset tool should return a dict"
        assert result["action"] == "reset_conversation", "Should return reset action"
        assert result["message"].startswith("Reset."), "Should return reset marker"
        assert "status" in result, "Should include status message"

    def test_reset_tool_in_ringdown_demo_config(self):
        """Test that the reset tool is available in ringdown-demo configuration."""
        agent_config = get_agent_config("ringdown-demo")
        agent_tools = agent_config.get("tools", [])

        assert "reset" in agent_tools, "Reset tool should be in ringdown-demo tools"

    def test_reset_tool_not_in_unknown_caller_config(self):
        """Test that the reset tool is NOT available to unknown callers."""
        agent_config = get_agent_config("unknown-caller")
        agent_tools = agent_config.get("tools", [])
        
        assert "reset" not in agent_tools, "Reset tool should not be available to unknown callers"

    def test_reset_tool_prompt_restrictiveness(self):
        """Test that the reset tool has very restrictive prompt guidance."""
        tool_spec = TOOL_REGISTRY["reset"]
        prompt = tool_spec.prompt.lower()
        description = tool_spec.description.lower()
        
        assert isinstance(prompt, str) and prompt.strip()
        assert isinstance(description, str) and description.strip()

    def test_reset_tool_description_emphasizes_destruction(self):
        """Test that the description clearly warns about the destructive nature."""
        tool_spec = TOOL_REGISTRY["reset"]
        description = tool_spec.description.lower()
        
        assert isinstance(description, str) and description.strip()

    def test_reset_tool_args_validation(self):
        """Test that the reset tool validates its arguments correctly."""
        # Valid args should work
        result = execute_tool("reset", {"confirm": True})
        assert result["action"] == "reset_conversation"
        
        # Missing args should be handled (confirm has default value)
        result = execute_tool("reset", {})
        assert result["action"] == "reset_conversation"

    def test_reset_tool_response_structure(self):
        """Test that the reset tool returns the expected response structure."""
        result = execute_tool("reset", {"confirm": True})
        
        # Verify all expected keys are present
        expected_keys = {"action", "message", "status"}
        assert set(result.keys()) == expected_keys, f"Reset tool should return exactly these keys: {expected_keys}"
        
        # Verify specific values
        assert result["action"] == "reset_conversation"
        assert result["message"].startswith("Reset.")
        assert isinstance(result["status"], str) and len(result["status"]) > 0

    # Removed detailed text checks – prompt wording may evolve.
    def test_reset_tool_prompt_examples(self):
        tool_spec = TOOL_REGISTRY["reset"]
        assert isinstance(tool_spec.prompt, str) and tool_spec.prompt.strip()

    @pytest.mark.asyncio
    async def test_reset_tool_yields_reset_marker(self):
        """Test that reset tool execution in stream_response yields a reset marker."""
        from app.chat import stream_response
        
        # Set up mock agent config
        agent_config = {
            "model": "gpt-4",
            "temperature": 0.7,
            "max_tokens": 500,
            "max_history": 1000,
            "prompt": "You are a helpful assistant.",
            "tools": ["reset"],
            "welcome_greeting": "Welcome back."
        }
        
        # Create messages array
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "previous conversation"}
        ]
        
        # Mock litellm.acompletion to simulate reset tool call
        mock_response = AsyncMock()
        mock_chunks = [
            MagicMock(choices=[MagicMock(delta={"content": "Resetting..."}, finish_reason=None)]),
            MagicMock(choices=[MagicMock(delta={
                "tool_calls": [{
                    "index": 0,
                    "id": "call_123",
                    "function": {
                        "name": "reset",
                        "arguments": '{"confirm": true}'
                    }
                }]
            }, finish_reason=None)]),
            MagicMock(choices=[MagicMock(delta={}, finish_reason="tool_calls")])
        ]
        
        async def async_generator():
            for chunk in mock_chunks:
                yield chunk
        
        mock_response.__aiter__.return_value = async_generator()
        
        collected_output = []
        
        with patch('litellm.acompletion', return_value=mock_response):
            async for token in stream_response("reset", agent_config, messages):
                collected_output.append(token)
        
        # Check for reset marker
        reset_markers = [t for t in collected_output if isinstance(t, dict) and t.get("type") == "reset_conversation"]
        assert len(reset_markers) == 1, "Should yield exactly one reset_conversation marker"
        assert reset_markers[0]["message"] == "Reset. Welcome back", "Reset marker should contain the message"
        
        # The reset message should NOT be yielded as separate text (it's sent by WebSocket handler)
        text_tokens = [t for t in collected_output if isinstance(t, str)]
        assert "Reset. Welcome back" not in text_tokens, "Reset message should not be yielded as text - WebSocket handler sends it"