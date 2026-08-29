"""Isolated page-text fallback for periodic disclosures available only as PDF."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .table_extractor import clean_text
from .xml_parser import sha256_file


def parse_pdf(path: Path, *, receipt_no: str, source_file: str) -> dict[str, Any]:
    reader = PdfReader(str(path))
    blocks: list[dict[str, Any]] = []
    warnings = ["PDF_FALLBACK_USED", "PDF_TABLE_EXTRACTION_UNAVAILABLE"]
    for page_number, page in enumerate(reader.pages, start=1):
        text = clean_text(page.extract_text() or "")
        provenance = {
            "source_file": source_file,
            "file_role": "main",
            "source_index": None,
            "dom_path": None,
            "tag": "pdf-page",
            "source_line": None,
            "page_number": page_number,
            "attributes": {},
        }
        if text:
            blocks.append(
                {
                    "block_id": f"{receipt_no}:b{len(blocks):07d}",
                    "block_type": "paragraph",
                    "text": text,
                    "source_file": source_file,
                    "source_locator": f"page:{page_number}",
                    "source_line": None,
                    "provenance": provenance,
                    "table_id": None,
                    "attributes": {"pdf_page": page_number},
                }
            )
        blocks.append(
            {
                "block_id": f"{receipt_no}:b{len(blocks):07d}",
                "block_type": "page_break",
                "text": "",
                "source_file": source_file,
                "source_locator": f"page:{page_number}",
                "source_line": None,
                "provenance": provenance,
                "table_id": None,
                "attributes": {"pdf_page": page_number},
            }
        )
    return {
        "blocks": blocks,
        "tables": [],
        "source_sha256": sha256_file(path),
        "parse_status": "partial",
        "warning_codes": warnings,
    }
