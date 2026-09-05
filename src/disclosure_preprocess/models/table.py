"""Evidence-preserving table intermediate representation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._serialization import JsonSerializable
from .provenance import Provenance


@dataclass(slots=True)
class TableCell(JsonSerializable):
    """One source table cell before grid expansion.

    ``raw_text`` is never normalized in place.  ``text`` may contain a safe,
    whitespace-normalized view, while rowspan/colspan retain the literal
    structural meaning reported by the parser.  Invalid span values are
    accepted here so the table normalizer can return a structured warning
    instead of crashing the entire disclosure parse.
    """

    raw_text: str
    text: str | None = None
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False
    source_row_index: int | None = None
    source_cell_index: int | None = None
    provenance: Provenance | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Table(JsonSerializable):
    """A source table with both raw rows and an optional normalized view."""

    table_id: str
    raw_rows: list[list[TableCell]] = field(default_factory=list)
    normalized_rows: list[list[str | None]] | None = None
    caption: str | None = None
    raw_caption: str | None = None
    provenance: Provenance | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


__all__ = ["Table", "TableCell"]
