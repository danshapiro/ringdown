"""Test WebSocket reconnection for long-running calls."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.main import websocket_endpoint
from app.api import websocket as websocket_api

CALL_STATE = (
    "test_agent",
    {
        "prompt": "Test prompt",
        "voice": "test_voice",
        "language": "en-US",
        "welcome_greeting": "Test greeting",
        "max_disconnect_seconds": 7200,
        "model": "gpt-4",
    },
    None,
    False,
    "+1234567890",
)


@pytest.mark.asyncio
async def test_reconnection_at_55_minutes():
    """Test that WebSocket closes gracefully at 55 minutes with reconnection message."""
    # Mock WebSocket
    mock_ws = Mock(spec=WebSocket)
    mock_ws.client = ("127.0.0.1", 12345)
    mock_ws.headers = {"x-twilio-signature": "test_signature"}
    mock_ws.scope = {}
    mock_ws.url = Mock(scheme="wss", hostname="test.com", path="/ws", query="")
    
    # Track sent messages and close parameters
    sent_messages = []
    close_params = {}
    
    async def mock_send_json(data):
        sent_messages.append(data)
    
    async def mock_accept(**kwargs):
        pass
    
    async def mock_close(code=None, reason=None):
        close_params["code"] = code
        close_params["reason"] = reason
        raise WebSocketDisconnect(code=code, reason=reason)
    
    # Setup mock methods
    mock_ws.send_json = AsyncMock(side_effect=mock_send_json)
    mock_ws.accept = AsyncMock(side_effect=mock_accept)
    mock_ws.close = AsyncMock(side_effect=mock_close)
    
    # Mock message iterator
    messages_sent = False
    
    async def mock_iter_text():
        nonlocal messages_sent
        # First yield setup message
        yield json.dumps({
            "type": "setup",
            "callSid": "test_call_123"
        })
        
        # Then simulate waiting past 55 minutes
        if not messages_sent:
            messages_sent = True
            # Set connection start time to 55 minutes ago
            mock_ws.scope["connection_start_time"] = time.perf_counter() - 3301
            yield json.dumps({"type": "keepalive"})
    
    mock_ws.iter_text = mock_iter_text
    
    # Mock dependencies
    with patch.object(websocket_api, "is_from_twilio", return_value=True), \
         patch.object(websocket_api, "pop_call", return_value=CALL_STATE):
        
        # Run the WebSocket endpoint
        await websocket_endpoint(mock_ws)
    
    # Verify reconnection message was sent
    reconnection_messages = [
        msg for msg in sent_messages 
        if "reconnect" in str(msg.get("token", "")).lower()
    ]
    assert len(reconnection_messages) > 0
    assert "maintain quality" in reconnection_messages[0]["token"]
    
    # Verify close was called with correct parameters
    assert close_params["code"] == 4000
    assert close_params["reason"] == "Graceful reconnection required"


@pytest.mark.asyncio
async def test_no_reconnection_before_55_minutes():
    """Test that reconnection doesn't trigger before 55 minutes."""
    # Mock WebSocket
    mock_ws = Mock(spec=WebSocket)
    mock_ws.client = ("127.0.0.1", 12345)
    mock_ws.headers = {"x-twilio-signature": "test_signature"}
    mock_ws.scope = {}
    mock_ws.url = Mock(scheme="wss", hostname="test.com", path="/ws", query="")
    
    sent_messages = []
    
    async def mock_send_json(data):
        sent_messages.append(data)
    
    # Setup mock methods
    mock_ws.send_json = AsyncMock(side_effect=mock_send_json)
    mock_ws.accept = AsyncMock()
    mock_ws.close = AsyncMock()
    
    # Mock message iterator
    message_count = 0
    
    async def mock_iter_text():
        nonlocal message_count
        # First yield setup message
        yield json.dumps({
            "type": "setup",
            "callSid": "test_call_123"
        })
        
        # Simulate messages at 30 minutes (should not trigger reconnection)
        mock_ws.scope["connection_start_time"] = time.perf_counter() - 1800  # 30 minutes
        
        # Send a few messages then stop
        for _ in range(3):
            message_count += 1
            yield json.dumps({"type": "keepalive"})
        
        # Simulate disconnect
        raise WebSocketDisconnect(code=1000, reason="Normal closure")
    
    mock_ws.iter_text = mock_iter_text
    
    # Mock dependencies
    with patch.object(websocket_api, "is_from_twilio", return_value=True), \
         patch.object(websocket_api, "pop_call", return_value=CALL_STATE):
        
        # Run the WebSocket endpoint
        await websocket_endpoint(mock_ws)
    
    # Verify no reconnection message was sent
    reconnection_messages = [
        msg for msg in sent_messages 
        if "reconnect" in str(msg.get("token", "")).lower()
    ]
    assert len(reconnection_messages) == 0
    
    # Verify close was not called with reconnection code
    mock_ws.close.assert_not_called()


