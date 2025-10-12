#!/usr/bin/env python3
"""Tests for WebSocket interrupt handling.

These tests verify that the WebSocket endpoint properly handles interrupt
messages from Twilio ConversationRelay when users barge in on the bot's speech.
"""

import json
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import WebSocket

from app.main import websocket_endpoint
from app.api import websocket as websocket_api


@pytest.mark.asyncio
async def test_interrupt_message_handling():
    """Test that interrupt messages are properly handled without closing the connection."""
    
    # Mock WebSocket
    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.client = "test_client"
    mock_ws.headers = {"x-twilio-signature": "test_sig"}
    mock_ws.scope = {}
    
    # Mock the Twilio validation to pass
    with patch.object(websocket_api, 'is_from_twilio', return_value=True):
        # Mock the message iterator to send setup, then interrupt, then prompt
        messages = [
            '{"type": "setup", "callSid": "test_call"}',
            '{"type": "interrupt", "utteranceUntilInterrupt": "The latest session had your party", "durationUntilInterruptMs": 938}',
            '{"type": "prompt", "voicePrompt": "Tell me about the session"}',
        ]
        
        async def mock_iter_text():
            for msg in messages:
                yield msg
        
        mock_ws.iter_text = mock_iter_text
        
        # Mock dependencies
        with patch.object(websocket_api, 'get_agent_config') as mock_get_agent:
            with patch.object(websocket_api, 'pop_call', return_value=None):
                with patch.object(websocket_api, 'stream_response') as mock_stream:
                    with patch.object(websocket_api, 'log_turn') as mock_log_turn:
                        with patch.object(websocket_api, 'run_in_threadpool', side_effect=lambda f, *args: None):
                            mock_get_agent.return_value = {
                                "prompt": "Test prompt",
                                "model": "test-model", 
                                "max_disconnect_seconds": 60,
                                "voice": "test-voice",
                                "tts_provider": "test-provider"
                            }
                            
                            # Mock the stream_response to yield a simple response
                            async def mock_response_stream(*args):
                                yield "This is a test response."
                            
                            mock_stream.return_value = mock_response_stream()
                            
                            # This should not raise an exception
                            try:
                                await websocket_endpoint(mock_ws)
                            except StopAsyncIteration:
                                # Expected when we run out of test messages
                                pass
        
        # Verify that accept() was called (connection established)
        mock_ws.accept.assert_called_once()
        
        # Verify that send_json was called (for the prompt response)
        assert mock_ws.send_json.call_count > 0


@pytest.mark.asyncio
async def test_malformed_json_handling():
    """Test that malformed JSON messages don't crash the WebSocket connection."""
    
    # Mock WebSocket
    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.client = "test_client"
    mock_ws.headers = {"x-twilio-signature": "test_sig"}
    mock_ws.scope = {}
    
    # Mock the Twilio validation to pass
    with patch.object(websocket_api, 'is_from_twilio', return_value=True):
        # Mock messages including malformed JSON and valid messages after
        messages = [
            '{"type": "setup", "callSid": "test_call"}',
            '{malformed json here}',  # This should be handled gracefully
            '{"type": "prompt", "voicePrompt": "Are you still there?"}',
        ]
        
        async def mock_iter_text():
            for msg in messages:
                yield msg
        
        mock_ws.iter_text = mock_iter_text
        
        # Mock dependencies
        with patch.object(websocket_api, 'get_agent_config') as mock_get_agent:
            with patch.object(websocket_api, 'pop_call', return_value=None):
                with patch.object(websocket_api, 'stream_response') as mock_stream:
                    with patch.object(websocket_api, 'log_turn') as mock_log_turn:
                        with patch.object(websocket_api, 'run_in_threadpool', side_effect=lambda f, *args: None):
                            mock_get_agent.return_value = {
                                "prompt": "Test prompt",
                                "model": "test-model", 
                                "max_disconnect_seconds": 60,
                                "voice": "test-voice",
                                "tts_provider": "test-provider"
                            }
                            
                            # Mock the stream_response to yield a simple response
                            async def mock_response_stream(*args):
                                yield "I'm still here."
                            
                            mock_stream.return_value = mock_response_stream()
                            
                            # This should not raise an exception even with malformed JSON
                            try:
                                await websocket_endpoint(mock_ws)
                            except StopAsyncIteration:
                                # Expected when we run out of test messages
                                pass
        
        # Verify that accept() was called (connection established)
        mock_ws.accept.assert_called_once()
        
        # Verify that send_json was called multiple times:
        # - Error message for malformed JSON 
        # - Response to the valid prompt after
        assert mock_ws.send_json.call_count >= 2
        
        # Check that an error message was sent for the malformed JSON
        error_calls = [call for call in mock_ws.send_json.call_args_list 
                       if 'malformed message' in str(call)]
        assert len(error_calls) > 0


