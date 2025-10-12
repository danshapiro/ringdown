#!/usr/bin/env python3
"""Tests for async tool execution functionality."""

import json
import time
import threading
from typing import Any, Dict
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field

from app.tool_framework import register_tool, execute_tool, TOOL_REGISTRY


class AsyncTestArgs(BaseModel):
    """Test arguments for async tool testing."""
    message: str = Field(..., description="Test message")
    delay: float = Field(default=0.1, description="Delay in seconds")


# Global variable to track async execution
async_execution_result = None
async_execution_completed = threading.Event()


def _async_test_tool(args: AsyncTestArgs) -> Dict[str, Any]:
    """Test async tool that simulates work with a delay."""
    global async_execution_result
    
    time.sleep(args.delay)  # Simulate work
    
    result = {
        "success": True,
        "message": f"Async execution completed: {args.message}",
        "delay": args.delay
    }
    
    async_execution_result = result
    async_execution_completed.set()
    return result


def _sync_test_tool(args: AsyncTestArgs) -> Dict[str, Any]:
    """Test sync tool for comparison."""
    time.sleep(args.delay)  # Simulate work
    
    return {
        "success": True,
        "message": f"Sync execution completed: {args.message}",
        "delay": args.delay
    }


def test_async_tool_executes_in_background():
    """Test that async tools execute in background and return immediately."""
    # Register an async test tool
    register_tool(
        name="TestAsyncExecution",
        description="Test tool for async execution",
        param_model=AsyncTestArgs,
        async_execution=True
    )(_async_test_tool)
    
    # Reset global state
    global async_execution_result
    async_execution_result = None
    async_execution_completed.clear()
    
    # Execute async tool
    start_time = time.time()
    result = execute_tool("TestAsyncExecution", {"message": "test async", "delay": 0.2})
    immediate_time = time.time()
    
    # Check that we got an immediate response
    assert result["success"] is True
    assert result["async_execution"] is True
    assert "started asynchronously" in result["message"]
    assert immediate_time - start_time < 0.1  # Should be very fast
    
    # Wait for async execution to complete
    async_execution_completed.wait(timeout=1.0)
    
    # Check that async execution completed
    assert async_execution_result is not None
    assert async_execution_result["success"] is True
    assert "test async" in async_execution_result["message"]


def test_sync_tool_executes_normally():
    """Test that sync tools execute normally and block until completion."""
    # Register a sync test tool
    register_tool(
        name="TestSyncExecution", 
        description="Test tool for sync execution comparison",
        param_model=AsyncTestArgs,
        async_execution=False
    )(_sync_test_tool)
    
    # Execute sync tool
    start_time = time.time()
    result = execute_tool("TestSyncExecution", {"message": "test sync", "delay": 0.2})
    end_time = time.time()
    
    # Check that sync tool completed normally
    assert result["success"] is True
    assert "async_execution" not in result
    assert "test sync" in result["message"]
    assert end_time - start_time >= 0.15  # Should take at least most of the delay time


def test_async_error_handling():
    """Test that async tools handle errors correctly."""
    # Create a tool that will fail
    @register_tool(
        name="TestAsyncError",
        description="Test tool that fails",
        param_model=AsyncTestArgs,
        async_execution=True
    )
    def test_async_error_tool(args: AsyncTestArgs) -> Dict[str, Any]:
        """Test async tool that raises an error."""
        raise ValueError(f"Intentional test error: {args.message}")
    
    # Mock the error email function to avoid actually sending emails
    with patch('app.tool_framework._send_error_email') as mock_send_error:
        # Execute async tool that will fail
        result = execute_tool("TestAsyncError", {"message": "error test"})
        
        # Check immediate response
        assert result["success"] is True
        assert result["async_execution"] is True
        
        # Wait a bit for async execution to complete and fail
        time.sleep(0.5)
        
        # Check that error email was called
        mock_send_error.assert_called_once()
        call_args = mock_send_error.call_args
        assert call_args[0][0] == "TestAsyncError"  # tool name
        assert call_args[0][1] == {"message": "error test"}  # args
        assert isinstance(call_args[0][2], ValueError)  # error


def test_tool_registry_has_async_flags():
    """Test that tools are properly registered with async flags."""
    # Register test tools first
    register_tool(
        name="TestAsyncExecution2",
        description="Test tool for async execution",
        param_model=AsyncTestArgs,
        async_execution=True
    )(_async_test_tool)
    
    register_tool(
        name="TestSyncExecution2", 
        description="Test tool for sync execution comparison",
        param_model=AsyncTestArgs,
        async_execution=False
    )(_sync_test_tool)
    
    # Check that our test tools are registered
    assert "TestAsyncExecution2" in TOOL_REGISTRY
    assert "TestSyncExecution2" in TOOL_REGISTRY
    
    # Check async flags
    async_spec = TOOL_REGISTRY["TestAsyncExecution2"]
    sync_spec = TOOL_REGISTRY["TestSyncExecution2"]
    
    assert async_spec.async_execution is True
    assert sync_spec.async_execution is False


def test_real_tools_have_async_flags():
    """Test that real tools like SendEmail have async execution enabled."""
    # Check that production tools have async flags set correctly
    if "SendEmail" in TOOL_REGISTRY:
        assert TOOL_REGISTRY["SendEmail"].async_execution is True
    
    if "CreateGoogleDoc" in TOOL_REGISTRY:
        assert TOOL_REGISTRY["CreateGoogleDoc"].async_execution is True
        
    if "AppendGoogleDoc" in TOOL_REGISTRY:
        assert TOOL_REGISTRY["AppendGoogleDoc"].async_execution is True
    
    # Tools that should remain synchronous
    if "TavilySearch" in TOOL_REGISTRY:
        assert TOOL_REGISTRY["TavilySearch"].async_execution is False
        
    if "ReadGoogleDoc" in TOOL_REGISTRY:
        assert TOOL_REGISTRY["ReadGoogleDoc"].async_execution is False 