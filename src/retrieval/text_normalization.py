"""Search-only text normalization; source body/title values are never mutated."""

from __future__ import annotations

import re
import unicodedata

ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"
SPECIAL_SPACES = "\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"


def normalize_search_text(value: str | None, *, remove_spaces: bool = False) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.translate({ord(char): " " for char in ZERO_WIDTH + SPECIAL_SPACES})
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text.replace(" ", "") if remove_spaces else text


def normalize_company_query(value: str | None) -> str:
    return normalize_search_text(value, remove_spaces=True)


_REPORT_TYPE_MAP = {
    "\uc0ac\uc5c5\ubcf4\uace0\uc11c": "annual", "\uc5f0\ucc28\ubcf4\uace0\uc11c": "annual", "annual": "annual",
    "\ubc18\uae30\ubcf4\uace0\uc11c": "half", "\ubc18\uae30": "half", "half": "half",
    "\ubd84\uae30\ubcf4\uace0\uc11c": "quarter", "\ubd84\uae30": "quarter", "quarter": "quarter",
}

def canonical_report_type(value: str | None) -> str | None:
    """Map display labels and stored codes to the canonical DB report_type."""
    if value is None:
        return None
    normalized = normalize_search_text(value)
    return _REPORT_TYPE_MAP.get(normalized, value)
