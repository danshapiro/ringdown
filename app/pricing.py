"""Pricing helpers for estimating LLM and telephony costs."""

from __future__ import annotations

import logging
import os
from typing import Tuple

import litellm

logger = logging.getLogger(__name__)

_TWILIO_INBOUND_PER_MIN: float = float(
    os.getenv("RINGDOWN_TWILIO_INBOUND_PER_MIN", "0.0085")
)


def get_token_prices(model: str) -> Tuple[float, float]:
    """Return (input_cost, output_cost) USD per token for *model*."""

    try:
        result = litellm.cost_per_token(model=model)  # type: ignore[call-arg]
    except Exception as exc:  # noqa: BLE001 – pricing is best-effort telemetry
        logger.error("LiteLLM cost lookup failed for %s: %s", model, exc, exc_info=True)
        return 0.0, 0.0

    in_cost: float | None = None
    out_cost: float | None = None

    if isinstance(result, (list, tuple)) and len(result) == 2:
        in_cost, out_cost = (float(result[0]), float(result[1]))
    elif isinstance(result, dict):
        if result.get("input_cost_per_token") is not None:
            in_cost = float(result["input_cost_per_token"])
        if result.get("output_cost_per_token") is not None:
            out_cost = float(result["output_cost_per_token"])
    elif result is not None:
        logger.error(
            "LiteLLM cost lookup returned unsupported payload for model %s: %s",
            model,
            result,
        )
        return 0.0, 0.0

    missing_fields: list[str] = []
    if in_cost is None:
        missing_fields.append("input_cost_per_token")
    if out_cost is None:
        missing_fields.append("output_cost_per_token")

    if missing_fields:
        logger.error(
            "LiteLLM pricing response missing %s for model %s: %s",
            ", ".join(missing_fields),
            model,
            result,
        )
        if in_cost is None:
            in_cost = 0.0
        if out_cost is None:
            out_cost = 0.0

    return float(in_cost), float(out_cost)


def calculate_llm_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return total USD cost for a completion using LiteLLM's pricing data."""

    in_cost, out_cost = get_token_prices(model)
    total = (in_cost * prompt_tokens) + (out_cost * completion_tokens)
    return float(total)


def estimate_twilio_cost(minutes: float) -> float:
    """Return estimated Twilio telephony cost in USD."""

    return minutes * _TWILIO_INBOUND_PER_MIN


__all__ = [
    "get_token_prices",
    "calculate_llm_cost",
    "estimate_twilio_cost",
]
