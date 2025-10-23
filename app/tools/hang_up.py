"""Hang up tool for Ringdown.

Provides an explicit tool that terminates the active Twilio call when the
caller requests it.  Returning a structured payload allows the chat loop to
stop streaming audio and instruct the WebSocket layer to disconnect cleanly.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, cast

from pydantic import BaseModel, Field

from ..settings import get_env
from ..tool_framework import register_tool

logger = logging.getLogger(__name__)


_call_context = threading.local()


def set_call_context(call_ctx: dict[str, Any] | None) -> None:
    """Store the active call metadata for the current thread."""

    _call_context.data = call_ctx


def _get_call_context() -> dict[str, Any] | None:
    return getattr(_call_context, "data", None)


def _complete_call_via_twilio(account_sid: str, auth_token: str, call_sid: str) -> None:
    """Mark the Twilio call identified by *call_sid* as completed."""

    from twilio.rest import Client  # local import to keep module deps light

    client = Client(account_sid, auth_token)
    client.calls(call_sid).update(status="completed")


class HangUpArgs(BaseModel):
    """Arguments for the hang up tool."""

    confirm: bool = Field(
        default=True,
        description="Set to true when the caller explicitly asks to hang up.",
    )


@register_tool(
    name="hang_up",
    description=(
        "End the current Twilio call immediately when the caller clearly requests a hang up, "
        "disconnect, or termination."
    ),
    param_model=HangUpArgs,
    prompt=(
        "MANDATORY TOOL USAGE: When the caller asks you to hang up, end the call, or disconnect, "
        "call this tool immediately. Do not use it for casual goodbyes or ambiguous language."
    ),
)
def hang_up_call(args: BaseModel) -> dict[str, Any]:
    """Terminate the active Twilio call and return a hang-up marker."""

    if not isinstance(args, HangUpArgs):
        raise TypeError("hang_up_call received unexpected argument type")
    hang_args = cast(HangUpArgs, args)

    if not hang_args.confirm:
        logger.info("Hang up tool invoked without confirmation; skipping hang up request")
        return {
            "action": "hangup_call",
            "status": "not_confirmed",
            "message": "Hang up cancelled.",
        }

    call_ctx = _get_call_context() or {}
    call_sid = call_ctx.get("call_sid")

    if not call_sid:
        logger.warning("Hang up tool invoked but call_sid is unavailable")
        return {
            "action": "hangup_call",
            "status": "missing_call_sid",
            "message": "Hanging up now.",
        }

    env = get_env()
    account_sid = env.twilio_account_sid
    auth_token = env.twilio_auth_token

    status = "success"
    try:
        if not account_sid:
            raise RuntimeError("TWILIO_ACCOUNT_SID is not configured")
        _complete_call_via_twilio(account_sid, auth_token, call_sid)
        logger.info("Requested Twilio hang up for call %s", call_sid)
    except Exception as exc:  # noqa: BLE001 – log and fall back to socket closure
        status = "twilio_failure"
        logger.error("Failed to hang up call %s via Twilio: %s", call_sid, exc, exc_info=True)

    return {
        "action": "hangup_call",
        "status": status,
        "message": "Hanging up now.",
        "call_sid": call_sid,
    }
