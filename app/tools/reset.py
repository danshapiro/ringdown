"""Reset tool for Ringdown.

This tool resets conversations back to the configured welcome prompt so callers
hear the onboarding message again.
"""

import logging
import threading
from typing import Any, Dict

from pydantic import BaseModel

from ..tool_framework import register_tool

logger = logging.getLogger(__name__)

# Thread-local storage so the tool can honour whichever agent invoked it.
_agent_context = threading.local()


def set_agent_context(agent_config: Dict[str, Any] | None) -> None:
    """Capture the active agent configuration for subsequent tool calls."""

    _agent_context.config = agent_config


def _get_agent_context() -> Dict[str, Any] | None:
    return getattr(_agent_context, "config", None)

class ResetArgs(BaseModel):
    """Arguments for the reset tool.
    
    This tool doesn't require any arguments since it simply resets
    the conversation state.
    """
    
    confirm: bool = True  # Always True when the tool is called


@register_tool(
    name="reset",
    description="MANDATORY: Call this tool immediately when the user says 'reset', 'restart' or similar. Do NOT respond conversationally to reset requests. This resets the conversation to its initial state, clearing ALL message history permanently. This is a destructive action that cannot be undone.",
    param_model=ResetArgs,
    prompt="""
    MANDATORY TOOL USAGE: When the user says "RESET", "RESTART" or similar, you MUST call this tool immediately. Do not respond conversationally to reset requests.
    
    This tool resets the conversation back to the beginning, permanently deleting all conversation history. 
    
    CRITICAL REQUIREMENTS - You MUST call this tool when:
    1. The user explicitly says the word "RESET" or "RESTART" (or variations like "reset the conversation", "please restart", etc.)
    2. Never use this tool for simple questions, clarifications, or continuing conversations.
    3. Do not use it under any other circumstances.
    
    Examples requiring IMMEDIATE tool call:
    - User says: "reset the conversation" → CALL TOOL IMMEDIATELY
    - User says: "please reset our chat" → CALL TOOL IMMEDIATELY
    - User says: "i want to reset and start over" → CALL TOOL IMMEDIATELY
    - User says: "reset this" → CALL TOOL IMMEDIATELY
    - User says: "can you reset" → CALL TOOL IMMEDIATELY
    - User says: "reset" → CALL TOOL IMMEDIATELY
    
    Examples of invalid usage (DO NOT call tool):
    - User says: "start over" (without reset word)
    - User says: "let's begin again" (without reset word)
    - User says: "clear this up" (without reset word)
    - User says: "i'm confused" (without reset word)
    - User says: "new topic" (without reset word)
    """,
)
def reset_conversation(args: ResetArgs) -> dict[str, str]:
    """Reset the conversation to its initial state.
    
    This tool clears the conversation history and returns a welcome message,
    effectively restarting the conversation from the beginning.
    
    Args:
        args: ResetArgs containing confirmation (always True when called)
        
    Returns:
        A dictionary with the reset confirmation and welcome message
    """
    
    logger.info("Reset tool called - conversation will be reset to initial state")

    # The actual message array clearing will need to be handled by the calling code
    # since tools don't have direct access to the WebSocket scope.
    # We return a special response that the chat system can recognize.

    # Build reset marker using the active agent's welcome greeting when available.
    agent_cfg = _get_agent_context()
    tools = (agent_cfg.get("tools") if agent_cfg else None) or []

    if agent_cfg is None or "reset" not in {str(t).lower() for t in tools}:
        from app import settings as _settings

        config = _settings._load_config()
        defaults = config.get("defaults", {})
        greeting_raw = str(defaults.get("welcome_greeting", "Hello."))

        for agent_name in config.get("agents", {}):
            candidate = _settings.get_agent_config(agent_name)
            candidate_tools = {str(t).lower() for t in candidate.get("tools", [])}
            if "reset" in candidate_tools:
                agent_cfg = candidate
                greeting_raw = str(candidate.get("welcome_greeting", greeting_raw))
                break
        else:
            agent_cfg = defaults
        greeting = greeting_raw.strip().rstrip(".")
        return {
            "action": "reset_conversation",
            "message": f"Reset. {greeting}",
            "status": "Conversation has been reset to the starting state.",
        }

    greeting_raw = str(agent_cfg.get("welcome_greeting", "Hello.")).strip()
    greeting = greeting_raw.rstrip(".")

    return {
        "action": "reset_conversation",
        "message": f"Reset. {greeting}",
        "status": "Conversation has been reset to the starting state."
    }
