"""Twilio webhook endpoints for Ringdown."""

from __future__ import annotations

from urllib.parse import parse_qsl

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from app.audio import provider_supports_speed, rate_to_speed_factor
from app.call_state import store_call
from app.logging_utils import logger
from app.memory import load_state
from app.settings import get_agent_for_number
from app.validators import validator as _twilio_validator

router = APIRouter()


@router.get("/twiml", response_class=Response)
async def twiml(
    request: Request,
    x_twilio_signature: str = Header(..., alias="X-Twilio-Signature"),
) -> Response:
    """Generate TwiML dynamically using config.yaml settings.

    Point your Twilio number's Voice webhook at this endpoint instead of a TwiML Bin.
    """

    # -------- Twilio signature validation --------
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.hostname)
    # Reconstruct URL with original scheme (likely https) for Cloud Run / proxies
    full_url = f"{proto}://{host}{request.url.path}"
    if request.url.query:
        full_url += f"?{request.url.query}"

    url_no_query = f"{proto}://{host}{request.url.path}"

    query_params = dict(parse_qsl(request.url.query, keep_blank_values=True))
    body_params: dict[str, str] = {}

    if request.method.upper() == "POST":
        content_type = request.headers.get("content-type", "")
        try:
            if "application/x-www-form-urlencoded" in content_type:
                form = await request.form()
                body_params = dict(form.multi_items())
            else:
                raw_body = (await request.body()).decode("utf-8", "ignore")
                if raw_body:
                    body_params = dict(parse_qsl(raw_body, keep_blank_values=True))
        except Exception as exc:  # noqa: BLE001 – validation should remain best-effort
            logger.warning("Failed to parse POST body for signature validation: %s", exc)

    merged_params = query_params.copy()
    merged_params.update(body_params)

    param_candidates: list[dict[str, str]] = []
    seen_param_fingerprints: set[tuple[tuple[str, str], ...]] = set()
    for candidate in (body_params, query_params, merged_params, {}):
        fingerprint = tuple(sorted(candidate.items()))
        if fingerprint in seen_param_fingerprints:
            continue
        seen_param_fingerprints.add(fingerprint)
        param_candidates.append(candidate)

    attempts: list[tuple[str, dict[str, str]]] = []
    seen_attempts: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for url_candidate in (full_url, url_no_query):
        for params_candidate in param_candidates:
            fingerprint = (url_candidate, tuple(sorted(params_candidate.items())))
            if fingerprint in seen_attempts:
                continue
            seen_attempts.add(fingerprint)
            attempts.append((url_candidate, params_candidate))

    verified = False
    for url_candidate, params_candidate in attempts:
        if _twilio_validator.validate(url_candidate, params_candidate, x_twilio_signature):
            logger.debug("Twilio signature OK via %s", url_candidate)
            verified = True
            break

    if not verified:
        ref_url, ref_params = attempts[0] if attempts else (full_url, merged_params)
        expected = _twilio_validator.compute_signature(ref_url, ref_params)
        logger.warning(
            "Twilio signature mismatch for %s. expected=%s received=%s",
            full_url,
            expected,
            x_twilio_signature,
        )
        raise HTTPException(status_code=401, detail="Invalid Twilio signature")

    # -------- Build dynamic TwiML --------
    # Determine agent based on inbound caller number
    caller_number = merged_params.get("From") or merged_params.get("Caller")
    agent_name, agent = get_agent_for_number(caller_number)

    call_sid = merged_params.get("CallSid")
    if call_sid:
        # Load persisted state if enabled
        saved_settings, saved_messages = (None, None)
        resumed = False
        if agent.get("continue_conversation"):
            saved_settings, saved_messages = load_state(agent_name)
            if saved_messages is not None:
                # Merge settings override
                if saved_settings:
                    agent.update(saved_settings)
                agent["welcome_greeting"] = agent["continuation_greeting"]
                resumed = True

        store_call(call_sid, (agent_name, agent, saved_messages, resumed, caller_number))

    logger.debug("  Agent selected: %s", agent_name)
    logger.debug("  Agent config: %s", agent)

    # Use the host header which includes port if present
    host_header = request.headers.get("host", request.url.hostname)
    ws_url = f"wss://{host_header}/ws"

    # Twilio Connect action callback – helps reconcile costs if the process dies early
    action_url = f"https://{host_header}/connect_done"

    # ------------------------------------------------------------------
    # Build TwiML using xml.etree to avoid improper escaping
    # ------------------------------------------------------------------
    import os
    from xml.etree.ElementTree import (
        Element,
        SubElement,
        tostring,
    )  # local import keeps global namespace clean

    attrs: dict[str, str] = {
        "url": ws_url,
        "language": agent["language"],
        "ttsProvider": agent.get("tts_provider", ""),
        "voice": agent["voice"],
        "welcomeGreeting": agent["welcome_greeting"],
        "welcomeGreetingInterruptible": "speech",  # callers can barge-in on greeting
        "reportInputDuringAgentSpeech": "any",  # preserve legacy barge-in behaviour
        "transcriptionProvider": agent.get("transcription_provider", ""),
        "speechModel": agent.get("speech_model", ""),
    }

    # Allow barge-in between assistant turns (long responses)
    attrs["preemptible"] = "true"
    attrs["interruptible"] = "speech"

    # Optional STT hints (comma-separated) – improve recognition of domain terms
    if hints := agent.get("hints"):
        attrs["hints"] = hints

    # Enable debug & DTMF events if configured for this agent
    if debug_opts := agent.get("debug"):
        attrs["debug"] = debug_opts
        attrs["dtmfDetection"] = "true"

    # ------------------------------------------------------------------
    # Provider-specific attributes (speed factor)
    # ------------------------------------------------------------------

    provider_name = agent.get("tts_provider", "")
    if provider_supports_speed(provider_name):
        rate_val = agent.get("tts_prosody", {}).get("rate")
        if rate_val is not None:
            attrs["ttsSpeed"] = str(rate_to_speed_factor(rate_val))

    # ---------------------------
    # Build XML tree
    # ---------------------------
    resp = Element("Response")
    connect_el = SubElement(resp, "Connect", action=action_url)
    cr_el = SubElement(connect_el, "ConversationRelay", **attrs)

    # Custom parameters passed back in initial setup message
    params: dict[str, str | int] = {
        "agent": agent_name,
        "version": os.getenv("GIT_SHA", "dev"),
        "max_disc": str(agent["max_disconnect_seconds"]),
    }

    for name, val in params.items():
        SubElement(cr_el, "Parameter", name=name, value=str(val))

    xml = tostring(resp, encoding="unicode")

    logger.debug("Returning TwiML:\n%s", xml)

    return Response(content=xml, media_type="application/xml")


@router.post("/twiml", response_class=Response)
async def twiml_post(
    request: Request,
    x_twilio_signature: str = Header(..., alias="X-Twilio-Signature"),
) -> Response:  # noqa: D401
    """POST variant – Twilio may call the webhook with POST depending on console configuration."""
    return await twiml(request, x_twilio_signature)


# ---------------------------------------------------------------------------
# Twilio <Connect action="..."> callback – persists even if container dies.
# We don't need to process the payload yet; respond 200 so Twilio is satisfied.
# ---------------------------------------------------------------------------


@router.post("/connect_done", response_class=PlainTextResponse)
def connect_done() -> str:  # noqa: D401
    """No-op endpoint so Twilio's action callback doesn't 404."""

    return "ok"
