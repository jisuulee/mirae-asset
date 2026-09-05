"""Shared configuration for corpus inventory scans."""

from __future__ import annotations

from pathlib import Path

DEFAULT_PREFIX_BYTES = 64 * 1024
DEFAULT_DECODE_CHUNK_BYTES = 1024 * 1024
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[2] / "reports"

MANIFEST_FILENAME = "manifest.jsonl"
RAW_DIRECTORY_NAME = "raw"

ID_FIELDS = frozenset({"doc_id", "corp_code", "stock_code", "rcept_no"})
TEXT_FORMATS = frozenset({"xml", "html", "json", "csv", "markdown"})

EXPECTED_FORMAT_BY_EXTENSION = {
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".json": "json",
    ".jsonl": "json",
    ".pdf": "pdf",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".md": "markdown",
    ".markdown": "markdown",
}
