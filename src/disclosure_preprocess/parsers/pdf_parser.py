"""OCR-free, page-preserving PDF text extraction using pypdf."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from disclosure_preprocess.models import (
    BlockType,
    ContentBlock,
    DocumentContent,
    ParseResult,
    ParseStatus,
    ParseWarning,
    Provenance,
    SourceFileRecord,
    WarningSeverity,
)
from disclosure_preprocess.normalizers.text import normalize_text

from .base import (
    PARSER_DEPENDENCY_MISSING,
    BaseParser,
    BinaryPayload,
    ParseContext,
)

try:  # Kept optional so one unavailable parser cannot stop the corpus run.
    import pypdf
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - exercised only in dependency failures.
    pypdf = None  # type: ignore[assignment]
    PdfReader = None  # type: ignore[assignment,misc]


PDF_ENCRYPTED = "PDF_ENCRYPTED"
PDF_EMPTY_PASSWORD_DECRYPTED = "PDF_EMPTY_PASSWORD_DECRYPTED"
PDF_METADATA_READ_FAILED = "PDF_METADATA_READ_FAILED"
PDF_NO_PAGES = "PDF_NO_PAGES"
PDF_PAGE_EXTRACTION_FAILED = "PDF_PAGE_EXTRACTION_FAILED"
PDF_PARSE_FAILED = "PDF_PARSE_FAILED"
TITLE_NOT_FOUND = "TITLE_NOT_FOUND"
EMPTY_BODY = "EMPTY_BODY"


def _provenance(
    context: ParseContext,
    *,
    source_index: int | None = None,
    page_number: int | None = None,
    dom_path: str | None = None,
    tag: str | None = None,
) -> Provenance:
    return Provenance(
        source_file=context.relative_path,
        file_role=context.role,
        source_index=source_index,
        page_number=page_number,
        dom_path=dom_path,
        tag=tag,
    )


def _exception_details(error: Exception) -> dict[str, str]:
    # Exception type and a bounded message are diagnostic without retaining
    # arbitrary binary input or an unbounded parser traceback in JSONL output.
    return {
        "exception_type": type(error).__name__,
        "exception_message": str(error)[:500],
    }


def _metadata_as_strings(metadata: Any) -> dict[str, str | None]:
    if metadata is None:
        return {}
    result: dict[str, str | None] = {}
    for key, value in metadata.items():
        result[str(key)] = None if value is None else str(value)
    return result


class PdfParser(BaseParser):
    """Extract source-provided PDF text per page without OCR or inference."""

    parser_name = "pdf"

    def parse(
        self,
        payload: BinaryPayload,
        context: ParseContext,
        source_file: SourceFileRecord,
    ) -> ParseResult:
        source_file.parser = self.parser_name
        source_file.encoding = None
        source_file.declared_charset = None
        file_provenance = _provenance(context, dom_path="/", tag="pdf")
        warnings: list[ParseWarning] = []
        source_metadata: dict[str, Any] = {
            "extraction_method": "pypdf",
            "ocr_used": False,
            "page_boundaries_preserved": True,
            "table_structure_preserved": False,
            "text_extraction_only": True,
        }

        if PdfReader is None or pypdf is None:
            warning = ParseWarning(
                code=PARSER_DEPENDENCY_MISSING,
                message="pypdf is required for PDF text extraction",
                severity=WarningSeverity.ERROR,
                provenance=file_provenance,
                details={"dependency": "pypdf"},
            )
            return self.failed_result(
                source_file, warning, source_metadata=source_metadata
            )

        source_metadata["parser_version"] = pypdf.__version__
        try:
            reader = PdfReader(BytesIO(payload.data), strict=False)
        except Exception as error:
            warning = ParseWarning(
                code=PDF_PARSE_FAILED,
                message="The PDF container could not be opened",
                severity=WarningSeverity.ERROR,
                provenance=file_provenance,
                details=_exception_details(error),
            )
            return self.failed_result(
                source_file, warning, source_metadata=source_metadata
            )

        source_metadata["is_encrypted"] = bool(reader.is_encrypted)
        if reader.is_encrypted:
            try:
                decrypt_result = reader.decrypt("")
            except Exception as error:
                decrypt_result = 0
                decrypt_error = error
            else:
                decrypt_error = None
            if not decrypt_result:
                details: dict[str, Any] = {"password_supplied": False}
                if decrypt_error is not None:
                    details.update(_exception_details(decrypt_error))
                warning = ParseWarning(
                    code=PDF_ENCRYPTED,
                    message=(
                        "The PDF is encrypted and cannot be read without a "
                        "password"
                    ),
                    severity=WarningSeverity.ERROR,
                    provenance=file_provenance,
                    details=details,
                )
                return self.failed_result(
                    source_file, warning, source_metadata=source_metadata
                )
            warnings.append(
                ParseWarning(
                    code=PDF_EMPTY_PASSWORD_DECRYPTED,
                    message="The encrypted PDF was readable with an empty password",
                    severity=WarningSeverity.INFO,
                    provenance=file_provenance,
                )
            )

        try:
            page_count = len(reader.pages)
        except Exception as error:
            warning = ParseWarning(
                code=PDF_PARSE_FAILED,
                message="The PDF page tree could not be read",
                severity=WarningSeverity.ERROR,
                provenance=file_provenance,
                details=_exception_details(error),
            )
            return ParseResult(
                source_file=source_file,
                parse_status=ParseStatus.FAILED,
                warnings=warnings + [warning],
                parser_name=self.parser_name,
                source_metadata=source_metadata,
            )

        source_metadata["page_count"] = page_count
        metadata_failed = False
        try:
            pdf_metadata = _metadata_as_strings(reader.metadata)
        except Exception as error:
            pdf_metadata = {}
            metadata_failed = True
            warnings.append(
                ParseWarning(
                    code=PDF_METADATA_READ_FAILED,
                    message="PDF document metadata could not be read",
                    provenance=file_provenance,
                    details=_exception_details(error),
                )
            )
        source_metadata["pdf_metadata"] = pdf_metadata

        raw_title = pdf_metadata.get("/Title")
        title = (
            normalize_text(raw_title, preserve_line_breaks=False)
            if raw_title is not None
            else None
        )
        if title == "":
            title = None
        title_provenance = (
            _provenance(context, dom_path="/Info/Title", tag="pdf-metadata")
            if raw_title is not None
            else None
        )
        if title is None:
            warnings.append(
                ParseWarning(
                    code=TITLE_NOT_FOUND,
                    message="Explicit PDF title metadata could not be identified",
                    provenance=file_provenance,
                )
            )

        blocks: list[ContentBlock] = []
        failed_pages = 0
        for page_index in range(page_count):
            page_number = page_index + 1
            block_index = len(blocks)
            page_provenance = _provenance(
                context,
                source_index=block_index,
                page_number=page_number,
                dom_path=f"/pages/{page_number}",
                tag="page",
            )
            extraction_failed = False
            try:
                extracted = reader.pages[page_index].extract_text()
                raw_text = extracted if extracted is not None else ""
            except Exception as error:
                raw_text = ""
                extraction_failed = True
                failed_pages += 1
                warnings.append(
                    ParseWarning(
                        code=PDF_PAGE_EXTRACTION_FAILED,
                        message=f"Text extraction failed for PDF page {page_number}",
                        severity=WarningSeverity.ERROR,
                        provenance=page_provenance,
                        details={
                            "page_number": page_number,
                            **_exception_details(error),
                        },
                    )
                )

            blocks.append(
                ContentBlock(
                    block_type=BlockType.PARAGRAPH,
                    block_id=f"b{block_index}",
                    raw_text=raw_text,
                    text=normalize_text(raw_text),
                    provenance=page_provenance,
                    attributes={
                        "pdf_page_text": True,
                        "layout_structure_preserved": False,
                        "extraction_failed": extraction_failed,
                    },
                )
            )
            if page_index < page_count - 1:
                break_index = len(blocks)
                blocks.append(
                    ContentBlock(
                        block_type=BlockType.PAGE_BREAK,
                        block_id=f"b{break_index}",
                        provenance=_provenance(
                            context,
                            source_index=break_index,
                            page_number=page_number,
                            dom_path=f"/pages/{page_number}/end",
                            tag="page-break",
                        ),
                        attributes={
                            "after_page_number": page_number,
                            "before_page_number": page_number + 1,
                        },
                    )
                )

        content = DocumentContent(
            title=title,
            raw_title=raw_title,
            block_sequence=blocks,
            title_provenance=title_provenance,
            provenance=file_provenance,
            attributes={
                "source_format": "pdf",
                "ocr_used": False,
                "table_structure_preserved": False,
                "page_count": page_count,
            },
        )
        content.derive_body_text()

        if page_count == 0:
            warnings.append(
                ParseWarning(
                    code=PDF_NO_PAGES,
                    message="The PDF contains no pages",
                    severity=WarningSeverity.ERROR,
                    provenance=file_provenance,
                )
            )

        if not content.body_text.strip():
            warnings.append(
                ParseWarning(
                    code=EMPTY_BODY,
                    message=(
                        "No extractable PDF text was found; OCR is intentionally "
                        "disabled"
                    ),
                    provenance=file_provenance,
                    details={"ocr_used": False, "page_count": page_count},
                )
            )

        if page_count == 0 or (page_count > 0 and failed_pages == page_count):
            status = ParseStatus.FAILED
        elif failed_pages or metadata_failed or not content.body_text.strip():
            status = ParseStatus.PARTIAL
        else:
            status = ParseStatus.SUCCESS

        source_metadata["page_extraction_failure_count"] = failed_pages
        return ParseResult(
            source_file=source_file,
            parse_status=status,
            content=content,
            warnings=warnings,
            parser_name=self.parser_name,
            source_metadata=source_metadata,
        )


__all__ = [
    "EMPTY_BODY",
    "PDF_EMPTY_PASSWORD_DECRYPTED",
    "PDF_ENCRYPTED",
    "PDF_METADATA_READ_FAILED",
    "PDF_NO_PAGES",
    "PDF_PAGE_EXTRACTION_FAILED",
    "PDF_PARSE_FAILED",
    "TITLE_NOT_FOUND",
    "PdfParser",
]
