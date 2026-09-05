"""Streaming Phase 1 corpus inventory and format profiling."""

from __future__ import annotations

import codecs
import csv
import html
import json
import logging
import math
import os
import re
import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from .config import (
    DEFAULT_DECODE_CHUNK_BYTES,
    DEFAULT_PREFIX_BYTES,
    EXPECTED_FORMAT_BY_EXTENSION,
    RAW_DIRECTORY_NAME,
    TEXT_FORMATS,
)
from .format_detector import detect_declared_charset, detect_format_from_bytes
from .manifest import ManifestInventory, canonical_relative_path, load_manifest, parse_n_files
from .reporting import render_inventory_report
from .source import CorpusSource, SourceFile, open_corpus_source

LOGGER = logging.getLogger(__name__)

EncodingMode = Literal["all", "sample", "none"]

_RCEPT_NO_RE = re.compile(r"(?<!\d)(\d{14})(?!\d)")
_DOCUMENT_NAME_RE = re.compile(
    r"<DOCUMENT-NAME(?:\s[^>]*)?>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</DOCUMENT-NAME>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")

CSV_FIELDS = (
    "relative_path",
    "source_path",
    "scope",
    "file_scope",
    "extension",
    "detected_format",
    "expected_format_from_extension",
    "extension_format_mismatch",
    "file_size",
    "doc_group",
    "manifest_doc_id",
    "rcept_no",
    "rcept_no_inferable",
    "rcept_no_source",
    "declared_charset",
    "utf8_decodable",
    "contains_non_ascii",
    "charset_declaration_mismatch",
    "document_name",
    "periodic_file_role",
    "error",
)


@dataclass(slots=True)
class IssueCollector:
    max_examples: int = 100
    counts: Counter[str] = field(default_factory=Counter)
    examples: list[dict[str, str]] = field(default_factory=list)

    def add(self, stage: str, path: str, error: BaseException | str) -> None:
        self.counts[stage] += 1
        message = str(error)
        LOGGER.warning("%s failed for %s: %s", stage, path, message)
        if len(self.examples) < self.max_examples:
            self.examples.append({"stage": stage, "path": path, "error": message})

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_count": sum(self.counts.values()),
            "counts_by_stage": _ordered_counter(self.counts),
            "examples": self.examples,
            "examples_truncated": sum(self.counts.values()) > len(self.examples),
        }


@dataclass(frozen=True, slots=True)
class Utf8Facts:
    decodable: bool
    contains_non_ascii: bool


@dataclass(slots=True)
class MultiFileDocument:
    doc_id: str
    rcept_no: str
    report_name: str
    file_path: str
    expected_n_files: int
    files: list[dict[str, Any]] = field(default_factory=list)


def _ordered_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _ordered_nested_counter(
    counters: dict[str, Counter[str]],
) -> dict[str, dict[str, int]]:
    return {
        key: _ordered_counter(counters[key])
        for key in sorted(counters)
    }


def _read_prefix(entry: SourceFile, limit: int) -> bytes:
    with entry.open() as stream:
        return stream.read(limit)


def _scan_utf8(entry: SourceFile, chunk_size: int) -> Utf8Facts:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    decodable = True
    contains_non_ascii = False
    with entry.open() as stream:
        while chunk := stream.read(chunk_size):
            contains_non_ascii = contains_non_ascii or not chunk.isascii()
            if decodable:
                try:
                    decoder.decode(chunk, final=False)
                except UnicodeDecodeError:
                    decodable = False
        if decodable:
            try:
                decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                decodable = False
    return Utf8Facts(decodable=decodable, contains_non_ascii=contains_non_ascii)


def _charset_mismatch(
    declared: str | None,
    utf8_facts: Utf8Facts | None,
) -> bool | None:
    if declared is None or utf8_facts is None:
        return None
    try:
        codec_name = codecs.lookup(declared).name
    except LookupError:
        return True
    declares_utf8 = codec_name in {"utf-8", "utf-8-sig"}
    if declares_utf8:
        return not utf8_facts.decodable
    if not utf8_facts.contains_non_ascii:
        return False
    if utf8_facts.decodable:
        return True
    # A non-UTF-8 declaration and non-UTF-8 bytes are compatible, but the
    # byte-level check cannot prove the exact legacy encoding.
    return None


