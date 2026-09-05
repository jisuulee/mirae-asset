"""Common evidence-preserving intermediate representation for Phase 2."""

from .content import (
    BlockType,
    ContentBlock,
    DocumentContent,
    ListItem,
    Section,
    derive_body_text,
)
from .document import (
    AlternativeRepresentation,
    DisclosureDocument,
    DocumentParseResult,
    ParseResult,
    ParseStatus,
    ParseWarning,
    ParsedDisclosure,
    WarningSeverity,
    combine_parse_status,
)
from .provenance import Provenance, SourceFileRecord
from .table import Table, TableCell

__all__ = [
    "AlternativeRepresentation",
    "BlockType",
    "ContentBlock",
    "DisclosureDocument",
    "DocumentContent",
    "DocumentParseResult",
    "ListItem",
    "ParseResult",
    "ParseStatus",
    "ParseWarning",
    "ParsedDisclosure",
    "Provenance",
    "Section",
    "SourceFileRecord",
    "Table",
    "TableCell",
    "WarningSeverity",
    "combine_parse_status",
    "derive_body_text",
]