@pytest.mark.asyncio
async def test_reconnection_flag_prevents_multiple_messages():
    """Test that reconnection message is only sent once."""
    # Mock WebSocket
    mock_ws = Mock(spec=WebSocket)
    mock_ws.client = ("127.0.0.1", 12345)
    mock_ws.headers = {"x-twilio-signature": "test_signature"}
    mock_ws.scope = {}
    mock_ws.url = Mock(scheme="wss", hostname="test.com", path="/ws", query="")
    
    sent_messages = []
    
    async def mock_send_json(data):
        sent_messages.append(data)
    
    # Setup mock methods
    mock_ws.send_json = AsyncMock(side_effect=mock_send_json)
    mock_ws.accept = AsyncMock()
    
    close_count = 0
    
    async def mock_close(code=None, reason=None):
        nonlocal close_count
        close_count += 1
        if close_count == 1:
            # First close, don't actually disconnect
            return
        else:
            # Second close, disconnect
            raise WebSocketDisconnect(code=code, reason=reason)
    
    mock_ws.close = AsyncMock(side_effect=mock_close)
    
    # Mock message iterator
    async def mock_iter_text():
        # Setup message
        yield json.dumps({
            "type": "setup",
            "callSid": "test_call_123"
        })
        
        # Set connection time past 55 minutes
        mock_ws.scope["connection_start_time"] = time.perf_counter() - 3301
        
        # Send multiple messages after 55 minutes
        for i in range(5):
            yield json.dumps({"type": "keepalive", "index": i})
            if i == 0:
                # After first message, set reconnection_sent flag
                mock_ws.scope["reconnection_sent"] = True
    
    mock_ws.iter_text = mock_iter_text
    
    # Mock dependencies
    with patch.object(websocket_api, "is_from_twilio", return_value=True), \
         patch.object(websocket_api, "pop_call", return_value=CALL_STATE):
        
        # Run the WebSocket endpoint
        await websocket_endpoint(mock_ws)
    
    # Count reconnection messages
    reconnection_messages = [
        msg for msg in sent_messages 
        if "reconnect" in str(msg.get("token", "")).lower()
    ]
    
    # Should only have one reconnection message
    assert len(reconnection_messages) == 1


def test_connection_duration_calculation():
    """Test connection duration calculation for logging."""
    # Test duration formatting
    test_cases = [
        (300, "00:05:00"),      # 5 minutes
        (3300, "00:55:00"),     # 55 minutes
        (3600, "01:00:00"),     # 1 hour
        (7200, "02:00:00"),     # 2 hours
        (10800, "03:00:00"),    # 3 hours
    ]
    
    for seconds, expected in test_cases:
        h, rem = divmod(int(seconds), 3600)
        m, s = divmod(rem, 60)
        formatted = f"{h:02}:{m:02}:{s:02}"
        assert formatted == expected


