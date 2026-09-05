"""Conservative, evidence-safe text normalization."""

from __future__ import annotations

import unicodedata


_EXTRA_LINE_BREAKS = {"\u0085", "\u2028", "\u2029"}


def normalize_line_breaks(value: str) -> str:
    """Normalize Unicode to NFC and represent recognized line breaks as LF."""

    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join("\n" if character in _EXTRA_LINE_BREAKS else character for character in text)


def _collapse_horizontal_whitespace(value: str) -> str:
    pieces: list[str] = []
    in_whitespace = False
    for character in value:
        if character != "\n" and character.isspace():
            if not in_whitespace:
                pieces.append(" ")
            in_whitespace = True
        else:
            pieces.append(character)
            in_whitespace = False
    return "".join(pieces)


def normalize_text(
    value: str,
    *,
    preserve_line_breaks: bool = True,
    trim: bool = True,
) -> str:
    """Return an NFC/whitespace-only normalized view of source text.

    No digits, punctuation, units, words, or sentence boundaries are rewritten.
    The caller must retain the original string separately when exact evidence
    is required (the IR's ``raw_text`` fields do this).
    """

    text = normalize_line_breaks(value)
    if not preserve_line_breaks:
        text = " ".join(text.split())
        return text.strip() if trim else text

    text = _collapse_horizontal_whitespace(text)
    lines = [line.strip(" ") if trim else line for line in text.split("\n")]
    normalized = "\n".join(lines)
    return normalized.strip("\n") if trim else normalized


def normalize_optional_text(
    value: str | None,
    *,
    preserve_line_breaks: bool = True,
    trim: bool = True,
) -> str | None:
    if value is None:
        return None
    return normalize_text(
        value, preserve_line_breaks=preserve_line_breaks, trim=trim
    )


__all__ = ["normalize_line_breaks", "normalize_optional_text", "normalize_text"]
