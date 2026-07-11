"""Unit contract for ``sanitize_message`` (panelapp error-message sanitation).

``sanitize_message`` strips the fence's forbidden control/zero-width/bidi/NUL
code points from a caller-visible message and length-caps it. It reuses the same
``FORBIDDEN_CODEPOINTS`` set the untrusted-text fence removes, so an error frame
can never smuggle those code points into the model. It does NOT (and is not meant
to) remove injection PROSE -- attacker prose is kept out of caller-visible
messages by severing upstream bodies at the source, not by this helper.
"""

from __future__ import annotations

from panelapp_link.mcp.untrusted_content import (
    FORBIDDEN_CODEPOINTS,
    MAX_MESSAGE_CHARS,
    sanitize_message,
)


def test_strips_nul_zwj_bom_and_bidi_override() -> None:
    dirty = "boom\x00 zwj‍ bom﻿ rtl‮ tail"
    clean = sanitize_message(dirty)
    for cp in ("\x00", "‍", "﻿", "‮"):
        assert cp not in clean
    # ordinary prose survives verbatim (only the code points are removed)
    assert clean == "boom zwj bom rtl tail"


def test_preserves_ordinary_prose_and_whitespace() -> None:
    text = "PanelApp returned HTTP 404. Try search_panels."
    assert sanitize_message(text) == text
    # tab + newline are ratified as ordinary whitespace, not stripped
    assert sanitize_message("a\tb\nc") == "a\tb\nc"


def test_length_capped_at_max() -> None:
    capped = sanitize_message("x" * (MAX_MESSAGE_CHARS + 500))
    assert len(capped) == MAX_MESSAGE_CHARS


def test_strips_every_forbidden_codepoint() -> None:
    dirty = "".join(chr(cp) for cp in sorted(FORBIDDEN_CODEPOINTS))
    assert sanitize_message(dirty) == ""
