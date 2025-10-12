"""Change LLM tool for Ringdown.

This tool allows users to switch between different permitted LLM models
during conversation, changing the underlying AI model being used.
"""

import logging
import threading
from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel, Field

from ..tool_framework import register_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent context – populated by tool_framework.set_agent_context
# ---------------------------------------------------------------------------

_agent_context = threading.local()

def set_agent_context(agent_config: Dict[str, Any] | None) -> None:
    """Store the current agent configuration in thread-local storage.

    This is called automatically by the tool framework before the tool
    executes, allowing `change_llm` to read the active model when
    constructing its response.
    """
    _agent_context.config = agent_config


def _get_agent_context() -> Dict[str, Any] | None:
    """Return the agent configuration for the current thread, if any."""
    return getattr(_agent_context, "config", None)


class PermittedModels(str, Enum):
    """Permitted LLM models that users can switch to."""

    OPENAI_4_1 = "gpt-4.1"
    OPENAI_4_1_MINI = "gpt-4.1-mini"
    GEMINI_PRO_2_5 = "gemini-2.5-pro"
    OPENAI_O3_PRO = "o3-pro"
    SONNET_4 = "claude-sonnet-4-20250514"


class ChangeLLMArgs(BaseModel):
    """Arguments for the change LLM tool."""

    model_choice: PermittedModels = Field(
        description="The LLM model to switch to. Choose from the available options."
    )


@register_tool(
    name="change_llm",
    description="""Change the underlying LLM model for this conversation. 
    
Available models:
- gpt-4.1: OpenAI's latest GPT-4.1 model with enhanced capabilities
- gpt-4.1-mini: Cost-optimized GPT-4.1 variant for lower latency/cost
- gemini-2.5-pro: Google's Gemini 2.5 Pro with advanced reasoning  
- o3-pro: OpenAI's advanced reasoning model (o3-pro)
- claude-sonnet-4-20250514: Anthropic's Claude 4 Sonnet (current version)

ONLY use this tool if the user explicitly requests to change or switch the AI model being used. 
This is a significant change that affects all subsequent responses in the conversation.""",
    param_model=ChangeLLMArgs,
)

def change_llm(args: ChangeLLMArgs) -> Dict[str, Any]:
    """Change the underlying LLM model for the conversation.
    
    Args:
        args: ChangeLLMArgs containing model choice
        
    Returns:
        Dict containing the model change confirmation and new settings
    """

    logger.info(f"User requested LLM model change to: {args.model_choice}")

    model_choice = args.model_choice.value

    model_configs = {
        "gpt-4.1": {
            "name": "OpenAI GPT-4.1",
            "description": "Latest GPT-4.1 with enhanced reasoning and coding capabilities",
            "temp": 0.7,
            "max_tokens": 10000,
        },
        "gpt-4.1-mini": {
            "name": "OpenAI GPT-4.1 Mini",
            "description": "Cost-optimized GPT-4.1 variant with reduced compute demand",
            "temp": 0.7,
            "max_tokens": 10000,
        },
        "gemini-2.5-pro": {
            "name": "Google Gemini Pro 2.5",
            "description": "Advanced multimodal model with superior reasoning",
            "temp": 0.7,
            "max_tokens": 10000,
        },
        "o3-pro": {
            "name": "OpenAI o3-pro",
            "description": "Advanced reasoning model optimized for complex problems",
            "temp": 1.0,
            "max_tokens": 10000,
        },
        "claude-sonnet-4-20250514": {
            "name": "Claude 4 Sonnet",
            "description": "Anthropic's cutting-edge Claude 4 Sonnet model (2025-05-14)",
            "temp": 1.0,
            "max_tokens": 10000,
        },
    }

    config = model_configs[model_choice]

    # Determine the model currently in use so we can include it in the response
    agent_ctx = _get_agent_context()
    previous_model = (
        agent_ctx.get("model") if isinstance(agent_ctx, dict) and agent_ctx.get("model") else "unknown"
    )

    result = {
        "action": "model_changed",
        "previous_model": previous_model,
        "new_model": model_choice,
        "model_name": config["name"],
        "model_description": config["description"],
        "settings": {
            "temperature": config["temp"],
            "max_tokens": config["max_tokens"],
        },
        "message": (
            f"Switched to {config['name']}.\n\n"
        ),
    }

    logger.info(
        "Model change completed: %s with temp=%s",
        model_choice,
        config["temp"],
    )

    return result
