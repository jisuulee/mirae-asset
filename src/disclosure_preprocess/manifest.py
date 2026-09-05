"""Streaming manifest loading and document-level statistics."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .config import ID_FIELDS
from .source import SourceFile
from .utils import normalize_nfc, normalize_relative_posix_path

LOGGER = logging.getLogger(__name__)


def canonical_relative_path(value: object) -> str:
    """Normalize a manifest or filesystem path for reliable joins."""

    return normalize_relative_posix_path(str(value or ""))


def preserve_identifier_strings(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy whose identifier fields are strings.

    Existing strings are never parsed as numbers, so leading zeroes survive.
    """

    result = dict(record)
    for field in ID_FIELDS:
        value = result.get(field)
        if value is not None and not isinstance(value, str):
            result[field] = str(value)
    return result


def parse_n_files(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def correction_label(value: object) -> str:
    if isinstance(value, bool):
        return "correction" if value else "non_correction"
    if isinstance(value, (int, float)):
        return "correction" if value else "non_correction"
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "y"}:
            return "correction"
        if normalized in {"false", "0", "no", "n", ""}:
            return "non_correction"
    return "unknown"


@dataclass(slots=True)
class ManifestInventory:
    records: list[dict[str, Any]]
    by_file_path: dict[str, dict[str, Any]]
    summary: dict[str, Any]
    malformed_lines: int
    duplicate_file_paths: list[str]

    def match_file(self, relative_path: str) -> dict[str, Any] | None:
        """Find the manifest row owning a physical file by longest ancestor."""

        canonical = canonical_relative_path(relative_path)
        candidate = PurePosixPath(canonical)
        keys = [str(candidate), str(candidate.parent)]
        for key in keys:
            record = self.by_file_path.get(key)
            if record is not None:
                return record
        return None


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def load_manifest(entry: SourceFile) -> ManifestInventory:
    """Read JSONL one line at a time and compute manifest statistics."""

    records: list[dict[str, Any]] = []
    by_file_path: dict[str, dict[str, Any]] = {}
    duplicate_paths: list[str] = []
    ambiguous_paths: set[str] = set()
    seen_doc_ids: set[str] = set()
    duplicate_doc_ids: list[str] = []
    seen_rcept_nos: set[str] = set()
    duplicate_rcept_nos: list[str] = []
    invalid_file_paths: list[dict[str, str]] = []
    invalid_file_path_count = 0
    malformed_lines = 0

    doc_groups: Counter[str] = Counter()
    doc_subtypes: Counter[str] = Counter()
    correction_counts: Counter[str] = Counter()
    file_formats: Counter[str] = Counter()
    n_files_distribution: Counter[str] = Counter()
    company_counts: Counter[str] = Counter()
    corp_listed_name_pairs: Counter[str] = Counter()
    rcept_date_mismatches: list[dict[str, str]] = []
    rcept_date_mismatch_count = 0

    with entry.open() as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            try:
                line = raw_line.decode("utf-8-sig")
                parsed = json.loads(line)
                if not isinstance(parsed, dict):
                    raise ValueError("manifest line is not a JSON object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                malformed_lines += 1
                LOGGER.warning(
                    "Skipping malformed manifest line %d in %s: %s",
                    line_number,
                    entry.relative_path,
                    exc,
                )
                continue

            record = preserve_identifier_strings(parsed)
            records.append(record)

            doc_group = str(record.get("doc_group") or "unknown")
            subtype_value = record.get("doc_subtype")
            if subtype_value is None:
                doc_subtype = "[null]"
            elif str(subtype_value) == "":
                doc_subtype = "[empty]"
            else:
                doc_subtype = str(subtype_value)
            file_format = str(record.get("file_format") or "unknown").lower()
            company = normalize_nfc(
                str(record.get("corp_name") or record.get("listed_name") or "unknown")
            )
            n_files = parse_n_files(record.get("n_files"))

            doc_groups[doc_group] += 1
            doc_subtypes[doc_subtype] += 1
            correction_counts[correction_label(record.get("is_correction"))] += 1
            file_formats[file_format] += 1
            n_files_distribution[str(n_files) if n_files is not None else "invalid"] += 1
            company_counts[company] += 1

            corp_name = str(record.get("corp_name") or "")
            listed_name = str(record.get("listed_name") or "")
            if (
                corp_name
                and listed_name
                and normalize_nfc(corp_name) != normalize_nfc(listed_name)
            ):
                corp_listed_name_pairs[f"{corp_name} -> {listed_name}"] += 1

            rcept_no = str(record.get("rcept_no") or "")
            rcept_dt = str(record.get("rcept_dt") or "")
            if (
                len(rcept_no) >= 8
                and rcept_no[:8].isdigit()
                and len(rcept_dt) == 8
                and rcept_dt.isdigit()
                and rcept_no[:8] != rcept_dt
            ):
                rcept_date_mismatch_count += 1
                if len(rcept_date_mismatches) < 50:
                    rcept_date_mismatches.append(
                        {
                            "doc_id": str(record.get("doc_id") or ""),
                            "rcept_no": rcept_no,
                            "rcept_dt": rcept_dt,
                        }
                    )

            doc_id = str(record.get("doc_id") or "")
            if doc_id:
                if doc_id in seen_doc_ids:
                    duplicate_doc_ids.append(doc_id)
                seen_doc_ids.add(doc_id)
            if rcept_no:
                if rcept_no in seen_rcept_nos:
                    duplicate_rcept_nos.append(rcept_no)
                seen_rcept_nos.add(rcept_no)

            try:
                file_path = canonical_relative_path(record.get("file_path"))
            except ValueError as exc:
                invalid_file_path_count += 1
                LOGGER.warning(
                    "Ignoring unsafe manifest file_path at line %d: %s",
                    line_number,
                    exc,
                )
                if len(invalid_file_paths) < 50:
                    invalid_file_paths.append(
                        {
                            "doc_id": doc_id,
                            "file_path": str(record.get("file_path") or ""),
                            "error": str(exc),
                        }
                    )
                file_path = ""
            if file_path:
                if file_path in by_file_path or file_path in ambiguous_paths:
                    duplicate_paths.append(file_path)
                    by_file_path.pop(file_path, None)
                    ambiguous_paths.add(file_path)
                else:
                    by_file_path[file_path] = record

    summary: dict[str, Any] = {
        "available": True,
        "document_count": len(records),
        "doc_group_counts": _counter_dict(doc_groups),
        "doc_subtype_counts": _counter_dict(doc_subtypes),
        "correction_counts": _counter_dict(correction_counts),
        "file_format_counts": _counter_dict(file_formats),
        "n_files_distribution": _counter_dict(n_files_distribution),
        "company_document_counts": _counter_dict(company_counts),
        "company_count": len(company_counts),
        "malformed_line_count": malformed_lines,
        "duplicate_file_path_count": len(duplicate_paths),
        "duplicate_doc_id_count": len(duplicate_doc_ids),
        "duplicate_rcept_no_count": len(duplicate_rcept_nos),
        "invalid_file_path_count": invalid_file_path_count,
        "invalid_file_path_examples": invalid_file_paths,
        "corp_name_listed_name_mismatch_count": sum(corp_listed_name_pairs.values()),
        "corp_name_listed_name_mismatch_pairs": _counter_dict(corp_listed_name_pairs),
        "rcept_date_prefix_mismatch_count": rcept_date_mismatch_count,
        "rcept_date_prefix_mismatch_examples": rcept_date_mismatches,
        "declared_physical_file_count": sum(
            parse_n_files(record.get("n_files")) or 0 for record in records
        ),
    }
    return ManifestInventory(
        records=records,
        by_file_path=by_file_path,
        summary=summary,
        malformed_lines=malformed_lines,
        duplicate_file_paths=duplicate_paths,
    )
