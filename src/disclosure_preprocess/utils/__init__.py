"""Small shared utilities for disclosure preprocessing."""

from __future__ import annotations

import unicodedata
from os import PathLike
from pathlib import PurePosixPath
import re


def normalize_nfc(value: str) -> str:
    """Return *value* in Unicode NFC form.

    DART archive paths can be NFD while manifest values are NFC.  Normalising
    both sides with this function before equality checks or dictionary joins
    prevents visually identical Korean names from comparing unequal.
    """

    return unicodedata.normalize("NFC", value)


def normalize_unicode(value: str) -> str:
    """Alias with a descriptive name for :func:`normalize_nfc`."""

    return normalize_nfc(value)


def normalized_path_key(value: str | PathLike[str]) -> str:
    """Return an NFC-normalized path/join key without changing case or separators."""

    return normalize_nfc(str(value))


def normalize_relative_posix_path(value: str | PathLike[str]) -> str:
    """Validate and NFC-normalize a relative path with POSIX separators.

    Only exact leading ``./`` segments are removed. Absolute paths, Windows
    drive-qualified paths, and parent traversal are rejected rather than
    silently aliased to a different corpus member.
    """

    text = str(value).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if re.match(r"^[A-Za-z]:(?:/|$)", text):
        raise ValueError(f"Drive-qualified path is not allowed: {value}")
    path = PurePosixPath(text)
    if path.is_absolute():
        raise ValueError(f"Absolute path is not allowed: {value}")
    if ".." in path.parts:
        raise ValueError(f"Parent traversal is not allowed: {value}")
    normalized = str(path)
    return "" if normalized == "." else normalize_nfc(normalized)


def nfc_equal(left: str, right: str) -> bool:
    """Compare two strings after NFC normalization."""

    return normalize_nfc(left) == normalize_nfc(right)


__all__ = [
    "nfc_equal",
    "normalize_nfc",
    "normalize_relative_posix_path",
    "normalize_unicode",
    "normalized_path_key",
]
