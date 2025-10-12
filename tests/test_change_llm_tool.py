"""Tests for the change_llm tool functionality."""

import pytest
from unittest.mock import patch

from app.tool_framework import execute_tool, list_tools, TOOL_REGISTRY
from app.settings import get_agent_config


class TestChangeLLMTool:
    """Test cases for the change_llm tool."""

    def test_change_llm_tool_registered(self):
        """Test that the change_llm tool is properly registered."""
        available_tools = list_tools()
        assert "change_llm" in available_tools, "change_llm tool should be registered"

    def test_change_llm_tool_configuration(self):
        """Test that the change_llm tool has correct configuration."""
        tool_spec = TOOL_REGISTRY.get("change_llm")
        assert tool_spec is not None, "change_llm tool should be in registry"
        assert isinstance(tool_spec.description, str) and tool_spec.description.strip()
        assert tool_spec.param_model.__name__ == "ChangeLLMArgs"

    def test_gpt_4_1_model_change(self):
        """Test changing to OpenAI GPT-4.1 model."""
        result = execute_tool("change_llm", {"model_choice": "gpt-4.1"})
        
        assert isinstance(result, dict), "Should return a dict"
        assert result["action"] == "model_changed"
        assert result["new_model"] == "gpt-4.1"
        assert result["model_name"] == "OpenAI GPT-4.1"
        assert result["settings"]["temperature"] == 0.7
        assert result["settings"]["max_tokens"] == 10000

    def test_gemini_pro_2_5_model_change(self):
        """Test changing to Google Gemini Pro 2.5 model."""
        result = execute_tool("change_llm", {"model_choice": "gemini-2.5-pro"})
        
        assert isinstance(result, dict), "Should return a dict"
        assert result["action"] == "model_changed"
        assert result["new_model"] == "gemini-2.5-pro"
        assert result["model_name"] == "Google Gemini Pro 2.5"
        assert isinstance(result["model_description"], str) and result["model_description"].strip()

    def test_o3_pro_model_change(self):
        """Test changing to OpenAI o3-pro model with temperature adjustment."""
        result = execute_tool("change_llm", {"model_choice": "o3-pro"})
        
        assert isinstance(result, dict), "Should return a dict"
        assert result["action"] == "model_changed"
        assert result["new_model"] == "o3-pro"
        assert result["model_name"] == "OpenAI o3-pro"
        # o3-pro should auto-adjust temperature to 1.0
        assert result["settings"]["temperature"] == 1.0

    def test_claude_sonnet_model_change(self):
        """Test changing to Claude 4 Sonnet model."""
        result = execute_tool("change_llm", {"model_choice": "claude-sonnet-4-20250514"})
        
        assert isinstance(result, dict), "Should return a dict"
        assert result["action"] == "model_changed"
        assert result["new_model"] == "claude-sonnet-4-20250514"
        assert result["model_name"] == "Claude 4 Sonnet"
        assert isinstance(result["model_description"], str) and result["model_description"].strip()

    def test_custom_parameters(self):
        """Test change_llm with custom temperature and max_tokens."""
                # Even if user supplies custom params, the tool now keeps built-in defaults
        result = execute_tool("change_llm", {
            "model_choice": "gpt-4.1",
            "temperature": 0.3,
            "max_tokens": 500
        })

        assert result["settings"]["temperature"] == 0.7
        assert result["settings"]["max_tokens"] == 10000

    def test_invalid_model_validation(self):
        """Test that invalid model choices are rejected."""
        with pytest.raises(Exception) as exc_info:
            execute_tool("change_llm", {"model_choice": "invalid-model"})
        
        error_msg = str(exc_info.value)
        assert "validation error" in error_msg.lower()
        assert "enum" in error_msg.lower()

    def test_temperature_bounds(self):
        """Test temperature parameter bounds."""
        # Valid temperature
        result = execute_tool("change_llm", {
            "model_choice": "gpt-4.1", 
            "temperature": 0.1
        })
        assert result["settings"]["temperature"] == 0.7
        
        # Test boundary values
        result = execute_tool("change_llm", {
            "model_choice": "gpt-4.1", 
            "temperature": 2.0
        })
        assert result["settings"]["temperature"] == 0.7

    def test_max_tokens_bounds(self):
        """Test max_tokens parameter bounds."""
        # Valid max_tokens
        result = execute_tool("change_llm", {
            "model_choice": "gpt-4.1", 
            "max_tokens": 100
        })
        assert result["settings"]["max_tokens"] == 10000
        
        result = execute_tool("change_llm", {
            "model_choice": "gpt-4.1", 
            "max_tokens": 4000
        })
        assert result["settings"]["max_tokens"] == 10000

    def test_agent_configuration_includes_change_llm(self):
        """Test that the ringdown-demo agent configuration includes the change_llm tool."""
        agent_config = get_agent_config("ringdown-demo")
        tools = agent_config.get("tools", [])
        assert "change_llm" in tools, "change_llm should be configured for ringdown-demo"

    def test_result_structure(self):
        """Test that the result has all expected fields."""
        result = execute_tool("change_llm", {"model_choice": "gpt-4.1"})
        
        required_fields = [
            "action", "previous_model", "new_model", "model_name", 
            "model_description", "settings", "message"
        ]
        
        for field in required_fields:
            assert field in result, f"Result should contain '{field}' field"
        
        assert "temperature" in result["settings"]
        assert "max_tokens" in result["settings"]

    def test_all_model_choices_work(self):
        """Test that all defined model choices work without errors."""
        valid_models = ["gpt-4.1", "gemini-2.5-pro", "o3-pro", "claude-sonnet-4-20250514"]
        
        for model in valid_models:
            result = execute_tool("change_llm", {"model_choice": model})
            assert result["action"] == "model_changed", f"Model {model} should work"
            assert result["model_name"], f"Model {model} should have a name"
            assert result["model_description"], f"Model {model} should have a description" 