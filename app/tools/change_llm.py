"""Change LLM tool for Ringdown.

This tool allows users to switch between different permitted LLM models
during conversation, changing the underlying AI model being used.
"""

import logging
import threading
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..tool_framework import register_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent context – populated by tool_framework.set_agent_context
# ---------------------------------------------------------------------------

_agent_context = threading.local()

def set_agent_context(agent_config: dict[str, Any] | None) -> None:
    """Store the current agent configuration in thread-local storage.

    This is called automatically by the tool framework before the tool
    executes, allowing `change_llm` to read the active model when
    constructing its response.
    """
    _agent_context.config = agent_config


def _get_agent_context() -> dict[str, Any] | None:
    """Return the agent configuration for the current thread, if any."""
    return getattr(_agent_context, "config", None)


_ALIAS_MAP: dict[str, str] = {
    "gpt-5": "gpt-5",
    "gpt5": "gpt-5",
    "gpt-5-mini": "gpt-5-mini",
    "gpt5-mini": "gpt-5-mini",
    "gpt-5-instant": "gpt-5-instant",
    "gpt5-instant": "gpt-5-instant",
    "gpt-5-high": "gpt-5-high",
    "gpt5-high": "gpt-5-high",
    "gpt-5-high-thinking": "gpt-5-high",
    "gemini-pro": "gemini-pro",
    "geminipro": "gemini-pro",
    "gemini": "gemini-flash",
    "gemini-flash": "gemini-flash",
    "haiku": "haiku",
    "sonnet": "sonnet",
}

_MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    "gpt-5": {
        "model_id": "gpt-5",
        "label": "gpt-5",
        "temperature": 1.0,
        "max_tokens": 16000,
        "thinking_level": "medium",
    },
    "gpt-5-high": {
        "model_id": "gpt-5",
        "label": "gpt-5-high",
        "temperature": 1.0,
        "max_tokens": 16000,
        "thinking_level": "high",
    },
    "gpt-5-mini": {
        "model_id": "gpt-5-mini",
        "label": "gpt-5-mini",
        "temperature": 1.0,
        "max_tokens": 12000,
    },
    "gpt-5-instant": {
        "model_id": "gpt-5-instant",
        "label": "gpt-5-instant",
        "temperature": 1.0,
        "max_tokens": 8000,
        "thinking_level": "low",
    },
    "gemini-pro": {
        "model_id": "gemini/gemini-2.5-pro",
        "label": "gemini-pro",
        "temperature": 1.0,
        "max_tokens": 8192,
    },
    "gemini-flash": {
        "model_id": "gemini/gemini-2.5-flash",
        "label": "gemini-flash",
        "temperature": 1.0,
        "max_tokens": 8192,
    },
    "haiku": {
        "model_id": "claude-haiku-4-5",
        "label": "haiku",
        "temperature": 1.0,
        "max_tokens": 8000,
    },
    "sonnet": {
        "model_id": "claude-sonnet-4-20250514",
        "label": "sonnet",
        "temperature": 1.0,
        "max_tokens": 12000,
    },
}


class ChangeLLMArgs(BaseModel):
    """Arguments for the change LLM tool."""

    model_choice: str = Field(
        description=(
            "Accepted inputs: gpt-5, gpt-5-mini, gpt-5-instant, gpt-5-high, "
            "gpt-5-high-thinking, Gemini Pro, Gemini Flash, Gemini, Haiku, "
            "Sonnet."
        )
    )

    @field_validator("model_choice")
    @classmethod
    def normalise_model_choice(cls, value: str) -> str:
        cleaned = value.strip().lower().replace("_", "-")
        cleaned = "-".join(part for part in cleaned.replace(" ", "-").split("-") if part)
        if cleaned not in _ALIAS_MAP:
            raise ValueError(f"Unsupported model choice: {value}")
        return _ALIAS_MAP[cleaned]


@register_tool(
    name="change_llm",
    description=(
        "Change the underlying LLM model for this conversation.\n\n"
        "Allowed inputs: gpt-5, gpt-5-mini, gpt-5-instant, gpt-5-high, "
        "gpt-5-high-thinking, Gemini Pro, Gemini Flash, Gemini, Haiku, Sonnet.\n\n"
        "ONLY use this tool if the user explicitly requests to change or switch "
        "the AI model being used. This is a significant change that affects all "
        "subsequent responses in the conversation."
    ),
    param_model=ChangeLLMArgs,
)

def change_llm(args: ChangeLLMArgs) -> dict[str, Any]:
    """Change the underlying LLM model for the conversation.
    
    Args:
        args: ChangeLLMArgs containing model choice
        
    Returns:
        Dict containing the model change confirmation and new settings
    """

    logger.info(f"User requested LLM model change to: {args.model_choice}")

    model_choice = args.model_choice

    config = _MODEL_CONFIGS[model_choice]

    # Determine the model currently in use so we can include it in the response
    agent_ctx = _get_agent_context()
    is_mapping = isinstance(agent_ctx, dict)
    previous_model = agent_ctx.get("model") if is_mapping and agent_ctx.get("model") else "unknown"

    result = {
        "action": "model_changed",
        "previous_model": previous_model,
        "new_model": config["model_id"],
        "model_label": config["label"],
        "settings": {
            "temperature": config["temperature"],
            "max_tokens": config["max_tokens"],
        },
        "message": (
            f"Switched to {config['label']}.\n\n"
        ),
    }

    thinking_level = config.get("thinking_level")
    if thinking_level:
        result["settings"]["thinking_level"] = thinking_level

    logger.info(
        "Model change completed: %s with temp=%s",
        model_choice,
        config["temperature"],
    )

    return result
