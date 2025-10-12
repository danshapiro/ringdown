"""Audio/SSML helpers shared across API modules."""

from __future__ import annotations

from .logging_utils import logger

__all__ = [
    "apply_prosody",
    "merge_prosody",
    "prosody_is_useful",
    "provider_supports_speed",
    "rate_to_speed_factor",
    "voice_supports_ssml",
]


_PROSODY_ATTR_NAMES: set[str] = {"rate", "pitch", "volume"}


def merge_prosody(
    defaults: dict[str, str | float], override: dict[str, str | float] | None
) -> dict[str, str | float]:
    """Return shallow copy of *defaults* updated with non-null override values."""

    merged = defaults.copy()
    if override:
        merged.update({k: v for k, v in override.items() if v is not None})
    return merged


def _normalise_rate(val: str | float) -> str:
    """Return an SSML-compliant rate string."""

    try:
        factor = float(val)
    except (TypeError, ValueError):
        return str(val)
    return f"{factor * 100:.0f}%"


def _build_prosody_tag(text: str, prosody: dict[str, str | float]) -> str:
    """Return *text* wrapped in a `<speak>` root with optional `<prosody>`."""

    import html
    from xml.sax.saxutils import escape as xml_escape

    text = html.unescape(text)
    escaped = xml_escape(text)

    if prosody:
        attrs: list[str] = []
        for attr, value in prosody.items():
            if attr not in _PROSODY_ATTR_NAMES or value is None:
                continue
            if attr == "rate":
                value = _normalise_rate(value)
            attrs.append(f'{attr}="{value}"')
        if attrs:
            attr_str = " ".join(attrs)
            inner = f"<prosody {attr_str}>{escaped}</prosody>"
        else:
            inner = escaped
    else:
        inner = escaped

    return f"<speak>{inner}</speak>"


def _is_default_rate(val: str | float | int) -> bool:
    try:
        normalised = _normalise_rate(val)
    except Exception:  # pragma: no cover - defensive
        return False
    return str(normalised).strip().lower() in {"100%", "normal", "medium"}


def _is_default_pitch(val: str | float | int) -> bool:
    sval = str(val).strip().lower()
    return sval in {"0%", "0", "default", "medium", "normal"}


def _is_default_volume(val: str | float | int) -> bool:
    sval = str(val).strip().lower()
    return sval in {"0%", "0", "default", "medium", "normal"}


def prosody_is_useful(prosody: dict[str, str | float] | None) -> bool:
    """Return ``True`` when at least one prosody attribute differs from default."""

    if not prosody:
        return False

    for attr, value in prosody.items():
        if value is None or attr not in _PROSODY_ATTR_NAMES:
            continue
        if attr == "rate" and not _is_default_rate(value):
            return True
        if attr == "pitch" and not _is_default_pitch(value):
            return True
        if attr == "volume" and not _is_default_volume(value):
            return True
    return False


def apply_prosody(text: str, prosody: dict[str, str | float] | None) -> str:
    """Return *text* as SSML, guarding against invalid chunks."""

    import html
    import re
    from xml.sax.saxutils import escape as xml_escape

    text = text.strip()
    if text == "":
        return ""

    text = html.unescape(text)
    if not re.search(r"[A-Za-z0-9]", text):
        return xml_escape(text)

    candidate = _build_prosody_tag(text, prosody or {})

    import xml.etree.ElementTree as ET

    try:
        ET.fromstring(candidate)
    except ET.ParseError as exc:  # pragma: no cover - defensive
        logger.error("SSML parse failure – falling back to plain text: %s", exc)
        return xml_escape(text)

    return candidate


def voice_supports_ssml(voice: str) -> bool:
    """Return ``True`` if *voice* is on the SSML greenlist."""

    green_tokens: tuple[str, ...] = (
        "Standard",
        "WaveNet",
        "Wavenet",
        "Neural2",
        "Studio",
        "Journey",
    )
    name_lower = voice.lower()
    return any(token.lower() in name_lower for token in green_tokens)


def provider_supports_speed(provider: str) -> bool:
    """Return ``True`` when *provider* exposes a numeric ttsSpeed parameter."""

    return provider.lower() in {"elevenlabs", "11labs"}


def rate_to_speed_factor(rate_val: str | float | int) -> float:
    """Convert SSML ``rate`` to ElevenLabs ttsSpeed values."""

    if isinstance(rate_val, str):
        val = rate_val.strip().lower()
        if val.endswith("%"):
            pct = float(val.rstrip("%")) / 100.0
            _validate_speed(pct)
            return pct
        mapping = {
            "x-slow": 0.75,
            "slow": 0.85,
            "medium": 1.0,
            "fast": 1.15,
            "x-fast": 1.2,
        }
        if val in mapping:
            mapped = mapping[val]
            _validate_speed(mapped)
            return mapped
        numeric = float(val)
        _validate_speed(numeric)
        return numeric

    numeric_val = float(rate_val)
    _validate_speed(numeric_val)
    return numeric_val


def _validate_speed(speed: float) -> None:
    if not (0.7 <= speed <= 1.2):
        raise ValueError(f"ttsSpeed {speed:.2f} is outside ElevenLabs supported range 0.7–1.2")
