"""Validate the X-Twilio-Signature header on the *WebSocket handshake*.

ConversationRelay started sending this header in April 2025.

Twilio signs it exactly the same way it signs every other webhook.
Rely on the helper in *twilio-python* so we stay in sync with future
algorithm tweaks.
"""

import logging

from urllib.parse import parse_qsl, urlsplit

from twilio.request_validator import RequestValidator

from fastapi import WebSocket

from log_love import setup_logging

from .settings import get_env


# Set up module logger
logger = setup_logging()

env = get_env()
validator = RequestValidator(env.twilio_auth_token)


def is_from_twilio(ws: WebSocket) -> bool:
    signature = ws.headers.get("x-twilio-signature")
    if not signature:
        logger.warning("WebSocket missing x-twilio-signature header")
        return False

    proto = ws.headers.get("x-forwarded-proto", ws.url.scheme)
    host_header = ws.headers.get("host", ws.url.hostname)

    # Prepare host variants: with and without port
    host_variants = {host_header}
    if ":" in host_header:
        host_variants.add(host_header.split(":")[0])

    # Twilio signs using the WebSocket scheme (wss/ws). When requests travel through an
    # HTTPS reverse-proxy such as ngrok or AWS ALB, x-forwarded-proto may come through as
    # "https".  Accept both so we validate the same way Twilio computed the signature.
    proto_alts = {proto}
    if proto.lower() == "https":
        proto_alts.add("wss")
    elif proto.lower() == "http":
        proto_alts.add("ws")
    elif proto.lower() == "wss":
        proto_alts.add("https")
    elif proto.lower() == "ws":
        proto_alts.add("http")

    url = f"{proto}://{host_header}{ws.url.path}"
    if ws.url.query:
        url += f"?{ws.url.query}"

    # Convert query-string list into a mapping for the validator.
    params = dict(parse_qsl(ws.url.query, keep_blank_values=True))

    attempts = []
    for p in proto_alts:
        for h in host_variants:
            full = f"{p}://{h}{ws.url.path}"
            no_q = full
            if ws.url.query:
                full_q = f"{full}?{ws.url.query}"
            else:
                full_q = full

            attempts.extend([
                (full_q, params),
                (full_q, {}),
                (no_q, params),
                (no_q, {}),
            ])

    for u, p in attempts:
        if validator.validate(u, p, signature):
            logger.debug("Twilio WS signature OK via %s", u)
            return True

    # Use the first attempted URL for expected signature reference
    base_url = next(iter(proto_alts)) + "://" + next(iter(host_variants)) + ws.url.path
    expected = validator.compute_signature(base_url, params)
    logger.warning(
        "Twilio WS signature failed for URL=%s expected=%s received=%s",
        url,
        expected,
        signature,
    )

    return False 