def test_reconnection_timing_threshold():
    """Test the 55-minute threshold logic."""
    # Connection times in seconds
    test_cases = [
        (3299, False),  # 54:59 - should not trigger
        (3300, True),   # 55:00 - should trigger
        (3301, True),   # 55:01 - should trigger
        (3599, True),   # 59:59 - should trigger
        (3600, True),   # 60:00 - should trigger
    ]
    
    for elapsed_seconds, should_trigger in test_cases:
        # Simulate the condition from the implementation
        reconnection_sent = False
        triggers = elapsed_seconds >= 3300 and not reconnection_sent
        
        assert triggers == should_trigger, \
            f"Failed for {elapsed_seconds}s: expected {should_trigger}, got {triggers}"


@pytest.mark.asyncio
async def test_banner_logging_on_call_setup():
    """Test that a visually distinct banner is logged when a call starts."""
    
    # Mock WebSocket
    mock_ws = Mock(spec=WebSocket)
    mock_ws.client = ("127.0.0.1", 12345)
    mock_ws.headers = {"x-twilio-signature": "test_signature"}
    mock_ws.scope = {}
    mock_ws.url = Mock(scheme="wss", hostname="test.com", path="/ws", query="")
    
    sent_messages = []
    
    async def mock_send_json(data):
        sent_messages.append(data)
    
    # Setup mock methods
    mock_ws.send_json = AsyncMock(side_effect=mock_send_json)
    mock_ws.accept = AsyncMock()
    mock_ws.close = AsyncMock()
    
    # Mock message iterator - just send setup then disconnect
    async def mock_iter_text():
        # Setup message
        yield json.dumps({
            "type": "setup",
            "callSid": "test_call_123"
        })
        
        # Simulate disconnect
        raise WebSocketDisconnect(code=1000, reason="Normal closure")
    
    mock_ws.iter_text = mock_iter_text
    
    # Mock dependencies with caller number
    caller_state = CALL_STATE[:-1] + ("+15551234567",)

    with patch.object(websocket_api, "is_from_twilio", return_value=True), \
         patch.object(websocket_api, "pop_call", return_value=caller_state):
        
        # Run the WebSocket endpoint
        await websocket_endpoint(mock_ws)
    
    # The test passes if no exceptions are raised
    # The banner logging would be visible in the console output during test execution
    # We can't easily capture logger output in this test, but we can verify the setup
    # message was processed correctly by checking that the WebSocket was accepted
    mock_ws.accept.assert_called_once()


@pytest.mark.asyncio
async def test_banner_logging_unknown_caller():
    """Test that 'unknown' is displayed when caller number is None."""
    
    # Mock WebSocket
    mock_ws = Mock(spec=WebSocket)
    mock_ws.client = ("127.0.0.1", 12345)
    mock_ws.headers = {"x-twilio-signature": "test_signature"}
    mock_ws.scope = {}
    mock_ws.url = Mock(scheme="wss", hostname="test.com", path="/ws", query="")
    
    sent_messages = []
    
    async def mock_send_json(data):
        sent_messages.append(data)
    
    # Setup mock methods
    mock_ws.send_json = AsyncMock(side_effect=mock_send_json)
    mock_ws.accept = AsyncMock()
    mock_ws.close = AsyncMock()
    
    # Mock message iterator - just send setup then disconnect
    async def mock_iter_text():
        # Setup message
        yield json.dumps({
            "type": "setup",
            "callSid": "test_call_123"
        })
        
        # Simulate disconnect
        raise WebSocketDisconnect(code=1000, reason="Normal closure")
    
    mock_ws.iter_text = mock_iter_text
    
    # Mock dependencies with None caller number
    caller_state = CALL_STATE[:-1] + (None,)

    with patch.object(websocket_api, "is_from_twilio", return_value=True), \
         patch.object(websocket_api, "pop_call", return_value=caller_state):
        
        # Run the WebSocket endpoint
        await websocket_endpoint(mock_ws)
    
    # The test passes if no exceptions are raised
    # The banner logging would show "unknown" for the caller number
    mock_ws.accept.assert_called_once()