"""Physical-source and fine-grained provenance records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._serialization import JsonSerializable


@dataclass(slots=True)
class Provenance(JsonSerializable):
    """Location of an extracted value in a physical corpus file.

    ``dom_path`` is intentionally a parser-supplied, DOM-like path rather than
    a claim that every source has a real DOM.  PDF parsers can use
    ``page_number`` and source indices instead.  All coordinates are nullable
    because a tolerant parser may only know the physical file.
    """

    source_file: str
    file_role: str | None = None
    source_index: int | None = None
    dom_path: str | None = None
    tag: str | None = None
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SourceFileRecord(JsonSerializable):
    """Evidence record for one physical source file."""

    relative_path: str
    role: str
    detected_format: str
    size_bytes: int | None = None
    sha256: str | None = None
    declared_charset: str | None = None
    encoding: str | None = None
    is_canonical: bool = False
    original_path: str | None = None
    parser: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def decoded_encoding(self) -> str | None:
        """Read-only alias clarifying that ``encoding`` is the decoder used."""

        return self.encoding


__all__ = ["Provenance", "SourceFileRecord"]
