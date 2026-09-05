"""Parse-result and logical-disclosure models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from ._serialization import JsonSerializable
from .content import DocumentContent
from .provenance import Provenance, SourceFileRecord


class ParseStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class WarningSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(slots=True)
class ParseWarning(JsonSerializable):
    code: str
    message: str
    severity: WarningSeverity | str = WarningSeverity.WARNING
    provenance: Provenance | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParseResult(JsonSerializable):
    """Result of parsing one physical source representation."""

    source_file: SourceFileRecord
    parse_status: ParseStatus = ParseStatus.FAILED
    content: DocumentContent | None = None
    warnings: list[ParseWarning] = field(default_factory=list)
    parser_name: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> ParseStatus:
        """Compatibility/readability alias for ``parse_status``."""

        return self.parse_status

    @property
    def parser(self) -> str | None:
        """Read-only shorthand retained for callers that prefer ``parser``."""

        return self.parser_name


@dataclass(slots=True)
class AlternativeRepresentation(JsonSerializable):
    """Non-canonical PDF/HTML representation without duplicated body content."""

    source_file: SourceFileRecord
    parse_status: ParseStatus
    parser_name: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[ParseWarning] = field(default_factory=list)
    content_included: bool = False


def combine_parse_status(results: Iterable[ParseResult]) -> ParseStatus:
    """Aggregate physical-file statuses without hiding partial failures."""

    statuses = [result.parse_status for result in results]
    if not statuses or all(status == ParseStatus.FAILED for status in statuses):
        return ParseStatus.FAILED
    if all(status == ParseStatus.SUCCESS for status in statuses):
        return ParseStatus.SUCCESS
    return ParseStatus.PARTIAL


@dataclass(slots=True)
class DisclosureDocument(JsonSerializable):
    """One logical disclosure corresponding to exactly one manifest row."""

    doc_id: str
    rcept_no: str
    metadata: dict[str, Any] = field(default_factory=dict)
    main_document: ParseResult | None = None
    attachments: list[ParseResult] = field(default_factory=list)
    alternative_representations: list[AlternativeRepresentation] = field(
        default_factory=list
    )
    source_files: list[SourceFileRecord] = field(default_factory=list)
    parse_status: ParseStatus = ParseStatus.FAILED
    warnings: list[ParseWarning] = field(default_factory=list)
    # Used for complete physical-file reporting.  JSON output uses the reduced
    # alternative representation above and never duplicates alternative body.
    physical_results: list[ParseResult] = field(
        default_factory=list,
        repr=False,
        metadata={"json_skip": True},
    )

    @property
    def status(self) -> ParseStatus:
        return self.parse_status

    def refresh_parse_status(self) -> ParseStatus:
        parts = ([self.main_document] if self.main_document is not None else []) + list(
            self.attachments
        )
        self.parse_status = combine_parse_status(parts)
        return self.parse_status


# Explicit aliases make intent clear to callers without creating duplicate IRs.
DocumentParseResult = ParseResult
ParsedDisclosure = DisclosureDocument


__all__ = [
    "AlternativeRepresentation",
    "DisclosureDocument",
    "DocumentParseResult",
    "ParseResult",
    "ParseStatus",
    "ParseWarning",
    "ParsedDisclosure",
    "WarningSeverity",
    "combine_parse_status",
]