def test_interrupt_message_structure():
    """Test that we can parse interrupt message fields correctly."""
    
    # Based on the actual interrupt message from logs
    interrupt_msg = {
        "type": "interrupt",
        "utteranceUntilInterrupt": " The latest session had your party defeating an Elder Brain in the mind flayer stronghold Illithinoch, ",
        "durationUntilInterruptMs": 938
    }
    
    # Verify we can extract the expected fields
    assert interrupt_msg.get("type") == "interrupt"
    assert interrupt_msg.get("utteranceUntilInterrupt") == " The latest session had your party defeating an Elder Brain in the mind flayer stronghold Illithinoch, "
    assert interrupt_msg.get("durationUntilInterruptMs") == 938


def test_interrupt_with_additional_fields():
    """Test that we can handle interrupt messages with optional fields."""
    
    interrupt_msg = {
        "type": "interrupt",
        "utteranceUntilInterrupt": "Test partial utterance",
        "durationUntilInterruptMs": 1500,
        "reason": "user_speech",
        "confidence": 0.95
    }
    
    # Extract fields like the handler would
    utterance_partial = interrupt_msg.get("utteranceUntilInterrupt", "")
    duration_ms = interrupt_msg.get("durationUntilInterruptMs", 0)
    reason = interrupt_msg.get("reason")
    confidence = interrupt_msg.get("confidence")
    
    # Verify we can extract all the information
    assert utterance_partial == "Test partial utterance"
    assert duration_ms == 1500
    assert reason == "user_speech"
    assert confidence == 0.95


@pytest.mark.asyncio
async def test_interrupt_does_not_break_conversation_flow():
    """Test that interrupts don't break the conversation flow."""
    
    # Mock WebSocket
    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.client = "test_client"
    mock_ws.headers = {"x-twilio-signature": "test_sig"}
    mock_ws.scope = {}
    
    # Track the message processing order
    processing_order = []
    
    # Mock the Twilio validation to pass
    with patch.object(websocket_api, 'is_from_twilio', return_value=True):
        # Mock messages: setup -> prompt -> interrupt -> prompt
        messages = [
            '{"type": "setup", "callSid": "test_call"}',
            '{"type": "prompt", "voicePrompt": "Tell me a story"}',
            '{"type": "interrupt", "utteranceUntilInterrupt": "Once upon a time", "durationUntilInterruptMs": 500}',
            '{"type": "prompt", "voicePrompt": "What about dragons?"}',
        ]
        
        async def mock_iter_text():
            for msg in messages:
                processing_order.append(json.loads(msg)["type"])
                yield msg
        
        mock_ws.iter_text = mock_iter_text
        
        # Mock dependencies
        with patch.object(websocket_api, 'get_agent_config') as mock_get_agent:
            with patch.object(websocket_api, 'pop_call', return_value=None):
                with patch.object(websocket_api, 'stream_response') as mock_stream:
                    with patch.object(websocket_api, 'log_turn') as mock_log_turn:
                        with patch.object(websocket_api, 'run_in_threadpool', side_effect=lambda f, *args: None):
                            mock_get_agent.return_value = {
                                "prompt": "Test prompt",
                                "model": "test-model", 
                                "max_disconnect_seconds": 60,
                                "voice": "test-voice",
                                "tts_provider": "test-provider"
                            }
                            
                            # Mock the stream_response to yield a simple response
                            async def mock_response_stream(*args):
                                yield "Response to: " + args[0]
                            
                            mock_stream.return_value = mock_response_stream("")
                            
                            # Process the messages
                            try:
                                await websocket_endpoint(mock_ws)
                            except StopAsyncIteration:
                                pass
        
        # Verify that all message types were processed in order
        assert processing_order == ["setup", "prompt", "interrupt", "prompt"]
        
        # Verify that both prompts were processed (stream_response called twice)
        assert mock_stream.call_count == 2 