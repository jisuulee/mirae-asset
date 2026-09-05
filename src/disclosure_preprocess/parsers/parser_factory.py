"""Content-based parser dispatch with file-level exception isolation."""

from __future__ import annotations

from disclosure_preprocess.format_detector import detect_format_from_bytes
from disclosure_preprocess.models import (
    ParseResult,
    ParseStatus,
    ParseWarning,
    Provenance,
    WarningSeverity,
)

from .base import (
    UNSUPPORTED_FORMAT,
    BaseParser,
    BinaryPayload,
    ParseContext,
    make_source_file_record,
)
from .html_parser import HtmlParser
from .json_parser import JsonParser
from .pdf_parser import PdfParser
from .xml_parser import XmlParser


PARSER_UNHANDLED_EXCEPTION = "PARSER_UNHANDLED_EXCEPTION"


def get_parser(detected_format: str) -> BaseParser | None:
    """Return a new parser for a detector result, never for a suffix alone."""

    parser_types: dict[str, type[BaseParser]] = {
        "xml": XmlParser,
        "html": HtmlParser,
        "json": JsonParser,
        "pdf": PdfParser,
    }
    parser_type = parser_types.get(detected_format)
    return parser_type() if parser_type is not None else None


def parse_payload(payload: BinaryPayload, context: ParseContext) -> ParseResult:
    """Detect and parse one physical payload without propagating file errors."""

    detected = detect_format_from_bytes(
        payload.data[: 64 * 1024], extension=context.extension
    )
    context.detected_format = detected
    source_file = make_source_file_record(context, payload)
    parser = get_parser(detected)
    provenance = Provenance(
        source_file=context.relative_path,
        file_role=context.role,
    )
    if parser is None:
        return ParseResult(
            source_file=source_file,
            parse_status=ParseStatus.FAILED,
            warnings=[
                ParseWarning(
                    code=UNSUPPORTED_FORMAT,
                    message=f"No parser is available for detected format: {detected}",
                    severity=WarningSeverity.ERROR,
                    provenance=provenance,
                    details={"detected_format": detected},
                )
            ],
            parser_name=None,
        )
    try:
        return parser.parse(payload, context, source_file)
    except Exception as exc:  # one damaged file must not terminate the corpus
        source_file.parser = parser.parser_name
        return ParseResult(
            source_file=source_file,
            parse_status=ParseStatus.FAILED,
            warnings=[
                ParseWarning(
                    code=PARSER_UNHANDLED_EXCEPTION,
                    message=f"The parser raised an isolated exception: {exc}",
                    severity=WarningSeverity.ERROR,
                    provenance=provenance,
                    details={
                        "parser": parser.parser_name,
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            ],
            parser_name=parser.parser_name,
        )


__all__ = ["PARSER_UNHANDLED_EXCEPTION", "get_parser", "parse_payload"]
