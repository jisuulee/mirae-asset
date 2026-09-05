"""Manifest-level Phase 2 corpus parsing and evidence-preserving assembly."""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any, Collection, TextIO

from disclosure_preprocess.config import DEFAULT_PREFIX_BYTES, RAW_DIRECTORY_NAME
from disclosure_preprocess.format_detector import (
    detect_declared_charset,
    detect_format_from_bytes,
)
from disclosure_preprocess.inventory import (
    classify_periodic_file_role,
    extract_document_name,
)
from disclosure_preprocess.manifest import (
    ManifestInventory,
    load_manifest,
    parse_n_files,
    preserve_identifier_strings,
)
from disclosure_preprocess.models import (
    AlternativeRepresentation,
    BlockType,
    DisclosureDocument,
    ParseResult,
    ParseStatus,
    ParseWarning,
    Provenance,
    SourceFileRecord,
    WarningSeverity,
)
from disclosure_preprocess.parsers.base import BinaryPayload, ParseContext, read_binary_stream
from disclosure_preprocess.parsers.parser_factory import parse_payload
from disclosure_preprocess.source import CorpusSource, SourceFile, open_corpus_source
from disclosure_preprocess.utils import normalize_nfc


LOGGER = logging.getLogger(__name__)

ATTACHMENT_ROLE_UNKNOWN = "ATTACHMENT_ROLE_UNKNOWN"
HTML_VIEWER_NOT_SUBSTANTIVE = "HTML_VIEWER_NOT_SUBSTANTIVE"
MAIN_DOCUMENT_ROLE_UNRESOLVED = "MAIN_DOCUMENT_ROLE_UNRESOLVED"
NO_SUBSTANTIVE_MAIN_DOCUMENT = "NO_SUBSTANTIVE_MAIN_DOCUMENT"
PDF_FALLBACK_USED = "PDF_FALLBACK_USED"
PHYSICAL_FILE_READ_FAILED = "PHYSICAL_FILE_READ_FAILED"
SOURCE_FILE_COUNT_MISMATCH = "SOURCE_FILE_COUNT_MISMATCH"
SOURCE_FILE_MISSING = "SOURCE_FILE_MISSING"
SOURCE_METADATA_MISMATCH = "SOURCE_METADATA_MISMATCH"
UNMATCHED_RAW_FILE = "UNMATCHED_RAW_FILE"

_GROUP_LISTING_RE = re.compile(r"^list_[^/]+\.json$", re.IGNORECASE)
_CORE_MANIFEST_FIELDS = (
    "corp_code",
    "corp_name",
    "listed_name",
    "stock_code",
    "industry",
    "sector",
    "doc_group",
    "doc_subtype",
    "report_nm",
    "is_correction",
    "rcept_dt",
    "flr_nm",
    "base_year",
    "base_month",
    "file_path",
    "file_format",
    "n_files",
)


def _status_text(status: ParseStatus | str) -> str:
    return status.value if isinstance(status, ParseStatus) else str(status)


def _counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _nested_counter(
    values: dict[str, Counter[str]],
) -> dict[str, dict[str, int]]:
    return {key: _counter(values[key]) for key in sorted(values)}


def _append_example(target: list[dict[str, Any]], value: dict[str, Any], limit: int = 50) -> None:
    if len(target) < limit:
        target.append(value)


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


