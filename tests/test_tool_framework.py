#!/usr/bin/env python3
"""Unit tests for app.tool_framework."""

from __future__ import annotations

import json
import types
from typing import Any, Dict, List

import pytest
from pydantic import BaseModel, ValidationError

from app import tool_framework as tf
from app import settings


# ---------------------------------------------------------------------------
# Helper tool for tests
# ---------------------------------------------------------------------------

class DummyArgs(BaseModel):
    x: int
    y: int | None = None


@tf.register_tool(
    name="dummy_add", 
    description="Add x + y", 
    param_model=DummyArgs,
    prompt="## dummy_add\nAdd two numbers together. Use for math operations."
)
def _dummy(args: DummyArgs):
    return (args.x or 0) + (args.y or 0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_registry_contains_dummy():
    assert "dummy_add" in tf.TOOL_REGISTRY


def test_schema_generation():
    schema = tf.get_tool_schema("dummy_add")
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "dummy_add"
    # parameters should include x
    params = schema["function"]["parameters"]
    assert "x" in params["properties"]


def test_execute_tool_success():
    res = tf.execute_tool("dummy_add", {"x": 2, "y": 3})
    assert res == 5


def test_execute_tool_validation_error():
    with pytest.raises(ValidationError):
        tf.execute_tool("dummy_add", {"x": "not-int"})


def test_get_tools_for_agent():
    agent_cfg = {"tools": ["dummy_add"]}
    tools = tf.get_tools_for_agent(agent_cfg)
    assert isinstance(tools, list) and tools
    assert tools[0]["function"]["name"] == "dummy_add"


def test_list_tools():
    names = tf.list_tools()
    assert "dummy_add" in names


def test_tool_prompt_functionality():
    """Test that tool prompts are correctly stored and retrieved."""
    
    # Test individual tool prompt retrieval
    prompt = tf.get_tool_prompt("dummy_add")
    assert "dummy_add" in prompt
    assert "Add two numbers together" in prompt
    
    # Test missing tool
    with pytest.raises(ValueError, match="Tool 'nonexistent' not found"):
        tf.get_tool_prompt("nonexistent")


def test_programmatic_tool_prompts():
    """Test that programmatic tool prompts are collected correctly."""
    
    prompts = settings.get_programmatic_tool_prompts()
    
    # Should include our dummy tool
    assert "dummy_add" in prompts
    assert "Add two numbers together" in prompts["dummy_add"]
    
    # Should include the real tools
    assert "TavilySearch" in prompts
    assert "TavilyExtract" in prompts
    assert "SendEmail" in prompts
    
    # Verify tool-specific content
    assert "tavily_search" in prompts["TavilySearch"]
    assert "tavily_extract" in prompts["TavilyExtract"]
    assert "send_email" in prompts["SendEmail"]


def test_build_tool_prompts_for_agent():
    """Test that tool prompts are correctly built for specific agent configurations."""
    
    tool_header = "### Test Header\nGeneral tool usage instructions."
    
    # Test with single tool
    single_tool_prompts = settings.build_tool_prompts_for_agent(["dummy_add"], tool_header)
    assert "Test Header" in single_tool_prompts
    assert "dummy_add" in single_tool_prompts
    assert "## tavily_search" not in single_tool_prompts
    
    # Test with multiple tools
    multi_tool_prompts = settings.build_tool_prompts_for_agent(
        ["TavilySearch", "SendEmail"], tool_header
    )
    assert "Test Header" in multi_tool_prompts
    assert "## tavily_search" in multi_tool_prompts
    assert "## send_email" in multi_tool_prompts
    assert "## tavily_extract" not in multi_tool_prompts
    
    # Test with empty tools list
    empty_prompts = settings.build_tool_prompts_for_agent([], tool_header)
    assert "Test Header" in empty_prompts
    assert "## tavily_search" not in empty_prompts


def test_agent_tool_prompts_interpolation():
    """Test that {ToolPrompts} placeholder works correctly for different agents."""
    
    # Clear cache for fresh test
    settings._load_config.cache_clear()
    
    # Get which tools actually have prompts defined
    available_prompts = settings.get_programmatic_tool_prompts()
    tools_with_prompts = {name for name, prompt in available_prompts.items() if prompt}
    
    # Map tool names to their expected prompt indicators
    tool_prompt_indicators = {
        "TavilySearch": "## tavily_search",
        "TavilyExtract": "## tavily_extract", 
        "SendEmail": "## send_email",
        "reset": "MANDATORY TOOL USAGE:"  # reset has a different format
    }
    
    # Test agents that we know exist in config.yaml
    test_agents = ["unknown-caller", "ringdown-demo", "sandbox-agent"]
    
    for agent_name in test_agents:
        agent_config = settings.get_agent_config(agent_name)
        actual_tools = set(agent_config.get("tools", []))
        prompt = agent_config.get("prompt", "")
        
        # Skip if no tools or no prompt (some agents might not have prompts)
        if not actual_tools or not prompt:
            continue
            
        # Verify tool header is present if there are tools
        if actual_tools:
            assert "# Tool Usage" in prompt, f"{agent_name} missing tool header"
        
        # Check that tools with prompts are documented when enabled
        tools_that_should_be_in_prompt = actual_tools & tools_with_prompts
        for tool in tools_that_should_be_in_prompt:
            expected_indicator = tool_prompt_indicators.get(tool)
            if expected_indicator:
                assert expected_indicator in prompt, \
                    f"{agent_name} missing prompt section for {tool}: {expected_indicator}"
        
        # Check that tools with prompts are NOT documented when not enabled  
        tools_that_should_not_be_in_prompt = tools_with_prompts - actual_tools
        for tool in tools_that_should_not_be_in_prompt:
            expected_indicator = tool_prompt_indicators.get(tool)
            if expected_indicator:
                assert expected_indicator not in prompt, \
                    f"{agent_name} has unexpected prompt section for excluded tool {tool}: {expected_indicator}"


def test_tool_response_truncation():
    """Test that tool responses are properly truncated at 200k characters."""
    import json
    
    # Test small response - should not be truncated
    small_result = {"message": "Hello world"}
    truncated = tf._truncate_tool_response(small_result)
    assert truncated == small_result
    
    # Test large string - should be truncated
    large_string = "x" * 250000
    truncated = tf._truncate_tool_response(large_string)
    assert len(truncated) < len(large_string)
    assert "more characters removed" in truncated
    assert "be sure to mention this to the user" in truncated
    
    # Test large dict - should preserve structure
    large_dict = {
        "data": "x" * 250000,
        "status": "success",
        "metadata": {"type": "test"}
    }
    truncated = tf._truncate_tool_response(large_dict)
    assert isinstance(truncated, dict)
    assert "_truncation_notice" in truncated
    assert "more characters removed" in truncated["_truncation_notice"]
    
    # Test large list - should preserve structure
    large_list = ["x" * 50000 for _ in range(10)]  # 500k characters total
    truncated = tf._truncate_tool_response(large_list)
    assert isinstance(truncated, list)
    assert any("more characters removed" in str(item) for item in truncated)
    assert len(truncated) < len(large_list)
    
    # Test non-serializable object - should handle gracefully
    class NonSerializable:
        pass
    
    non_serializable = NonSerializable()
    truncated = tf._truncate_tool_response(non_serializable)
    assert truncated is non_serializable
    
    # Test that truncated response is actually under 200k when JSON serialized
    huge_dict = {
        "data": "x" * 1000000,  # 1MB of data
        "more_data": "y" * 1000000,  # Another 1MB
        "metadata": {"size": "huge"}
    }
    truncated = tf._truncate_tool_response(huge_dict)
    truncated_json = json.dumps(truncated)
    assert len(truncated_json) <= 200000


# ---------------------------------------------------------------------------
# Cross-Provider Compatibility Tests
# ---------------------------------------------------------------------------

def test_json_schema_draft_2020_12_compatibility():
    """Test that schemas use JSON Schema draft 2020-12 format for cross-provider compatibility."""
    
    # Test with our dummy tool
    schema = tf.get_tool_schema("dummy_add")
    
    # Extract parameters schema
    params = schema["function"]["parameters"]
    
    # Check for JSON Schema draft 2020-12 identifier
    assert "$schema" in params, "Schema missing $schema identifier"
    assert "2020-12" in params["$schema"], f"Incorrect schema draft: {params['$schema']}"
    
    # Verify required OpenAI function calling fields
    assert "type" in params, "Schema missing 'type' field"
    assert params["type"] == "object", f"Expected type 'object', got '{params['type']}'"
    assert "properties" in params, "Schema missing 'properties' field"


def test_no_legacy_schema_refs():
    """Test that schemas don't use legacy #/components/schemas/ format."""
    
    # Test multiple tools to ensure consistency
    test_tools = ["dummy_add"]
    if "SendEmail" in tf.TOOL_REGISTRY:
        test_tools.append("SendEmail")
    
    for tool_name in test_tools:
        schema = tf.get_tool_schema(tool_name)
        params = schema["function"]["parameters"]
        
        # Check for legacy format
        schema_json = json.dumps(params)
        assert "#/components/schemas/" not in schema_json, \
            f"Tool {tool_name} uses legacy schema refs (incompatible with Anthropic)"


def test_proper_defs_format():
    """Test that $ref values use correct #/$defs/ format when present."""
    
    # Create a tool with nested models to test $defs references
    class NestedModel(BaseModel):
        value: str
        count: int = 1
    
    class ComplexArgs(BaseModel):
        name: str
        nested: NestedModel
        items: list[NestedModel] = []
    
    @tf.register_tool(
        name="test_complex_refs",
        description="Test tool with complex nested references",
        param_model=ComplexArgs
    )
    def _test_complex(args: ComplexArgs):
        return {"processed": args.name}
    
    try:
        schema = tf.get_tool_schema("test_complex_refs")
        params = schema["function"]["parameters"]
        
        if "$defs" in params:
            # Find all $ref values recursively
            def find_refs(obj):
                refs = []
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if key == "$ref" and isinstance(value, str):
                            refs.append(value)
                        else:
                            refs.extend(find_refs(value))
                elif isinstance(obj, list):
                    for item in obj:
                        refs.extend(find_refs(item))
                return refs
            
            all_refs = find_refs(params)
            for ref in all_refs:
                assert ref.startswith("#/$defs/"), \
                    f"Invalid $ref format: {ref} (should start with #/$defs/)"
        
    finally:
        # Clean up test tool
        if "test_complex_refs" in tf.TOOL_REGISTRY:
            del tf.TOOL_REGISTRY["test_complex_refs"]


# ---------------------------------------------------------------------------
# Cross-provider compatibility validation functions (test-time only)
# ---------------------------------------------------------------------------

def validate_tool_compatibility(name: str, target_providers: List[str] = None) -> Dict[str, Any]:
    """Validate that a tool's schema is compatible with the specified providers.
    
    Args:
        name: Tool name to validate
        target_providers: List of providers to validate against. 
                         Options: ['openai', 'anthropic', 'gemini']
                         Defaults to all three providers.
    
    Returns:
        Dict with compatibility results and any issues found.
    """
    if target_providers is None:
        target_providers = ['openai', 'anthropic', 'gemini']
    
    if name not in tf.TOOL_REGISTRY:
        raise ValueError(f"Tool '{name}' not found")
    
    spec = tf.TOOL_REGISTRY[name]
    schema = spec.openai_schema()
    
    results = {
        "tool_name": name,
        "compatible": True,
        "providers": {},
        "issues": [],
        "schema": schema
    }
    
    for provider in target_providers:
        provider_result = _validate_provider_compatibility(schema, provider)
        results["providers"][provider] = provider_result
        if not provider_result["compatible"]:
            results["compatible"] = False
            results["issues"].extend([f"{provider}: {issue}" for issue in provider_result["issues"]])
    
    return results

def _validate_provider_compatibility(schema: Dict[str, Any], provider: str) -> Dict[str, bool | List[str]]:
    """Validate schema compatibility for a specific provider."""
    issues = []
    
    # Extract the actual parameters schema from the OpenAI function format
    function_schema = schema.get("function", {})
    params_schema = function_schema.get("parameters", {})
    
    if provider == "anthropic":
        # Anthropic requires JSON Schema draft 2020-12 format
        if "$schema" not in params_schema:
            issues.append("Missing $schema identifier for draft 2020-12")
        elif "2020-12" not in params_schema["$schema"]:
            issues.append(f"Incorrect $schema version: {params_schema['$schema']} (should include 2020-12)")
        
        # Check for correct $ref format if present
        if "$defs" in params_schema:
            _check_anthropic_refs(params_schema, issues)
        
        # Check for basic required fields
        if "type" not in params_schema:
            issues.append("Missing 'type' field in parameters schema")
        elif params_schema["type"] != "object":
            issues.append(f"Parameters type should be 'object', got '{params_schema['type']}'")
            
        if "properties" not in params_schema:
            issues.append("Missing 'properties' field in parameters schema")
    
    elif provider == "openai":
        # OpenAI is generally compatible with both old and new formats
        # Check for basic required structure
        if "type" not in params_schema:
            issues.append("Missing 'type' field in parameters")
        if "properties" not in params_schema:
            issues.append("Missing 'properties' field in parameters")
    
    elif provider == "gemini":
        # Gemini works via LiteLLM translation, generally compatible
        # Check for basic required structure
        if "properties" not in params_schema:
            issues.append("Missing 'properties' field in parameters")
        if "type" not in params_schema:
            issues.append("Missing 'type' field in parameters")
    
    return {
        "compatible": len(issues) == 0,
        "issues": issues
    }

def _check_anthropic_refs(obj: Any, issues: List[str]) -> None:
    """Check for Anthropic-compatible $ref formats."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "$ref" and isinstance(value, str):
                if value.startswith("#/components/schemas/"):
                    issues.append(f"Legacy $ref format found: {value} (should use #/$defs/)")
            else:
                _check_anthropic_refs(value, issues)
    elif isinstance(obj, list):
        for item in obj:
            _check_anthropic_refs(item, issues)


def test_validate_tool_compatibility_function():
    """Test the validate_tool_compatibility function for cross-provider validation."""
    
    # Test with existing tool
    result = validate_tool_compatibility("dummy_add")
    
    # Check structure
    assert "compatible" in result
    assert "providers" in result
    assert "issues" in result
    
    # Should be compatible
    assert result["compatible"] is True, f"dummy_add should be compatible: {result['issues']}"
    
    # Check provider-specific results
    providers = result["providers"]
    assert "openai" in providers
    assert "anthropic" in providers
    assert "gemini" in providers
    
    for provider, provider_result in providers.items():
        assert "compatible" in provider_result
        assert "issues" in provider_result
        assert provider_result["compatible"] is True, \
            f"dummy_add should be compatible with {provider}: {provider_result['issues']}"


def test_validate_tool_compatibility_with_specific_providers():
    """Test validate_tool_compatibility with specific provider list."""
    
    # Test with just Anthropic (most strict requirements)
    result = validate_tool_compatibility("dummy_add", ["anthropic"])
    
    assert result["compatible"] is True
    assert "anthropic" in result["providers"]
    assert len(result["providers"]) == 1
    
    # Test with multiple specific providers
    result = validate_tool_compatibility("dummy_add", ["openai", "gemini"])
    
    assert "openai" in result["providers"]
    assert "gemini" in result["providers"]
    assert "anthropic" not in result["providers"]


def test_validate_tool_compatibility_nonexistent_tool():
    """Test validate_tool_compatibility with nonexistent tool."""
    
    with pytest.raises(ValueError, match="Tool 'nonexistent_tool' not found"):
        validate_tool_compatibility("nonexistent_tool")


def test_existing_tools_cross_provider_compatibility():
    """Test that all existing tools are cross-provider compatible."""
    
    # Get list of real tools (excluding test tools)
    all_tools = tf.list_tools()
    real_tools = [t for t in all_tools if not t.startswith("dummy") and not t.startswith("test")]
    
    # Skip if no real tools are available (CI environment)
    if not real_tools:
        pytest.skip("No real tools available for testing")
    
    # Test a sample of real tools for compatibility
    sample_tools = real_tools[:5]  # Test first 5 tools to keep test fast
    
    for tool_name in sample_tools:
        result = validate_tool_compatibility(tool_name)
        
        # Each tool should be compatible with all providers
        assert result["compatible"], \
            f"Tool {tool_name} has compatibility issues: {result['issues']}"
        
        # Check that all providers are compatible
        for provider in ["openai", "anthropic", "gemini"]:
            provider_result = result["providers"][provider]
            assert provider_result["compatible"], \
                f"Tool {tool_name} incompatible with {provider}: {provider_result['issues']}"


def test_schema_automatic_optimization():
    """Test that schemas are automatically optimized for compatibility."""
    
    schema = tf.get_tool_schema("dummy_add")
    params = schema["function"]["parameters"]
    
    # Should have the required compatibility features
    assert "$schema" in params
    assert "https://json-schema.org/draft/2020-12/schema" == params["$schema"]
    assert params["type"] == "object"
    assert "properties" in params
    
    # Should not have problematic features
    schema_json = json.dumps(params)
    assert "#/components/schemas/" not in schema_json


def test_provider_specific_validation_logic():
    """Test the internal provider validation logic."""
    
    # Get a valid schema to test with
    schema = tf.get_tool_schema("dummy_add")
    
    # Test each provider's validation logic
    providers = ["openai", "anthropic", "gemini"]
    
    for provider in providers:
        result = _validate_provider_compatibility(schema, provider)
        
        assert "compatible" in result
        assert "issues" in result
        assert isinstance(result["issues"], list)
        
        # Should be compatible with our well-formed schema
        assert result["compatible"], \
            f"Well-formed schema should be compatible with {provider}: {result['issues']}"


def test_cross_provider_future_proofing():
    """Test that the cross-provider system is extensible for future providers."""
    
    # Test that we can add a new provider to validation
    result = validate_tool_compatibility("dummy_add", ["openai", "anthropic", "future_provider"])
    
    # Should handle unknown providers gracefully
    assert "openai" in result["providers"]
    assert "anthropic" in result["providers"]
    # future_provider might not be implemented yet, but shouldn't crash 