def _decode_prefix(prefix: bytes, declared_charset: str | None) -> str:
    candidates = ["utf-8-sig"]
    if declared_charset:
        candidates.append(declared_charset)
    candidates.extend(["cp949", "euc-kr"])
    attempted: set[str] = set()
    for encoding in candidates:
        if encoding.casefold() in attempted:
            continue
        attempted.add(encoding.casefold())
        try:
            decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
            return decoder.decode(prefix, final=False)
        except (UnicodeDecodeError, LookupError):
            continue
    # Do not let replacement characters influence downstream role selection.
    # Callers can safely fall back to the filename when no strict decoder works.
    return ""


def extract_document_name(prefix: bytes, declared_charset: str | None) -> str | None:
    """Extract a bounded XML ``DOCUMENT-NAME`` value when present."""

    text = _decode_prefix(prefix, declared_charset)
    match = _DOCUMENT_NAME_RE.search(text)
    if not match:
        return None
    value = _TAG_RE.sub(" ", match.group(1))
    value = html.unescape(value)
    value = " ".join(value.split())
    return value or None


def classify_periodic_file_role(
    document_name: str | None,
    filename: str,
    rcept_no: str,
    detected_format: str = "unknown",
) -> str:
    """Classify the main and common periodic attachments conservatively."""

    compact_name = re.sub(r"\s+", "", document_name or "")
    if "연결감사보고서" in compact_name:
        return "consolidated_audit_report"
    if "감사보고서" in compact_name:
        return "audit_report"
    if "연결검토보고서" in compact_name:
        return "consolidated_review_report"
    if "검토보고서" in compact_name:
        return "review_report"

    stem = PurePosixPath(filename).stem
    if detected_format == "pdf" and rcept_no and stem == rcept_no:
        return "primary_pdf"
    if detected_format == "html" and rcept_no and stem == f"{rcept_no}_viewer":
        return "viewer_html"
    if rcept_no and stem == rcept_no:
        return "main"
    if stem.endswith("_00760"):
        return "audit_report"
    if stem.endswith("_00761"):
        return "consolidated_audit_report"
    if compact_name:
        if any(
            label in compact_name
            for label in ("사업보고서", "반기보고서", "분기보고서")
        ):
            return "main"
        return "attachment"
    return "unknown"


def _is_raw_file(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path).parts
    return bool(parts and parts[0].casefold() == RAW_DIRECTORY_NAME.casefold())


def _infer_doc_group(
    relative_path: str, manifest_record: dict[str, Any] | None
) -> str:
    if manifest_record and manifest_record.get("doc_group"):
        return str(manifest_record["doc_group"])
    parts = PurePosixPath(relative_path).parts
    if len(parts) >= 2 and parts[0].casefold() == RAW_DIRECTORY_NAME.casefold():
        return parts[1]
    return ""


def _infer_rcept_no(
    relative_path: str, manifest_record: dict[str, Any] | None
) -> tuple[str, str]:
    if manifest_record and manifest_record.get("rcept_no") is not None:
        return str(manifest_record["rcept_no"]), "manifest"
    match = _RCEPT_NO_RE.search(relative_path)
    if match:
        return match.group(1), "path"
    return "", "unavailable"