@dataclass(slots=True)
class _RunStatistics:
    started_at: datetime
    source_kind: str
    source_path: str
    logical_root: str
    manifest_document_count: int
    expected_document_count: int | None
    document_limit: int | None
    document_ids: list[str] | None = None
    output_line_count: int = 0
    status_counts: Counter[str] = field(default_factory=Counter)
    group_counts: Counter[str] = field(default_factory=Counter)
    status_by_group: dict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    physical_format_counts: Counter[str] = field(default_factory=Counter)
    physical_status_counts: Counter[str] = field(default_factory=Counter)
    physical_status_by_format: dict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    role_counts: Counter[str] = field(default_factory=Counter)
    warning_counts: Counter[str] = field(default_factory=Counter)
    warning_severity_counts: Counter[str] = field(default_factory=Counter)
    main_title_count: int = 0
    main_body_count: int = 0
    main_section_document_count: int = 0
    main_section_count: int = 0
    main_table_document_count: int = 0
    main_table_count: int = 0
    all_block_count: int = 0
    all_section_count: int = 0
    all_table_count: int = 0
    provenance_item_count: int = 0
    provenance_missing_count: int = 0
    provenance_counts_by_kind: Counter[str] = field(default_factory=Counter)
    provenance_missing_by_kind: Counter[str] = field(default_factory=Counter)
    attachment_count: int = 0
    source_file_count: int = 0
    sha256_count: int = 0
    pdf_fallback_count: int = 0
    source_metadata_mismatch_count: int = 0
    title_missing_examples: list[dict[str, Any]] = field(default_factory=list)
    empty_body_examples: list[dict[str, Any]] = field(default_factory=list)
    no_section_examples: list[dict[str, Any]] = field(default_factory=list)
    failed_examples: list[dict[str, Any]] = field(default_factory=list)
    partial_examples: list[dict[str, Any]] = field(default_factory=list)
    malformed_table_examples: list[dict[str, Any]] = field(default_factory=list)
    unknown_role_examples: list[dict[str, Any]] = field(default_factory=list)
    source_mismatch_examples: list[dict[str, Any]] = field(default_factory=list)
    example_by_kind: dict[str, dict[str, Any]] = field(default_factory=dict)
    auxiliary_file_count: int = 0
    auxiliary_status_counts: Counter[str] = field(default_factory=Counter)
    auxiliary_root_type_counts: Counter[str] = field(default_factory=Counter)
    auxiliary_record_count: int = 0
    unmatched_raw_file_count: int = 0
    unmatched_raw_examples: list[str] = field(default_factory=list)
    source_issue_count: int = 0
    source_issue_examples: list[dict[str, str]] = field(default_factory=list)

    def add_warning(self, warning: ParseWarning) -> None:
        self.warning_counts[warning.code] += 1
        self.warning_severity_counts[_status_text(warning.severity)] += 1
        if warning.code == PDF_FALLBACK_USED:
            self.pdf_fallback_count += 1
        if warning.code == SOURCE_METADATA_MISMATCH:
            self.source_metadata_mismatch_count += 1

    def add_auxiliary(self, result: ParseResult) -> None:
        self.auxiliary_file_count += 1
        self.auxiliary_status_counts[_status_text(result.parse_status)] += 1
        root_type = str(result.source_metadata.get("root_type") or "unknown")
        self.auxiliary_root_type_counts[root_type] += 1
        record_count = result.source_metadata.get("record_count")
        if isinstance(record_count, int):
            self.auxiliary_record_count += record_count
        for warning in result.warnings:
            self.add_warning(warning)

    def add_disclosure(
        self, disclosure: DisclosureDocument, manifest_record: dict[str, Any]
    ) -> None:
        self.output_line_count += 1
        status = _status_text(disclosure.parse_status)
        group = str(manifest_record.get("doc_group") or "unknown")
        self.status_counts[status] += 1
        self.group_counts[group] += 1
        self.status_by_group[group][status] += 1
        self.attachment_count += len(disclosure.attachments)

        results = _physical_results(disclosure)
        for result in results:
            source = result.source_file
            detected = source.detected_format or "unknown"
            result_status = _status_text(result.parse_status)
            self.source_file_count += 1
            self.physical_format_counts[detected] += 1
            self.physical_status_counts[result_status] += 1
            self.physical_status_by_format[detected][result_status] += 1
            self.role_counts[source.role or "unknown"] += 1
            if source.sha256:
                self.sha256_count += 1
            if result.content is not None:
                content = result.content

                def account_provenance(kind: str, provenance: Provenance | None) -> None:
                    self.provenance_item_count += 1
                    self.provenance_counts_by_kind[kind] += 1
                    if provenance is None:
                        self.provenance_missing_count += 1
                        self.provenance_missing_by_kind[kind] += 1

                self.all_block_count += len(content.block_sequence)
                self.all_section_count += len(content.sections)
                self.all_table_count += len(content.tables)
                account_provenance("document", content.provenance)
                if content.title:
                    account_provenance("title", content.title_provenance)
                for section in content.sections:
                    account_provenance("section", section.provenance)
                for block in content.block_sequence:
                    account_provenance("block", block.provenance)
                    for item in block.items:
                        account_provenance("list_item", item.provenance)
                for table in result.content.tables:
                    account_provenance("table", table.provenance)
                    for row in table.raw_rows:
                        for cell in row:
                            account_provenance("table_cell", cell.provenance)
            for warning in result.warnings:
                self.add_warning(warning)

        for warning in disclosure.warnings:
            self.add_warning(warning)

        main = disclosure.main_document
        content = main.content if main is not None else None
        doc_ref = {
            "doc_id": disclosure.doc_id,
            "rcept_no": disclosure.rcept_no,
            "doc_group": group,
            "report_nm": manifest_record.get("report_nm"),
        }
        if content is not None and content.title:
            self.main_title_count += 1
        else:
            _append_example(self.title_missing_examples, doc_ref)
        if content is not None and (
            content.body_text.strip()
            or any(_table_has_source_text(table) for table in content.tables)
        ):
            self.main_body_count += 1
        else:
            _append_example(self.empty_body_examples, doc_ref)
        if content is not None and content.sections:
            self.main_section_document_count += 1
            self.main_section_count += len(content.sections)
        else:
            _append_example(self.no_section_examples, doc_ref)
        if content is not None and content.tables:
            self.main_table_document_count += 1
            self.main_table_count += len(content.tables)

        if status == ParseStatus.FAILED.value:
            _append_example(self.failed_examples, doc_ref)
        elif status == ParseStatus.PARTIAL.value:
            _append_example(self.partial_examples, doc_ref)

        warning_codes = {
            warning.code
            for warning in disclosure.warnings
        } | {
            warning.code for result in results for warning in result.warnings
        }
        if "TABLE_SPAN_NORMALIZATION_FAILED" in warning_codes or "MALFORMED_TABLE" in warning_codes:
            _append_example(self.malformed_table_examples, doc_ref)
        if ATTACHMENT_ROLE_UNKNOWN in warning_codes:
            _append_example(self.unknown_role_examples, doc_ref)
        if SOURCE_METADATA_MISMATCH in warning_codes:
            _append_example(self.source_mismatch_examples, doc_ref)

        self._consider_example(disclosure, manifest_record)

    def _consider_example(
        self, disclosure: DisclosureDocument, manifest_record: dict[str, Any]
    ) -> None:
        main = disclosure.main_document
        if main is None or main.content is None:
            return
        group = str(manifest_record.get("doc_group") or "unknown")
        if any(warning.code == PDF_FALLBACK_USED for warning in disclosure.warnings):
            kind = "pdf_fallback"
        elif group == "periodic" and disclosure.attachments:
            kind = "periodic_multi_file"
        else:
            kind = group
        if kind in self.example_by_kind:
            return
        content = main.content
        preview = content.body_text.strip().replace("\n", " ")[:180]
        if not preview and content.tables:
            preview_values: list[str] = []
            for row in content.tables[0].raw_rows[:2]:
                preview_values.extend(cell.raw_text.strip() for cell in row[:4])
            preview = " | ".join(value for value in preview_values if value)[:180]
        self.example_by_kind[kind] = {
            "kind": kind,
            "doc_id": disclosure.doc_id,
            "rcept_no": disclosure.rcept_no,
            "corp_name": manifest_record.get("corp_name"),
            "report_nm": manifest_record.get("report_nm"),
            "source_title": content.title,
            "main_format": main.source_file.detected_format,
            "main_role": main.source_file.role,
            "attachment_roles": [item.source_file.role for item in disclosure.attachments],
            "section_count": len(content.sections),
            "table_count": len(content.tables),
            "source_path": main.source_file.relative_path,
            "text_preview": preview,
        }

    def to_dict(
        self,
        *,
        finished_at: datetime,
        output_files: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        processed_target = (
            len(self.document_ids)
            if self.document_ids is not None
            else min(self.manifest_document_count, self.document_limit)
            if self.document_limit is not None
            else self.manifest_document_count
        )
        complete_corpus_parse = (
            self.document_limit is None and self.document_ids is None
        )
        return {
            "schema_version": "2.0.0",
            "run": {
                "started_at_utc": self.started_at.isoformat(),
                "finished_at_utc": finished_at.isoformat(),
                "duration_seconds": round(
                    (finished_at - self.started_at).total_seconds(), 3
                ),
                "python_version": platform.python_version(),
                "python_executable": sys.executable,
                "platform": platform.platform(),
                "dependencies": {
                    "lxml": _package_version("lxml"),
                    "pypdf": _package_version("pypdf"),
                },
                "document_limit": self.document_limit,
                "document_ids": self.document_ids,
                "complete_corpus_parse": complete_corpus_parse,
            },
            "source": {
                "kind": self.source_kind,
                "path": self.source_path,
                "logical_root": self.logical_root,
                "source_issue_count": self.source_issue_count,
                "source_issue_examples": self.source_issue_examples,
            },
            "logical_documents": {
                "manifest_document_count": self.manifest_document_count,
                "expected_document_count": self.expected_document_count,
                "processed_target_count": processed_target,
                "output_line_count": self.output_line_count,
                "matches_manifest_count": (
                    complete_corpus_parse
                    and self.output_line_count == self.manifest_document_count
                ),
                "matches_expected_count": (
                    self.expected_document_count is None
                    or self.manifest_document_count == self.expected_document_count
                ),
                "status_counts": _counter(self.status_counts),
                "doc_group_counts": _counter(self.group_counts),
                "status_by_doc_group": _nested_counter(self.status_by_group),
            },
            "physical_files": {
                "parsed_file_count": self.source_file_count,
                "sha256_count": self.sha256_count,
                "detected_format_counts": _counter(self.physical_format_counts),
                "parse_status_counts": _counter(self.physical_status_counts),
                "status_by_detected_format": _nested_counter(
                    self.physical_status_by_format
                ),
                "role_counts": _counter(self.role_counts),
                "attachment_count": self.attachment_count,
            },
            "extraction": {
                "main_title_document_count": self.main_title_count,
                "main_title_missing_count": self.output_line_count - self.main_title_count,
                "main_body_document_count": self.main_body_count,
                "main_empty_body_count": self.output_line_count - self.main_body_count,
                "main_section_document_count": self.main_section_document_count,
                "main_no_section_count": (
                    self.output_line_count - self.main_section_document_count
                ),
                "main_section_count": self.main_section_count,
                "main_table_document_count": self.main_table_document_count,
                "main_table_count": self.main_table_count,
                "all_physical_block_count": self.all_block_count,
                "all_physical_section_count": self.all_section_count,
                "all_physical_table_count": self.all_table_count,
                "provenance_item_count": self.provenance_item_count,
                "provenance_missing_count": self.provenance_missing_count,
                "provenance_counts_by_kind": _counter(
                    self.provenance_counts_by_kind
                ),
                "provenance_missing_by_kind": _counter(
                    self.provenance_missing_by_kind
                ),
            },
            "warnings": {
                "occurrence_count": sum(self.warning_counts.values()),
                "counts_by_code": _counter(self.warning_counts),
                "counts_by_severity": _counter(self.warning_severity_counts),
                "pdf_fallback_count": self.pdf_fallback_count,
                "source_metadata_mismatch_count": self.source_metadata_mismatch_count,
            },
            "suspicious_documents": {
                "title_missing_examples": self.title_missing_examples,
                "empty_body_examples": self.empty_body_examples,
                "no_section_examples": self.no_section_examples,
                "failed_examples": self.failed_examples,
                "partial_examples": self.partial_examples,
                "malformed_table_examples": self.malformed_table_examples,
                "unknown_attachment_role_examples": self.unknown_role_examples,
                "source_metadata_mismatch_examples": self.source_mismatch_examples,
            },
            "auxiliary_json": {
                "file_count": self.auxiliary_file_count,
                "parse_status_counts": _counter(self.auxiliary_status_counts),
                "root_type_counts": _counter(self.auxiliary_root_type_counts),
                "record_count": self.auxiliary_record_count,
                "included_as_disclosures": False,
            },
            "unmatched_raw_file_count": self.unmatched_raw_file_count,
            "unmatched_raw_file_examples": self.unmatched_raw_examples,
            "parsing_examples": list(self.example_by_kind.values())[:5],
            "output_files": output_files,
            "guarantees": {
                "manifest_row_is_logical_unit": True,
                "group_listing_json_excluded": True,
                "dispatch_is_content_based": True,
                "strict_utf8_first": True,
                "silent_decode_ignore_or_replace": False,
                "semantic_inference_or_summarization": False,
                "source_sha256_recorded": (
                    self.sha256_count == self.source_file_count
                ),
                "block_table_provenance_recorded": (
                    self.provenance_item_count > 0
                    and self.provenance_missing_count == 0
                ),
                "structured_content_provenance_recorded": (
                    self.provenance_item_count > 0
                    and self.provenance_missing_count == 0
                ),
                "body_text_derived_from_ordered_blocks": True,
                "body_text_char_level_reverse_mapping": False,
                "raw_source_mutated": False,
            },
        }


def _is_raw(entry: SourceFile) -> bool:
    return bool(
        PurePosixPath(entry.relative_path).parts
        and PurePosixPath(entry.relative_path).parts[0] == RAW_DIRECTORY_NAME
    )


def _is_group_listing(entry: SourceFile) -> bool:
    return _is_raw(entry) and bool(
        _GROUP_LISTING_RE.match(PurePosixPath(entry.relative_path).name)
    )


def _role_for_entry(
    record: dict[str, Any],
    entry: SourceFile,
    payload: BinaryPayload,
    detected_format: str,
) -> str:
    if str(record.get("doc_group") or "") != "periodic":
        return "main"
    declared = detect_declared_charset(payload.data[:DEFAULT_PREFIX_BYTES])
    document_name = extract_document_name(
        payload.data[:DEFAULT_PREFIX_BYTES], declared
    )
    return classify_periodic_file_role(
        document_name,
        PurePosixPath(entry.relative_path).name,
        str(record.get("rcept_no") or ""),
        detected_format,
    )


def _fallback_role(record: dict[str, Any], entry: SourceFile) -> str:
    if str(record.get("doc_group") or "") != "periodic":
        return "main"
    filename = PurePosixPath(entry.relative_path).name
    return classify_periodic_file_role(
        None,
        filename,
        str(record.get("rcept_no") or ""),
        PurePosixPath(filename).suffix.lstrip(".").lower(),
    )


def _failed_entry_result(
    entry: SourceFile,
    record: dict[str, Any],
    exc: BaseException,
) -> ParseResult:
    role = _fallback_role(record, entry)
    source_file = SourceFileRecord(
        relative_path=entry.relative_path,
        original_path=entry.source_path,
        role=role,
        detected_format="unknown",
        size_bytes=entry.size,
    )
    warning = ParseWarning(
        code=PHYSICAL_FILE_READ_FAILED,
        message=f"The physical source file could not be read: {exc}",
        severity=WarningSeverity.ERROR,
        provenance=Provenance(
            source_file=entry.relative_path,
            file_role=role,
        ),
        details={"exception_type": type(exc).__name__},
    )
    return ParseResult(
        source_file=source_file,
        parse_status=ParseStatus.FAILED,
        warnings=[warning],
        parser_name=None,
    )


def _parse_entry(entry: SourceFile, record: dict[str, Any]) -> ParseResult:
    try:
        with entry.open() as stream:
            payload = read_binary_stream(stream)
    except Exception as exc:  # file-level isolation is intentional
        LOGGER.exception("Unable to read %s", entry.relative_path)
        return _failed_entry_result(entry, record, exc)

    detected = detect_format_from_bytes(
        payload.data[:DEFAULT_PREFIX_BYTES],
        extension=PurePosixPath(entry.relative_path).suffix,
    )
    role = _role_for_entry(record, entry, payload, detected)
    context = ParseContext(
        relative_path=entry.relative_path,
        original_path=entry.source_path,
        detected_format=detected,
        role=role,
        size_bytes=entry.size,
        manifest_metadata=record,
    )
    return parse_payload(payload, context)


def _physical_results(disclosure: DisclosureDocument) -> list[ParseResult]:
    if disclosure.physical_results:
        return list(disclosure.physical_results)
    return (
        ([disclosure.main_document] if disclosure.main_document is not None else [])
        + list(disclosure.attachments)
    )


def _table_has_source_text(table: Any) -> bool:
    return any(
        cell.raw_text.strip()
        for row in table.raw_rows
        for cell in row
    )


def _content_is_substantive(result: ParseResult | None) -> bool:
    if result is None or result.content is None or result.parse_status == ParseStatus.FAILED:
        return False
    content = result.content
    if result.source_file.detected_format == "pdf":
        return bool(content.body_text.strip())
    if result.source_file.role == "viewer_html":
        explicit_paragraph = any(
            str(block.block_type) == BlockType.PARAGRAPH.value
            and block.provenance is not None
            and (block.provenance.tag or "")
            in {"p", "paragraph", "text", "pre", "blockquote"}
            and bool((block.text or block.raw_text or "").strip())
            for block in content.block_sequence
        )
        nonempty_table = any(_table_has_source_text(table) for table in content.tables)
        selected_tag_counts = result.source_metadata.get("selected_tag_counts")
        selected_tag_counts = (
            selected_tag_counts if isinstance(selected_tag_counts, dict) else {}
        )
        structurally_clean_long_text = (
            len(content.body_text.strip()) >= 500
            and int(selected_tag_counts.get("script", 0) or 0) <= 1
            and int(selected_tag_counts.get("iframe", 0) or 0) == 0
        )
        return explicit_paragraph or nonempty_table or structurally_clean_long_text
    return bool(
        content.body_text.strip()
        or any(_table_has_source_text(table) for table in content.tables)
    )


def _warning(
    code: str,
    message: str,
    *,
    source_file: str | None = None,
    role: str | None = None,
    severity: WarningSeverity = WarningSeverity.WARNING,
    details: dict[str, Any] | None = None,
) -> ParseWarning:
    provenance = (
        Provenance(source_file=source_file, file_role=role)
        if source_file is not None
        else None
    )
    return ParseWarning(
        code=code,
        message=message,
        severity=severity,
        provenance=provenance,
        details=details or {},
    )


def _mark_viewer_non_substantive(result: ParseResult) -> None:
    if any(warning.code == HTML_VIEWER_NOT_SUBSTANTIVE for warning in result.warnings):
        return
    result.warnings.append(
        _warning(
            HTML_VIEWER_NOT_SUBSTANTIVE,
            "The viewer HTML contains no substantive paragraphs or table cells",
            source_file=result.source_file.relative_path,
            role=result.source_file.role,
        )
    )
    if result.parse_status == ParseStatus.SUCCESS:
        result.parse_status = ParseStatus.PARTIAL


def _append_metadata_mismatch_warnings(
    results: list[ParseResult], record: dict[str, Any], document_warnings: list[ParseWarning]
) -> None:
    def compact(value: str) -> str:
        return "".join(normalize_nfc(value).split()).casefold()

    def company_key(value: str) -> str:
        """Remove only conventional leading/trailing Korean legal designators.

        DART source documents commonly spell ``삼성전자`` as
        ``삼성전자주식회사``.  That is not a conflicting company identity, but
        arbitrary punctuation or other company-name tokens must not be erased.
        """

        key = compact(value)
        designators = ("주식회사", "(주)", "㈜")
        changed = True
        while changed and key:
            changed = False
            for designator in designators:
                if key.startswith(designator):
                    key = key[len(designator) :]
                    changed = True
                if key.endswith(designator):
                    key = key[: -len(designator)]
                    changed = True
        return key

    for result in results:
        comparisons = [
            ("rcept_no", "rcept_no", "exact"),
            ("company_name", "corp_name", "company"),
        ]
        # Audit attachments are intentionally separate documents whose
        # DOCUMENT-NAME differs from the logical disclosure's report_nm.
        if result.source_file.role in {"main", "viewer_html", "primary_pdf"}:
            comparisons.append(("document_name", "report_nm", "contained"))
        for source_field, manifest_field, comparison in comparisons:
            source_value = str(result.source_metadata.get(source_field) or "")
            manifest_value = str(record.get(manifest_field) or "")
            if not source_value or not manifest_value:
                continue
            source_compact = compact(source_value)
            manifest_compact = compact(manifest_value)
            matches = source_compact == manifest_compact
            if comparison == "company":
                matches = company_key(source_value) == company_key(manifest_value)
            if comparison == "contained":
                matches = (
                    source_compact in manifest_compact
                    or manifest_compact in source_compact
                )
            if matches:
                continue
            document_warnings.append(
                _warning(
                    SOURCE_METADATA_MISMATCH,
                    "Source metadata differs from authoritative manifest metadata; the manifest value was retained",
                    source_file=result.source_file.relative_path,
                    role=result.source_file.role,
                    details={
                        "source_field": source_field,
                        "manifest_field": manifest_field,
                        "manifest_value": manifest_value,
                        "source_value": source_value,
                    },
                )
            )


def _assemble_disclosure(
    record: dict[str, Any], results: list[ParseResult]
) -> DisclosureDocument:
    document_warnings: list[ParseWarning] = []
    viewer_results = [item for item in results if item.source_file.role == "viewer_html"]
    pdf_results = [item for item in results if item.source_file.role == "primary_pdf"]
    main: ParseResult | None = None

    if viewer_results and pdf_results:
        viewer = viewer_results[0]
        pdf = pdf_results[0]
        if _content_is_substantive(viewer):
            main = viewer
        elif _content_is_substantive(pdf):
            _mark_viewer_non_substantive(viewer)
            main = pdf
            document_warnings.append(
                _warning(
                    PDF_FALLBACK_USED,
                    "Viewer HTML was not substantive, so the source PDF became the canonical main document",
                    source_file=pdf.source_file.relative_path,
                    role=pdf.source_file.role,
                    details={"viewer_html": viewer.source_file.relative_path},
                )
            )
        else:
            _mark_viewer_non_substantive(viewer)
            main = next(
                (item for item in (viewer, pdf) if item.parse_status != ParseStatus.FAILED),
                viewer,
            )

    if main is None:
        explicit_main = [item for item in results if item.source_file.role == "main"]
        if explicit_main:
            main = explicit_main[0]
            if len(explicit_main) > 1:
                document_warnings.append(
                    _warning(
                        MAIN_DOCUMENT_ROLE_UNRESOLVED,
                        "Multiple physical files were classified as main; the first path was selected",
                        details={
                            "candidate_paths": [
                                item.source_file.relative_path for item in explicit_main
                            ]
                        },
                    )
                )
        elif (
            len(results) == 1
            and str(record.get("file_format") or "").casefold() == "pdf+html"
            and results[0].source_file.role in {"viewer_html", "primary_pdf"}
        ):
            main = results[0]
            document_warnings.append(
                _warning(
                    MAIN_DOCUMENT_ROLE_UNRESOLVED,
                    "Only one representation of the PDF+HTML pair was available and was selected",
                    source_file=main.source_file.relative_path,
                    role=main.source_file.role,
                )
            )
        elif results:
            document_warnings.append(
                _warning(
                    MAIN_DOCUMENT_ROLE_UNRESOLVED,
                    "No physical file had an admissible main role; attachments were not promoted to canonical main",
                    severity=WarningSeverity.ERROR,
                    details={
                        "available_roles": [
                            item.source_file.role for item in results
                        ]
                    },
                )
            )

    representation_roles = {"viewer_html", "primary_pdf"}
    alternative_results = [
        item
        for item in results
        if item is not main and item.source_file.role in representation_roles
    ]
    attachments = [
        item
        for item in results
        if item is not main and item not in alternative_results
    ]
    for item in attachments:
        if item.source_file.role in {"unknown", "attachment"}:
            document_warnings.append(
                _warning(
                    ATTACHMENT_ROLE_UNKNOWN,
                    "The attachment role could not be classified more specifically",
                    source_file=item.source_file.relative_path,
                    role=item.source_file.role,
                )
            )

    for item in results:
        item.source_file.is_canonical = item is main
        item.source_file.attributes["parse_status"] = _status_text(item.parse_status)
        item.source_file.attributes["representation_disposition"] = (
            "canonical"
            if item is main
            else "alternative"
            if item in alternative_results
            else "attachment"
        )

    expected_files = parse_n_files(record.get("n_files"))
    if expected_files is not None and expected_files != len(results):
        document_warnings.append(
            _warning(
                SOURCE_FILE_COUNT_MISMATCH,
                "Manifest n_files differs from the number of linked physical files",
                severity=WarningSeverity.ERROR,
                details={
                    "manifest_n_files": expected_files,
                    "linked_file_count": len(results),
                },
            )
        )
    if not results:
        document_warnings.append(
            _warning(
                SOURCE_FILE_MISSING,
                "No physical source file was linked to this manifest row",
                severity=WarningSeverity.ERROR,
                details={"manifest_file_path": record.get("file_path")},
            )
        )

    _append_metadata_mismatch_warnings(results, record, document_warnings)
    disclosure = DisclosureDocument(
        doc_id=str(record.get("doc_id") or ""),
        rcept_no=str(record.get("rcept_no") or ""),
        metadata={
            "authority": "manifest",
            "manifest": preserve_identifier_strings(record),
        },
        main_document=main,
        attachments=attachments,
        alternative_representations=[
            AlternativeRepresentation(
                source_file=item.source_file,
                parse_status=item.parse_status,
                parser_name=item.parser_name,
                source_metadata=item.source_metadata,
                warnings=item.warnings,
            )
            for item in alternative_results
        ],
        source_files=[item.source_file for item in results],
        warnings=document_warnings,
        physical_results=list(results),
    )
    disclosure.refresh_parse_status()

    if main is None or not _content_is_substantive(main):
        disclosure.warnings.append(
            _warning(
                NO_SUBSTANTIVE_MAIN_DOCUMENT,
                "No substantive canonical main document could be established",
                source_file=(main.source_file.relative_path if main else None),
                role=(main.source_file.role if main else None),
                severity=WarningSeverity.ERROR,
            )
        )
        disclosure.parse_status = ParseStatus.FAILED
    elif any(
        warning.code
        in {
            ATTACHMENT_ROLE_UNKNOWN,
            MAIN_DOCUMENT_ROLE_UNRESOLVED,
            SOURCE_FILE_COUNT_MISMATCH,
            SOURCE_FILE_MISSING,
        }
        for warning in disclosure.warnings
    ):
        disclosure.parse_status = ParseStatus.PARTIAL
    return disclosure


def _serialized_disclosure(
    disclosure: DisclosureDocument, record: dict[str, Any]
) -> dict[str, Any]:
    payload = disclosure.to_dict()
    for field_name in _CORE_MANIFEST_FIELDS:
        payload[field_name] = record.get(field_name)
    return payload


def _write_json_line(stream: TextIO, value: dict[str, Any]) -> None:
    json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")


def _warning_rows(
    disclosure: DisclosureDocument,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for warning in disclosure.warnings:
        rows.append(
            {
                "doc_id": disclosure.doc_id,
                "rcept_no": disclosure.rcept_no,
                "scope": "logical_document",
                **warning.to_dict(),
            }
        )
    for result in _physical_results(disclosure):
        for warning in result.warnings:
            rows.append(
                {
                    "doc_id": disclosure.doc_id,
                    "rcept_no": disclosure.rcept_no,
                    "scope": "physical_file",
                    "source_file": result.source_file.relative_path,
                    "role": result.source_file.role,
                    **warning.to_dict(),
                }
            )
    return rows


def _failure_row(disclosure: DisclosureDocument) -> dict[str, Any]:
    return {
        "doc_id": disclosure.doc_id,
        "rcept_no": disclosure.rcept_no,
        "parse_status": _status_text(disclosure.parse_status),
        "main_source_file": (
            disclosure.main_document.source_file.relative_path
            if disclosure.main_document is not None
            else None
        ),
        "physical_file_statuses": [
            {
                "source_file": result.source_file.relative_path,
                "role": result.source_file.role,
                "detected_format": result.source_file.detected_format,
                "parse_status": _status_text(result.parse_status),
                "warning_codes": [warning.code for warning in result.warnings],
            }
            for result in _physical_results(disclosure)
        ],
        "warning_codes": [warning.code for warning in disclosure.warnings],
    }


def _stage_files(output_dir: Path) -> tuple[Path, dict[str, Path]]:
    stage = Path(tempfile.mkdtemp(prefix=".phase2-", dir=output_dir))
    return stage, {
        "parsed_disclosures": stage / "parsed_disclosures.jsonl",
        "parse_warnings": stage / "parse_warnings.jsonl",
        "parse_failures": stage / "parse_failures.jsonl",
    }


def _publish_stage(stage: Path, output_dir: Path, paths: dict[str, Path]) -> dict[str, Path]:
    backup_dir = stage / "previous-generation"
    backup_dir.mkdir()
    published: dict[str, Path] = {}
    backups: dict[Path, Path] = {}
    replaced_finals: list[Path] = []
    try:
        for staged_path in paths.values():
            final_path = output_dir / staged_path.name
            if final_path.exists() and final_path.is_dir():
                raise ValueError(f"Output artifact path is a directory: {final_path}")
            if final_path.exists() or final_path.is_symlink():
                backup_path = backup_dir / staged_path.name
                os.replace(final_path, backup_path)
                backups[final_path] = backup_path
        for key, staged_path in paths.items():
            final_path = output_dir / staged_path.name
            os.replace(staged_path, final_path)
            replaced_finals.append(final_path)
            published[key] = final_path
    except BaseException:
        for final_path in reversed(replaced_finals):
            final_path.unlink(missing_ok=True)
        for final_path, backup_path in backups.items():
            if backup_path.exists() or backup_path.is_symlink():
                os.replace(backup_path, final_path)
        raise
    shutil.rmtree(backup_dir)
    stage.rmdir()
    return published


def _validate_output_location(source: CorpusSource, output_dir: Path, report_dir: Path) -> None:
    source_path = source.location.resolve()
    for target in (output_dir.resolve(), report_dir.resolve()):
        if target == source_path:
            raise ValueError(f"Output directory collides with source: {target}")
    if source.kind != "directory" or source.corpus_root_path is None:
        return
    corpus_root = source.corpus_root_path.resolve()
    for target in (output_dir.resolve(), report_dir.resolve()):
        try:
            target.relative_to(corpus_root)
        except ValueError:
            continue
        raise ValueError(
            f"Output directory must not be inside the read-only corpus root: {target}"
        )


def _manifest_preconditions(
    manifest: ManifestInventory, expected_document_count: int | None
) -> None:
    summary = manifest.summary
    ambiguous = {
        "malformed_line_count": summary.get("malformed_line_count", 0),
        "duplicate_file_path_count": summary.get("duplicate_file_path_count", 0),
        "duplicate_doc_id_count": summary.get("duplicate_doc_id_count", 0),
        "invalid_file_path_count": summary.get("invalid_file_path_count", 0),
    }
    failures = {key: value for key, value in ambiguous.items() if value}
    if failures:
        raise ValueError(f"Manifest is ambiguous or malformed: {failures}")
    if expected_document_count is not None and len(manifest.records) != expected_document_count:
        raise ValueError(
            "Manifest document count does not match the required corpus count: "
            f"{len(manifest.records)} != {expected_document_count}"
        )


def _source_index(
    source: CorpusSource, manifest: ManifestInventory, entries: list[SourceFile]
) -> tuple[dict[str, list[SourceFile]], list[SourceFile], list[SourceFile]]:
    by_doc_id: dict[str, list[SourceFile]] = defaultdict(list)
    auxiliary: list[SourceFile] = []
    unmatched_raw: list[SourceFile] = []
    seen_paths: set[str] = set()
    for entry in entries:
        if entry.relative_path in seen_paths:
            raise ValueError(
                f"Duplicate canonical physical source path: {entry.relative_path}"
            )
        seen_paths.add(entry.relative_path)
        if PurePosixPath(entry.relative_path).name == "manifest.jsonl":
            continue
        record = manifest.match_file(entry.relative_path)
        if record is not None:
            by_doc_id[str(record.get("doc_id") or "")].append(entry)
        elif _is_group_listing(entry):
            auxiliary.append(entry)
        elif _is_raw(entry):
            unmatched_raw.append(entry)
    for values in by_doc_id.values():
        values.sort(key=lambda item: item.relative_path)
    auxiliary.sort(key=lambda item: item.relative_path)
    unmatched_raw.sort(key=lambda item: item.relative_path)
    return by_doc_id, auxiliary, unmatched_raw


def _parse_auxiliary(entry: SourceFile) -> ParseResult:
    try:
        with entry.open() as stream:
            payload = read_binary_stream(stream)
    except Exception as exc:
        return _failed_entry_result(entry, {}, exc)
    detected = detect_format_from_bytes(
        payload.data[:DEFAULT_PREFIX_BYTES],
        extension=PurePosixPath(entry.relative_path).suffix,
    )
    context = ParseContext(
        relative_path=entry.relative_path,
        original_path=entry.source_path,
        detected_format=detected,
        role="group_listing",
        size_bytes=entry.size,
    )
    return parse_payload(payload, context)


def _output_file_metadata(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
        }
        for key, path in paths.items()
    }


def parse_corpus(
    data_root: str | Path,
    *,
    output_dir: str | Path = Path("data/processed"),
    report_dir: str | Path = Path("reports"),
    expected_document_count: int | None = None,
    document_limit: int | None = None,
    document_ids: Collection[str] | None = None,
    progress_every: int = 50,
) -> dict[str, Any]:
    """Parse a DART corpus into one JSONL row per manifest disclosure."""

    if document_limit is not None and document_limit <= 0:
        raise ValueError("document_limit must be greater than zero")
    selected_document_ids = (
        {str(value) for value in document_ids if str(value)}
        if document_ids is not None
        else None
    )
    if selected_document_ids is not None and not selected_document_ids:
        raise ValueError("document_ids must contain at least one non-empty doc_id")
    if document_limit is not None and selected_document_ids is not None:
        raise ValueError("document_limit and document_ids are mutually exclusive")
    if progress_every < 0:
        raise ValueError("progress_every must be non-negative")
    output_path = Path(output_dir).resolve()
    reports_path = Path(report_dir).resolve()
    started_at = datetime.now(timezone.utc)

    with open_corpus_source(data_root) as source:
        _validate_output_location(source, output_path, reports_path)
        output_path.mkdir(parents=True, exist_ok=True)
        reports_path.mkdir(parents=True, exist_ok=True)
        entries = list(source.iter_files())
        manifest_entries = [
            entry
            for entry in entries
            if PurePosixPath(entry.relative_path).name == "manifest.jsonl"
        ]
        if len(manifest_entries) != 1:
            raise ValueError(
                f"Exactly one manifest.jsonl is required; found {len(manifest_entries)}"
            )
        manifest = load_manifest(manifest_entries[0])
        _manifest_preconditions(manifest, expected_document_count)
        if selected_document_ids is not None:
            known_ids = {
                str(record.get("doc_id") or "") for record in manifest.records
            }
            missing_ids = sorted(selected_document_ids - known_ids)
            if missing_ids:
                raise ValueError(
                    f"Unknown document_ids: {', '.join(missing_ids[:20])}"
                )
        by_doc_id, auxiliary, unmatched_raw = _source_index(source, manifest, entries)

        stats = _RunStatistics(
            started_at=started_at,
            source_kind=source.kind,
            source_path=str(source.location),
            logical_root=source.logical_root,
            manifest_document_count=len(manifest.records),
            expected_document_count=expected_document_count,
            document_limit=document_limit,
            document_ids=(
                sorted(selected_document_ids)
                if selected_document_ids is not None
                else None
            ),
            unmatched_raw_file_count=len(unmatched_raw),
            unmatched_raw_examples=[
                entry.relative_path for entry in unmatched_raw[:50]
            ],
            source_issue_count=len(source.discovery_issues),
            source_issue_examples=[
                {"stage": issue.stage, "path": issue.path, "error": issue.error}
                for issue in source.discovery_issues[:50]
            ],
        )

        stage, staged_paths = _stage_files(output_path)
        try:
            with (
                staged_paths["parsed_disclosures"].open("w", encoding="utf-8", newline="\n") as parsed_stream,
                staged_paths["parse_warnings"].open("w", encoding="utf-8", newline="\n") as warning_stream,
                staged_paths["parse_failures"].open("w", encoding="utf-8", newline="\n") as failure_stream,
            ):
                for auxiliary_entry in auxiliary:
                    result = _parse_auxiliary(auxiliary_entry)
                    stats.add_auxiliary(result)
                    for warning in result.warnings:
                        _write_json_line(
                            warning_stream,
                            {
                                "doc_id": None,
                                "rcept_no": None,
                                "scope": "auxiliary_json",
                                "source_file": result.source_file.relative_path,
                                **warning.to_dict(),
                            },
                        )

                if selected_document_ids is not None:
                    records = [
                        record
                        for record in manifest.records
                        if str(record.get("doc_id") or "")
                        in selected_document_ids
                    ]
                elif document_limit is not None:
                    records = manifest.records[:document_limit]
                else:
                    records = manifest.records
                total = len(records)
                for index, record in enumerate(records, start=1):
                    doc_id = str(record.get("doc_id") or "")
                    results = [
                        _parse_entry(entry, record)
                        for entry in by_doc_id.get(doc_id, [])
                    ]
                    disclosure = _assemble_disclosure(record, results)
                    _write_json_line(
                        parsed_stream,
                        _serialized_disclosure(disclosure, record),
                    )
                    for warning_row in _warning_rows(disclosure):
                        _write_json_line(warning_stream, warning_row)
                    if disclosure.parse_status != ParseStatus.SUCCESS:
                        _write_json_line(failure_stream, _failure_row(disclosure))
                    stats.add_disclosure(disclosure, record)
                    if progress_every and (
                        index % progress_every == 0 or index == total
                    ):
                        LOGGER.info(
                            "Parsed %d/%d logical disclosures (success=%d partial=%d failed=%d)",
                            index,
                            total,
                            stats.status_counts[ParseStatus.SUCCESS.value],
                            stats.status_counts[ParseStatus.PARTIAL.value],
                            stats.status_counts[ParseStatus.FAILED.value],
                        )
                    del results, disclosure

            published = _publish_stage(stage, output_path, staged_paths)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    finished_at = datetime.now(timezone.utc)
    output_files = _output_file_metadata(published)
    summary = stats.to_dict(finished_at=finished_at, output_files=output_files)

    # Imported late to keep report rendering independent of the parsing loop.
    from disclosure_preprocess.parser_reporting import write_parser_reports

    report_outputs = write_parser_reports(summary, reports_path)
    summary["report_files"] = {
        key: str(path.resolve()) for key, path in report_outputs.items()
    }
    return summary


__all__ = [
    "ATTACHMENT_ROLE_UNKNOWN",
    "HTML_VIEWER_NOT_SUBSTANTIVE",
    "PDF_FALLBACK_USED",
    "SOURCE_METADATA_MISMATCH",
    "parse_corpus",
]
