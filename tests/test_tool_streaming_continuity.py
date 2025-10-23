"""Test streaming continuity during tool execution.

This test verifies the fix for the bug where tool announcements like "Searching."
would be sent with last=True, causing the rest of the response to be lost.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.chat import stream_response


@pytest.mark.asyncio
async def test_stream_continuity_with_tools():
    """Test that tool execution doesn't break streaming continuity."""
    
    # Configure agent with tools
    agent = {
        "model": "gpt-5",
        "temperature": 1.0,
        "max_tokens": 1000,
        "max_history": 1000,
        "prompt": "You are a helpful assistant.",
        "max_tool_iterations": 3,
        "tools": ["TavilySearch"]
    }
    
    # Mock the tool framework
    with patch("app.chat.tf.set_agent_context"):
        with patch("app.chat.tf.get_tools_for_agent") as mock_get_tools:
            mock_get_tools.return_value = [
                {"type": "function", "function": {"name": "TavilySearch", "parameters": {}}}
            ]
            
            # Mock litellm.acompletion to simulate tool usage
            with patch("app.chat.acompletion") as mock_acompletion:
                # First call: LLM directly requests tool with no preceding text
                first_response = AsyncMock()
                first_response.__aiter__.return_value = [
                    MagicMock(
                        choices=[MagicMock(
                            delta={"content": None, "tool_calls": [{
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "TavilySearch", "arguments": '{"query": "test"}'}
                            }]},
                            finish_reason="tool_calls"
                        )]
                    )
                ]
                
                # Second call: LLM generates actual response
                second_response = AsyncMock()
                second_response.__aiter__.return_value = [
                    MagicMock(
                        choices=[MagicMock(
                            delta={"content": " I found three versions of the story.", "tool_calls": None},
                            finish_reason="stop"
                        )]
                    )
                ]
                
                mock_acompletion.side_effect = [first_response, second_response]
                
                # Mock tool execution
                with patch("app.chat.tf.execute_tool") as mock_execute:
                    mock_execute.return_value = {"result": "search results"}
                    
                    # Collect all yielded values
                    result = []
                    async for token in stream_response("Find stories", agent):
                        result.append(token)
                    
                    # Verify we got both text and marker
                    text_tokens = [t for t in result if isinstance(t, str)]
                    markers = [t for t in result if isinstance(t, dict)]

                    # We now expect two dict tokens: first play (thinking sound media), then tool_executing
                    assert any(m.get("type") == "tool_executing" for m in markers), "tool_executing marker missing"
                    assert any(m.get("type") == "play" for m in markers), "play marker missing"

                    # Verify text continuity – Search announcement is a play marker, so the
                    # plain text tokens should only include the final answer.
                    full_text = "".join(text_tokens)
                    assert "I found three versions of the story." in full_text
                    
                    # Verify tool was executed
                    assert mock_execute.call_count == 1


@pytest.mark.asyncio
async def test_thinking_sound_emitted_during_llm_wait():
    """Ensure thinking audio starts before the LLM yields tokens."""

    agent = {
        "model": "gpt-5",
        "temperature": 0.7,
        "max_tokens": 100,
        "max_history": 10,
        "prompt": "You are a helpful assistant.",
        "max_tool_iterations": 1,
        "tools": None,
    }

    with patch("app.chat.tf.set_agent_context"):
        with patch("app.chat.acompletion") as mock_acompletion:
            response = AsyncMock()
            response.__aiter__.return_value = [
                MagicMock(
                    choices=[
                        MagicMock(
                            delta={"content": "Hello there!", "tool_calls": None},
                            finish_reason="stop",
                        )
                    ]
                )
            ]

            mock_acompletion.return_value = response

            tokens = []
            async for item in stream_response("Hi", agent):
                tokens.append(item)

            play_markers = [t for t in tokens if isinstance(t, dict) and t.get("type") == "play"]
            assert play_markers, "Expected thinking play payload before text tokens"
            assert any(isinstance(t, str) for t in tokens), "Expected assistant text tokens"

@pytest.mark.asyncio
async def test_websocket_processes_markers():
    """Test that WebSocket handler correctly processes tool execution markers."""
    
    # Simulate the WebSocket handler's processing logic
    assistant_full = []
    markers_seen = []
    
    # Mock stream with tool execution marker
    async def mock_stream():
        # Tool announcement is now handled by ToolRunner, not yielded as text
        yield {"type": "tool_executing", "tool_count": 1}
        yield " Found results."
    
    # Process as WebSocket handler would
    async for token in mock_stream():
        if isinstance(token, dict):
            if token.get("type") == "tool_executing":
                markers_seen.append(token)
                continue
        else:
            assistant_full.append(token)
    
    # Verify correct processing
    assert len(markers_seen) == 1
    assert "".join(assistant_full) == " Found results."
    
    # No marker text should be in the assistant output
    assert "tool_executing" not in "".join(assistant_full) 