def _bool_cell(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def _percentile(sorted_values: list[int], fraction: float) -> float | None:
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _file_size_summary(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {
            "count": 0,
            "total_bytes": 0,
            "min_bytes": None,
            "max_bytes": None,
            "mean_bytes": None,
            "median_bytes": None,
            "p95_bytes": None,
            "p99_bytes": None,
        }
    ordered = sorted(values)
    return {
        "count": len(values),
        "total_bytes": sum(values),
        "min_bytes": ordered[0],
        "max_bytes": ordered[-1],
        "mean_bytes": round(statistics.fmean(values), 2),
        "median_bytes": round(float(statistics.median(ordered)), 2),
        "p95_bytes": round(_percentile(ordered, 0.95) or 0.0, 2),
        "p99_bytes": round(_percentile(ordered, 0.99) or 0.0, 2),
    }


def _manifest_key(record: dict[str, Any]) -> str:
    try:
        file_path = canonical_relative_path(record.get("file_path"))
    except ValueError:
        file_path = ""
    if file_path:
        return file_path
    doc_id = str(record.get("doc_id") or "")
    return doc_id


def _prepare_multi_file_documents(
    manifest: ManifestInventory | None,
) -> dict[str, MultiFileDocument]:
    result: dict[str, MultiFileDocument] = {}
    if manifest is None:
        return result
    for record in manifest.records:
        n_files = parse_n_files(record.get("n_files"))
        if str(record.get("doc_group") or "") != "periodic" or not n_files or n_files <= 1:
            continue
        key = _manifest_key(record)
        if not key:
            continue
        try:
            file_path = canonical_relative_path(record.get("file_path"))
        except ValueError:
            file_path = ""
        result[key] = MultiFileDocument(
            doc_id=str(record.get("doc_id") or ""),
            rcept_no=str(record.get("rcept_no") or ""),
            report_name=str(record.get("report_nm") or ""),
            file_path=file_path,
            expected_n_files=n_files,
        )
    return result


def _summarize_multi_file_documents(
    documents: dict[str, MultiFileDocument],
) -> dict[str, Any]:
    role_counts: Counter[str] = Counter()
    document_name_counts: Counter[str] = Counter()
    expected_distribution: Counter[str] = Counter()
    mismatches: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []

    for key in sorted(documents):
        document = documents[key]
        expected_distribution[str(document.expected_n_files)] += 1
        for file_info in document.files:
            role_counts[str(file_info["role"])] += 1
            if file_info.get("document_name"):
                document_name_counts[str(file_info["document_name"])] += 1
        row = {
            "doc_id": document.doc_id,
            "rcept_no": document.rcept_no,
            "report_name": document.report_name,
            "file_path": document.file_path,
            "expected_n_files": document.expected_n_files,
            "observed_n_files": len(document.files),
            "count_matches_manifest": len(document.files) == document.expected_n_files,
            "files": document.files,
        }
        document_rows.append(row)
        if not row["count_matches_manifest"]:
            mismatches.append(
                {
                    "doc_id": document.doc_id,
                    "file_path": document.file_path,
                    "expected_n_files": document.expected_n_files,
                    "observed_n_files": len(document.files),
                }
            )

    return {
        "document_count": len(documents),
        "expected_n_files_distribution": _ordered_counter(expected_distribution),
        "observed_file_count": sum(len(document.files) for document in documents.values()),
        "role_counts": _ordered_counter(role_counts),
        "document_name_counts": _ordered_counter(document_name_counts),
        "document_name_extracted_count": sum(document_name_counts.values()),
        "file_count_mismatch_count": len(mismatches),
        "file_count_mismatch_examples": mismatches[:50],
        "documents": document_rows,
    }


def _validate_output_location(source: CorpusSource, output_dir: Path) -> None:
    if source.kind == "directory" and source.corpus_root_path is not None:
        try:
            output_dir.resolve().relative_to(source.corpus_root_path.resolve())
        except ValueError:
            pass
        else:
            raise ValueError(
                f"Output directory must be outside the corpus root to avoid "
                f"inventorying generated files: {output_dir}"
            )

    source_location = source.location.resolve()
    output_targets = (output_dir.resolve(),) + tuple(
        (output_dir / name).resolve()
        for name in (
            "file_inventory.csv",
            "inventory_summary.json",
            "inventory_report.md",
        )
    )
    if source_location in output_targets:
        raise ValueError(
            f"Output location collides with the input dataset: {source_location}"
        )


def analyze_corpus(
    data_root: str | Path,
    output_dir: str | Path,
    *,
    prefix_bytes: int = DEFAULT_PREFIX_BYTES,
    decode_chunk_bytes: int = DEFAULT_DECODE_CHUNK_BYTES,
    encoding_mode: EncodingMode = "all",
    encoding_sample_size: int = 200,
) -> dict[str, Any]:
    """Profile a directory or ZIP corpus and write all Phase 1 deliverables."""

    if prefix_bytes <= 0:
        raise ValueError("prefix_bytes must be greater than zero")
    if decode_chunk_bytes <= 0:
        raise ValueError("decode_chunk_bytes must be greater than zero")
    if encoding_mode not in {"all", "sample", "none"}:
        raise ValueError(f"Unsupported encoding_mode: {encoding_mode}")
    if encoding_sample_size < 0:
        raise ValueError("encoding_sample_size must be non-negative")

    report_dir = Path(output_dir).expanduser().resolve()
    issues = IssueCollector()

    with open_corpus_source(data_root) as source:
        _validate_output_location(source, report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        LOGGER.info(
            "Scanning %s corpus at %s (logical root: %s)",
            source.kind,
            source.location,
            source.logical_root or ".",
        )
        manifest_entry = source.find_manifest()
        manifest: ManifestInventory | None = None
        if manifest_entry is None:
            issues.add("manifest_discovery", str(data_root), "manifest.jsonl not found")
        else:
            try:
                manifest = load_manifest(manifest_entry)
            except Exception as exc:
                issues.add("manifest_read", manifest_entry.relative_path, exc)

        multi_documents = _prepare_multi_file_documents(manifest)

        extension_counts: Counter[str] = Counter()
        format_counts: Counter[str] = Counter()
        mismatch_pairs: Counter[str] = Counter()
        payload_format_counts: Counter[str] = Counter()
        unlinked_filename_counts: Counter[str] = Counter()
        unlinked_group_counts: Counter[str] = Counter()
        group_format_counts: dict[str, Counter[str]] = defaultdict(Counter)
        declared_charset_counts: Counter[str] = Counter()
        utf8_counts: Counter[str] = Counter()
        encoding_by_format: dict[str, Counter[str]] = defaultdict(Counter)
        role_counts: Counter[str] = Counter()
        raw_sizes: list[int] = []
        auxiliary_paths: list[str] = []
        encoding_mismatch_examples: list[dict[str, Any]] = []
        utf8_failure_examples: list[str] = []
        canonical_path_collisions: list[dict[str, str]] = []
        seen_paths: dict[str, str] = {}
        manifest_actual_counts: Counter[str] = Counter()

        dataset_file_count = 0
        raw_file_count = 0
        linked_raw_file_count = 0
        mismatch_count = 0
        xml_extension_html_count = 0
        encoding_scanned_count = 0
        text_like_file_count = 0
        undeclared_charset_count = 0
        encoding_mismatch_count = 0
        canonical_path_collision_count = 0
        group_listing_file_count = 0
        unmatched_raw_file_count = 0
        manifest_link_unavailable_file_count = 0

        run_token = f"{os.getpid()}-{uuid.uuid4().hex}"
        final_csv = report_dir / "file_inventory.csv"
        summary_path = report_dir / "inventory_summary.json"
        report_path = report_dir / "inventory_report.md"
        temp_csv = report_dir / f".file_inventory.{run_token}.tmp"
        summary_temp = report_dir / f".inventory_summary.{run_token}.tmp"
        report_temp = report_dir / f".inventory_report.{run_token}.tmp"
        created_staging_paths: set[Path] = set()
        try:
            with temp_csv.open("x", encoding="utf-8-sig", newline="") as csv_file:
                created_staging_paths.add(temp_csv)
                writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
                writer.writeheader()

                for entry in source.iter_files():
                    dataset_file_count += 1
                    is_raw = _is_raw_file(entry.relative_path)
                    scope = "raw" if is_raw else "auxiliary"
                    if is_raw:
                        raw_file_count += 1
                        raw_sizes.append(entry.size)
                    else:
                        auxiliary_paths.append(entry.relative_path)
                    if dataset_file_count % 250 == 0:
                        LOGGER.info(
                            "Scanned %d files (%d raw); continuing",
                            dataset_file_count,
                            raw_file_count,
                        )

                    previous = seen_paths.get(entry.relative_path)
                    if previous is not None:
                        canonical_path_collision_count += 1
                        issues.add(
                            "canonical_path_collision",
                            entry.source_path,
                            f"normalizes to existing path from {previous}",
                        )
                        if len(canonical_path_collisions) < 50:
                            canonical_path_collisions.append(
                                {
                                    "normalized_path": entry.relative_path,
                                    "first_source_path": previous,
                                    "second_source_path": entry.source_path,
                                }
                            )
                    else:
                        seen_paths[entry.relative_path] = entry.source_path

                    extension = PurePosixPath(entry.relative_path).suffix.lower()
                    expected_format = EXPECTED_FORMAT_BY_EXTENSION.get(extension)
                    manifest_record = manifest.match_file(entry.relative_path) if manifest else None
                    doc_group = _infer_doc_group(entry.relative_path, manifest_record)
                    rcept_no, rcept_source = _infer_rcept_no(
                        entry.relative_path, manifest_record
                    )
                    doc_id = str(manifest_record.get("doc_id") or "") if manifest_record else ""
                    filename = PurePosixPath(entry.relative_path).name
                    if manifest_record:
                        file_scope = "document_payload"
                    elif is_raw and filename.startswith("list_") and extension == ".json":
                        file_scope = "group_listing"
                    elif is_raw and manifest is None:
                        file_scope = "manifest_link_unavailable"
                    elif is_raw:
                        file_scope = "unmatched_raw"
                    else:
                        file_scope = "corpus_support"
                    if file_scope == "group_listing":
                        group_listing_file_count += 1
                    elif file_scope == "unmatched_raw":
                        unmatched_raw_file_count += 1
                    elif file_scope == "manifest_link_unavailable":
                        manifest_link_unavailable_file_count += 1

                    prefix = b""
                    detected_format = "unknown"
                    row_error = ""
                    try:
                        prefix = _read_prefix(entry, prefix_bytes)
                        detected_format = detect_format_from_bytes(
                            prefix, extension=extension
                        )
                    except Exception as exc:
                        row_error = str(exc)
                        issues.add("format_detection", entry.relative_path, exc)

                    mismatch = bool(
                        expected_format is not None and detected_format != expected_format
                    )
                    if is_raw:
                        extension_counts[extension or "[no extension]"] += 1
                        format_counts[detected_format] += 1
                        group_format_counts[doc_group or "unknown"][detected_format] += 1
                        if mismatch:
                            mismatch_count += 1
                            mismatch_pairs[
                                f"{extension or '[no extension]'} -> {detected_format}"
                            ] += 1
                        if extension == ".xml" and detected_format == "html":
                            xml_extension_html_count += 1
                        if manifest_record:
                            linked_raw_file_count += 1
                            manifest_actual_counts[_manifest_key(manifest_record)] += 1
                            payload_format_counts[detected_format] += 1
                        elif manifest is not None:
                            unlinked_filename_counts[filename] += 1
                            unlinked_group_counts[doc_group or "unknown"] += 1

                    declared_charset = (
                        detect_declared_charset(prefix)
                        if prefix and detected_format in TEXT_FORMATS
                        else None
                    )
                    if is_raw and declared_charset:
                        declared_charset_counts[declared_charset] += 1
                    if is_raw and detected_format in TEXT_FORMATS:
                        text_like_file_count += 1
                        if declared_charset is None:
                            undeclared_charset_count += 1

                    should_scan_encoding = False
                    if is_raw and detected_format in TEXT_FORMATS:
                        if encoding_mode == "all":
                            should_scan_encoding = True
                        elif (
                            encoding_mode == "sample"
                            and encoding_scanned_count < encoding_sample_size
                        ):
                            should_scan_encoding = True

                    utf8_facts: Utf8Facts | None = None
                    if should_scan_encoding:
                        try:
                            utf8_facts = _scan_utf8(entry, decode_chunk_bytes)
                            encoding_scanned_count += 1
                            label = "decodable" if utf8_facts.decodable else "not_decodable"
                            utf8_counts[label] += 1
                            encoding_by_format[detected_format][label] += 1
                            if (
                                not utf8_facts.decodable
                                and len(utf8_failure_examples) < 50
                            ):
                                utf8_failure_examples.append(entry.relative_path)
                        except Exception as exc:
                            issues.add("encoding_scan", entry.relative_path, exc)

                    charset_mismatch = _charset_mismatch(declared_charset, utf8_facts)
                    if (
                        is_raw
                        and charset_mismatch is True
                    ):
                        encoding_mismatch_count += 1
                        if len(encoding_mismatch_examples) < 100:
                            encoding_mismatch_examples.append(
                                {
                                    "relative_path": entry.relative_path,
                                    "declared_charset": declared_charset,
                                    "utf8_decodable": (
                                        utf8_facts.decodable if utf8_facts else None
                                    ),
                                    "contains_non_ascii": (
                                        utf8_facts.contains_non_ascii if utf8_facts else None
                                    ),
                                }
                            )

                    document_name: str | None = None
                    periodic_role = ""
                    if manifest_record and _manifest_key(manifest_record) in multi_documents:
                        document_name = extract_document_name(prefix, declared_charset)
                        periodic_role = classify_periodic_file_role(
                            document_name,
                            PurePosixPath(entry.relative_path).name,
                            rcept_no,
                            detected_format,
                        )
                        role_counts[periodic_role] += 1
                        multi_documents[_manifest_key(manifest_record)].files.append(
                            {
                                "relative_path": entry.relative_path,
                                "filename": PurePosixPath(entry.relative_path).name,
                                "detected_format": detected_format,
                                "document_name": document_name,
                                "role": periodic_role,
                            }
                        )

                    writer.writerow(
                        {
                            "relative_path": entry.relative_path,
                            "source_path": entry.source_path,
                            "scope": scope,
                            "file_scope": file_scope,
                            "extension": extension,
                            "detected_format": detected_format,
                            "expected_format_from_extension": expected_format or "",
                            "extension_format_mismatch": _bool_cell(
                                mismatch if expected_format is not None else None
                            ),
                            "file_size": entry.size,
                            "doc_group": doc_group,
                            "manifest_doc_id": doc_id,
                            "rcept_no": rcept_no,
                            "rcept_no_inferable": _bool_cell(bool(rcept_no)),
                            "rcept_no_source": rcept_source,
                            "declared_charset": declared_charset or "",
                            "utf8_decodable": _bool_cell(
                                utf8_facts.decodable if utf8_facts else None
                            ),
                            "contains_non_ascii": _bool_cell(
                                utf8_facts.contains_non_ascii if utf8_facts else None
                            ),
                            "charset_declaration_mismatch": _bool_cell(charset_mismatch),
                            "document_name": document_name or "",
                            "periodic_file_role": periodic_role,
                            "error": row_error,
                        }
                    )
        except BaseException:
            for staging_path in created_staging_paths:
                try:
                    staging_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

        for source_issue in source.discovery_issues:
            issues.add(source_issue.stage, source_issue.path, source_issue.error)

        manifest_count_mismatches: list[dict[str, Any]] = []
        manifest_count_mismatch_count = 0
        if manifest:
            for record in manifest.records:
                expected = parse_n_files(record.get("n_files"))
                if expected is None:
                    continue
                key = _manifest_key(record)
                observed = manifest_actual_counts.get(key, 0)
                if expected != observed:
                    manifest_count_mismatch_count += 1
                    if len(manifest_count_mismatches) < 100:
                        try:
                            mismatch_file_path = canonical_relative_path(
                                record.get("file_path")
                            )
                        except ValueError:
                            mismatch_file_path = ""
                        manifest_count_mismatches.append(
                            {
                                "doc_id": str(record.get("doc_id") or ""),
                                "file_path": mismatch_file_path,
                                "expected_n_files": expected,
                                "observed_n_files": observed,
                            }
                        )

        multi_summary = _summarize_multi_file_documents(multi_documents)
        # Keep this independent cross-check visible even if future role logic changes.
        multi_summary["role_counts_cross_check"] = _ordered_counter(role_counts)

        summary: dict[str, Any] = {
            "schema_version": "1.0",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "source": {
                "kind": source.kind,
                "location": str(source.location),
                "logical_root": source.logical_root,
                "prefix_bytes": prefix_bytes,
                "decode_chunk_bytes": decode_chunk_bytes,
                "discovery_issue_count": len(source.discovery_issues),
                "skipped_entry_count": sum(
                    issue.stage
                    in {
                        "directory_walk",
                        "file_stat",
                        "symlink_skipped",
                        "unsafe_member_path",
                    }
                    for issue in source.discovery_issues
                ),
            },
            "manifest": manifest.summary if manifest else {
                "document_count": 0,
                "available": False,
            },
            "physical_files": {
                "dataset_file_count": dataset_file_count,
                "raw_file_count": raw_file_count,
                "auxiliary_file_count": len(auxiliary_paths),
                "auxiliary_file_paths": sorted(auxiliary_paths),
                "group_listing_file_count": group_listing_file_count,
                "unmatched_raw_file_count": unmatched_raw_file_count,
                "manifest_link_unavailable_file_count": (
                    manifest_link_unavailable_file_count
                ),
                "manifest_link_status": "available" if manifest else "unavailable",
                "manifest_linked_raw_file_count": (
                    linked_raw_file_count if manifest else None
                ),
                "manifest_unlinked_raw_file_count": (
                    raw_file_count - linked_raw_file_count if manifest else None
                ),
                "manifest_linked_detected_format_counts": _ordered_counter(
                    payload_format_counts
                ),
                "unlinked_raw_filename_counts": _ordered_counter(
                    unlinked_filename_counts
                ),
                "unlinked_raw_doc_group_counts": _ordered_counter(
                    unlinked_group_counts
                ),
                "extension_counts": _ordered_counter(extension_counts),
                "detected_format_counts": _ordered_counter(format_counts),
                "extension_format_mismatch_count": mismatch_count,
                "extension_format_mismatch_pairs": _ordered_counter(mismatch_pairs),
                "doc_group_detected_format_counts": _ordered_nested_counter(
                    group_format_counts
                ),
                "file_size": _file_size_summary(raw_sizes),
                "xml_extension_html_count": xml_extension_html_count,
            },
            "join_quality": {
                "manifest_available": manifest is not None,
                "manifest_file_count_mismatch_count": (
                    manifest_count_mismatch_count if manifest else None
                ),
                "manifest_file_count_mismatch_examples": manifest_count_mismatches,
                "canonical_path_collision_count": canonical_path_collision_count,
                "canonical_path_collision_examples": canonical_path_collisions,
            },
            "encoding": {
                "mode": encoding_mode,
                "sample_size_limit": (
                    encoding_sample_size if encoding_mode == "sample" else None
                ),
                "scanned_file_count": encoding_scanned_count,
                "text_like_file_count": text_like_file_count,
                "declared_charset_counts": _ordered_counter(declared_charset_counts),
                "no_charset_declaration_count": undeclared_charset_count,
                "utf8_decodability_counts": _ordered_counter(utf8_counts),
                "utf8_decodability_by_format": _ordered_nested_counter(
                    encoding_by_format
                ),
                "declaration_mismatch_count": encoding_mismatch_count,
                "declaration_mismatch_examples": encoding_mismatch_examples,
                "utf8_decode_failure_examples": utf8_failure_examples,
                "measurement_note": (
                    "UTF-8 validity is checked incrementally. A non-UTF-8 declaration "
                    "with valid non-ASCII UTF-8 bytes is reported as a mismatch; exact "
                    "legacy encodings are not guessed."
                ),
            },
            "periodic_multi_file": multi_summary,
            "issues": issues.as_dict(),
        }

        try:
            with summary_temp.open("x", encoding="utf-8") as summary_file:
                created_staging_paths.add(summary_temp)
                summary_file.write(
                    json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)
                    + "\n"
                )
            with report_temp.open("x", encoding="utf-8") as report_file:
                created_staging_paths.add(report_temp)
                report_file.write(render_inventory_report(summary))
            # Publish only after the complete generation has been staged.
            os.replace(temp_csv, final_csv)
            os.replace(summary_temp, summary_path)
            os.replace(report_temp, report_path)
        except BaseException:
            for staging_path in created_staging_paths:
                try:
                    staging_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    return summary


__all__ = [
    "CSV_FIELDS",
    "EncodingMode",
    "analyze_corpus",
    "classify_periodic_file_role",
    "extract_document_name",
]
