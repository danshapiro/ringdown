"""
Model capabilities registry for handling provider-specific API differences.

This module tracks which features are supported by different LLM models,
allowing the application to gracefully handle incompatibilities like
reasoning_effort not being usable with function calling on certain models.
"""

from dataclasses import dataclass, field
import re


@dataclass
class ModelCapabilities:
    """Capabilities and constraints for a specific model family."""

    supports_reasoning_effort: bool = False
    reasoning_effort_with_tools: bool = True
    supports_parallel_tool_calls: bool = True
    max_tool_id_length: int | None = None

    supported_reasoning_levels: tuple[str, ...] = field(
        default_factory=lambda: ("none", "minimal", "low", "medium", "high", "xhigh")
    )


_MODELS: dict[str, ModelCapabilities] = {
    "gpt-5.4": ModelCapabilities(
        supports_reasoning_effort=True,
        reasoning_effort_with_tools=False,
        supports_parallel_tool_calls=True,
        max_tool_id_length=40,
    ),
    "gpt-5.4-mini": ModelCapabilities(
        supports_reasoning_effort=True,
        reasoning_effort_with_tools=False,
        supports_parallel_tool_calls=True,
        max_tool_id_length=40,
    ),
    "gpt-5.4-nano": ModelCapabilities(
        supports_reasoning_effort=True,
        reasoning_effort_with_tools=False,
        supports_parallel_tool_calls=True,
        max_tool_id_length=40,
    ),
    "gpt-5": ModelCapabilities(
        supports_reasoning_effort=True,
        reasoning_effort_with_tools=True,
        supports_parallel_tool_calls=True,
        max_tool_id_length=40,
    ),
    "gpt-5-mini": ModelCapabilities(
        supports_reasoning_effort=True,
        reasoning_effort_with_tools=True,
        supports_parallel_tool_calls=True,
        max_tool_id_length=40,
    ),
    "gpt-5-instant": ModelCapabilities(
        supports_reasoning_effort=True,
        reasoning_effort_with_tools=True,
        supports_parallel_tool_calls=True,
        max_tool_id_length=40,
    ),
    "gpt-5-high": ModelCapabilities(
        supports_reasoning_effort=True,
        reasoning_effort_with_tools=True,
        supports_parallel_tool_calls=True,
        max_tool_id_length=40,
    ),
    "claude-opus": ModelCapabilities(
        supports_reasoning_effort=False,
        reasoning_effort_with_tools=True,
        supports_parallel_tool_calls=True,
    ),
    "claude-sonnet": ModelCapabilities(
        supports_reasoning_effort=False,
        reasoning_effort_with_tools=True,
        supports_parallel_tool_calls=True,
    ),
    "claude-haiku": ModelCapabilities(
        supports_reasoning_effort=False,
        reasoning_effort_with_tools=True,
        supports_parallel_tool_calls=True,
    ),
    "gemini": ModelCapabilities(
        supports_reasoning_effort=False,
        reasoning_effort_with_tools=True,
        supports_parallel_tool_calls=True,
    ),
}


def _normalize_model_name(model: str) -> str:
    """Strip provider prefix and normalize model name for lookup."""
    if "/" in model:
        model = model.split("/")[-1]
    return model.lower().strip()


def _find_capabilities(model: str) -> ModelCapabilities | None:
    """Find capabilities for a model by matching against known patterns."""
    normalized = _normalize_model_name(model)

    if normalized in _MODELS:
        return _MODELS[normalized]

    for pattern, caps in _MODELS.items():
        if re.match(f"^{pattern}", normalized) or re.match(f".*{pattern}.*", normalized):
            return caps

    return None


def supports_reasoning_effort(model: str) -> bool:
    """Check if a model supports the reasoning_effort parameter."""
    caps = _find_capabilities(model)
    return caps.supports_reasoning_effort if caps else False


def can_use_reasoning_effort_with_tools(model: str) -> bool:
    """Check if reasoning_effort can be used when tools are present."""
    caps = _find_capabilities(model)
    return caps.reasoning_effort_with_tools if caps else True


def should_include_reasoning_effort(model: str, has_tools: bool, effort_level: str | None) -> bool:
    """
    Determine whether to include reasoning_effort in an API request.

    Args:
        model: The model identifier (e.g., "gpt-5.4", "openai/gpt-5.4-mini")
        has_tools: Whether the request includes function tools
        effort_level: The configured reasoning effort level (or None)

    Returns:
        True if reasoning_effort should be included in the request
    """
    if not effort_level:
        return False

    if not supports_reasoning_effort(model):
        return False

    if has_tools and not can_use_reasoning_effort_with_tools(model):
        return False

    return True


def get_max_tool_id_length(model: str) -> int | None:
    """Get the maximum allowed tool ID length for a model (None = no limit)."""
    caps = _find_capabilities(model)
    return caps.max_tool_id_length if caps else None
