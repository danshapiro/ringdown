"""SSML chunk structure invariants.

These tests ensure that:
1. _build_prosody_tag() MUST embed <speak> so that Twilio TTS treats every
   streamed chunk as a self-contained SSML document.
2. The resulting SSML parses for every chunk (guards against the
   INVALID_ARGUMENT error thrown by Google Neural2 and similar voices).
"""

from xml.etree import ElementTree as ET

import pytest

from app.main import _build_prosody_tag, _merge_prosody


def _wrap_by_twilio(fragment: str) -> str:
    """Return *fragment* wrapped with the <speak> root CR adds automatically."""

    return f"<speak>{fragment}</speak>"


def test_build_prosody_tag_emits_speak_root():
    """Generated chunk must contain a <speak> root and optional <prosody>."""

    raw = "Hello world"
    out = _build_prosody_tag(raw, {"rate": "120%"})

    assert out.lstrip().startswith("<speak>"), "Output should begin with <speak>"

    root = ET.fromstring(out)
    assert root.tag == "speak" and out.count("<speak>") == 1
    prosody_el = root.find("prosody")
    assert prosody_el is not None and prosody_el.text == raw


def test_single_chunk_parses_validly():
    """_build_prosody_tag() output should be valid SSML on its own."""

    prosody_cfg = _merge_prosody({}, {"rate": "150%"})
    chunk = _build_prosody_tag("Hi there", prosody_cfg)

    # Direct output should parse without error.
    ET.fromstring(chunk) 