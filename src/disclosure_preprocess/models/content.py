"""Ordered content, section, and document-content IR models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from ._serialization import JsonSerializable
from .provenance import Provenance
from .table import Table


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    LINE_BREAK = "line_break"
    PAGE_BREAK = "page_break"
    OTHER = "other"


@dataclass(slots=True)
class ListItem(JsonSerializable):
    raw_text: str
    text: str | None = None
    provenance: Provenance | None = None


@dataclass(slots=True)
class ContentBlock(JsonSerializable):
    """One ordered block in a parsed physical document."""

    block_type: BlockType | str
    block_id: str | None = None
    raw_text: str | None = None
    text: str | None = None
    level: int | None = None
    items: list[ListItem] = field(default_factory=list)
    table_id: str | None = None
    provenance: Provenance | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Section(JsonSerializable):
    """A conservative section boundary over ordered block identifiers.

    Title and level are nullable by design.  A parser must not invent a
    hierarchy when source markup does not establish one.
    """

    section_id: str
    title: str | None = None
    raw_title: str | None = None
    level: int | None = None
    block_ids: list[str] = field(default_factory=list)
    start_block_index: int | None = None
    end_block_index: int | None = None
    provenance: Provenance | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


def derive_body_text(block_sequence: Iterable[ContentBlock]) -> str:
    """Derive a plain-text view using only text already present in blocks.

    Tables are excluded because their cells remain available in ``tables``;
    copying span-expanded cells into body text would create duplicates.  The
    function only joins source-derived strings with line breaks and performs
    no rewriting or semantic inference.
    """

    fragments: list[str] = []
    for block in block_sequence:
        block_type = str(block.block_type)
        if block_type == BlockType.TABLE.value:
            continue
        if block_type == BlockType.LINE_BREAK.value:
            fragments.append("")
            continue
        if block_type == BlockType.PAGE_BREAK.value:
            fragments.append("\f")
            continue
        if block_type == BlockType.LIST.value:
            for item in block.items:
                value = item.text if item.text is not None else item.raw_text
                if value:
                    fragments.append(value)
            continue
        value = block.text if block.text is not None else block.raw_text
        if value:
            fragments.append(value)
    return "\n".join(fragments)


@dataclass(slots=True)
class DocumentContent(JsonSerializable):
    """Extracted content for a single physical document representation."""

    title: str | None = None
    raw_title: str | None = None
    body_text: str = ""
    sections: list[Section] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    block_sequence: list[ContentBlock] = field(default_factory=list)
    title_provenance: Provenance | None = None
    provenance: Provenance | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def derive_body_text(self) -> str:
        """Replace ``body_text`` with its source-only ordered block view."""

        self.body_text = derive_body_text(self.block_sequence)
        return self.body_text


__all__ = [
    "BlockType",
    "ContentBlock",
    "DocumentContent",
    "ListItem",
    "Section",
    "derive_body_text",
]
