"""Streaming Phase 3 metadata normalization, validation, and handoff pipeline."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from disclosure_preprocess.manifest import (
    canonical_relative_path,
    load_manifest,
)
from disclosure_preprocess.metadata_normalization import (
    CANONICAL_METADATA_FIELDS,
    COMPARISON_POLICY_VERSION,
    CONTENT_SCHEMA_VERSION,
    EXPECTED_DOC_GROUPS,
    MetadataValidationStatus,
    MismatchReason,
    PHASE3_SCHEMA_VERSION,
    SAFE_FILTER_FIELDS,
    SAMPLE_POLICY_VERSION,
    build_canonical_metadata,
    classify_mismatch_reason,
    extract_source_observations,
    metadata_warning,
    normalize_comparison_text,
    stable_mismatch_id,
    validation_status,
)
from disclosure_preprocess.metadata_reporting import (
    render_handoff_readme,
    render_metadata_report,
)
from disclosure_preprocess.source import CorpusSource, SourceFile, open_corpus_source
from disclosure_preprocess.utils import normalize_nfc


LOGGER = logging.getLogger(__name__)

METADATA_FILENAME = "metadata_normalized.jsonl"
MISMATCH_FILENAME = "metadata_mismatches.jsonl"
METADATA_SUMMARY_FILENAME = "metadata_summary.json"
METADATA_REPORT_FILENAME = "metadata_report.md"

MISMATCH_FIELDS = (
    "corp_name",
    "listed_name",
    "rcept_dt",
    "report_nm",
    "doc_group",
    "doc_subtype",
    "corp_code",
    "stock_code",
    "other",
)

FORMAT_ERROR_CODES = frozenset(
    {
        "CANONICAL_FORMAT_INVALID",
        "IDENTIFIER_TYPE_INVALID",
        "RCEPT_DATE_INVALID",
        "DOC_GROUP_UNRECOGNIZED",
        "DOC_ID_COMPONENT_MISMATCH",
        "IS_CORRECTION_TYPE_INVALID",
        "CANONICAL_TYPE_INVALID",
        "BASE_MONTH_INVALID",
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    return text.encode("utf-8")


def _write_json_line(stream: BinaryIO, value: Mapping[str, Any]) -> None:
    stream.write(_json_bytes(value))


def _sha256_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _source_sha256(entry: SourceFile) -> str:
    digest = hashlib.sha256()
    with entry.open() as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_info(
    path: Path,
    *,
    relative_path: str,
    record_count: int | None,
    schema_ref: str | None,
    bundled: bool,
    sha256: str | None = None,
    media_type: str = "application/x-ndjson",
) -> dict[str, Any]:
    return {
        "relative_path": relative_path,
        "bundled": bundled,
        "media_type": media_type,
        "encoding": "utf-8",
        "newline": "LF",
        "schema_ref": schema_ref,
        "record_count": record_count,
        "size_bytes": path.stat().st_size,
        "sha256": sha256 or _sha256_file(path),
    }


def _validate_output_locations(
    source: CorpusSource,
    parsed_path: Path,
    warning_path: Path,
    contract_path: Path,
    output_dir: Path,
    report_dir: Path,
    handoff_dir: Path,
) -> None:
    target_files = (
        output_dir / METADATA_FILENAME,
        report_dir / MISMATCH_FILENAME,
        report_dir / METADATA_SUMMARY_FILENAME,
        report_dir / METADATA_REPORT_FILENAME,
        handoff_dir / METADATA_FILENAME,
        handoff_dir / "sample_disclosures.jsonl",
        handoff_dir / "sample_selection.jsonl",
        handoff_dir / "handoff_manifest.json",
        handoff_dir / "data_contract.md",
        handoff_dir / "README.md",
    )
    resolved_targets = [item.resolve() for item in target_files]
    if len(set(resolved_targets)) != len(resolved_targets):
        raise ValueError("Phase 3 output artifact paths must be distinct")

    protected_inputs = {
        parsed_path.resolve(): "parsed_disclosures.jsonl",
        warning_path.resolve(): "parse_warnings.jsonl",
        contract_path.resolve(): "Data Contract source",
        source.location.resolve(): "corpus source",
    }
    for target in resolved_targets:
        if target in protected_inputs:
            raise ValueError(
                f"Phase 3 output must not overwrite {protected_inputs[target]}: {target}"
            )

    resolved_directories = [
        output_dir.resolve(), report_dir.resolve(), handoff_dir.resolve()
    ]
    if len(set(resolved_directories)) != len(resolved_directories):
        raise ValueError("Output, report, and handoff directories must be distinct")
    for directory in resolved_directories:
        if directory in protected_inputs:
            raise ValueError(
                f"Phase 3 output directory collides with {protected_inputs[directory]}"
            )

    source_path = source.location.resolve()
    for directory in (output_dir, report_dir, handoff_dir):
        if directory.resolve() == source_path:
            raise ValueError(f"Output directory collides with source: {directory}")
    if source.kind == "directory" and source.corpus_root_path is not None:
        corpus_root = source.corpus_root_path.resolve()
        for directory in (output_dir, report_dir, handoff_dir):
            try:
                directory.resolve().relative_to(corpus_root)
            except ValueError:
                continue
            raise ValueError(
                f"Phase 3 output must not be inside read-only corpus root: {directory}"
            )


def _load_phase2_mismatches(
    warning_path: Path,
    manifest_by_doc_id: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_observations: list[dict[str, Any]] = []
    source_alias_targets: dict[str, set[str]] = defaultdict(set)
    digest = hashlib.sha256()
    line_count = 0
    malformed_count = 0

    with warning_path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            line_count += 1
            try:
                warning = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                malformed_count += 1
                continue
            if not isinstance(warning, Mapping) or warning.get("code") != "SOURCE_METADATA_MISMATCH":
                continue
            details = warning.get("details")
            detail_map = details if isinstance(details, Mapping) else {}
            provenance = warning.get("provenance")
            provenance_map = provenance if isinstance(provenance, Mapping) else {}
            canonical_field = str(detail_map.get("manifest_field") or "other")
            source_field = str(detail_map.get("source_field") or "unknown")
            canonical_raw = detail_map.get("manifest_value")
            source_raw = detail_map.get("source_value")
            observation = {
                "doc_id": str(warning.get("doc_id") or ""),
                "rcept_no": str(warning.get("rcept_no") or ""),
                "phase2_warning_scope": warning.get("scope"),
                "canonical_field": canonical_field,
                "source_field": source_field,
                "canonical_raw": canonical_raw,
                "source_raw": source_raw,
                "phase2_compared_source_value": source_raw,
                "source_file": provenance_map.get("source_file"),
                "file_role": provenance_map.get("file_role"),
                "source_provenance": dict(provenance_map),
                "phase2_warning_details": dict(detail_map),
                "phase2_warning_line": line_number,
            }
            raw_observations.append(observation)
            if canonical_field == "corp_name":
                source_key = normalize_comparison_text(source_raw)
                canonical_key = normalize_comparison_text(canonical_raw)
                if source_key is not None and canonical_key is not None:
                    source_alias_targets[source_key].add(canonical_key)

    duplicate_indexes: Counter[tuple[object, ...]] = Counter()
    mismatches: list[dict[str, Any]] = []
    for observation in raw_observations:
        source_key = normalize_comparison_text(observation.get("source_raw"))
        aliases = source_alias_targets.get(source_key or "", set())
        alias_ambiguous = (
            observation["canonical_field"] == "corp_name" and len(aliases) > 1
        )
        manifest_record = manifest_by_doc_id.get(str(observation["doc_id"]), {})
        reason, severity, normalized_equal = classify_mismatch_reason(
            field=str(observation["canonical_field"]),
            canonical_value=observation.get("canonical_raw"),
            source_value=observation.get("source_raw"),
            listed_name_value=manifest_record.get("listed_name"),
            company_identity_ambiguous=alias_ambiguous,
        )
        occurrence_key = (
            observation["doc_id"],
            observation["rcept_no"],
            observation.get("source_file"),
            observation.get("file_role"),
            observation["canonical_field"],
            observation["source_field"],
        )
        duplicate_indexes[occurrence_key] += 1
        occurrence_index = duplicate_indexes[occurrence_key]
        mismatch = {
            "mismatch_id": stable_mismatch_id(
                doc_id=str(observation["doc_id"]),
                rcept_no=str(observation["rcept_no"]),
                source_file=(
                    str(observation["source_file"])
                    if observation.get("source_file") is not None
                    else None
                ),
                file_role=(
                    str(observation["file_role"])
                    if observation.get("file_role") is not None
                    else None
                ),
                canonical_field=str(observation["canonical_field"]),
                source_field=str(observation["source_field"]),
                occurrence_index=occurrence_index,
            ),
            **observation,
            "canonical_normalized": normalize_comparison_text(
                observation.get("canonical_raw")
            ),
            "source_normalized": normalize_comparison_text(
                observation.get("source_raw")
            ),
            "normalized_equal": normalized_equal,
            "reason_code": str(reason),
            "severity": severity,
            "classification_basis": (
                "legal-designator/display form matches corp_name or listed_name"
                if reason == MismatchReason.DISPLAY_NAME_DIFFERENCE
                else "conservative field-specific comparison; no alias inference"
            ),
            "source_alias_ambiguous": alias_ambiguous,
            "phase2_warning_present": True,
            "evidence_verified": False,
            "origin": "phase2_baseline_recheck",
            "comparison_policy_version": COMPARISON_POLICY_VERSION,
        }
        mismatches.append(mismatch)

    ambiguous_aliases = {
        key: sorted(values)
        for key, values in source_alias_targets.items()
        if len(values) > 1
    }
    return mismatches, {
        "path": str(warning_path.resolve()),
        "sha256": digest.hexdigest(),
        "size_bytes": warning_path.stat().st_size,
        "line_count": line_count,
        "malformed_line_count": malformed_count,
        "source_alias_target_count": len(source_alias_targets),
        "ambiguous_source_aliases": ambiguous_aliases,
    }


def _components(parsed: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values: list[Mapping[str, Any]] = []
    main = parsed.get("main_document")
    if isinstance(main, Mapping):
        values.append(main)
    for field in ("attachments", "alternative_representations"):
        items = parsed.get(field)
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
            values.extend(item for item in items if isinstance(item, Mapping))
    return values


def _mismatch_fingerprint(
    *,
    doc_id: object,
    rcept_no: object,
    source_file: object,
    file_role: object,
    details: object,
) -> tuple[str, str, str, str, str]:
    detail_map = details if isinstance(details, Mapping) else {}
    return (
        str(doc_id or ""),
        str(rcept_no or ""),
        str(source_file or ""),
        str(file_role or ""),
        json.dumps(
            detail_map,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _embedded_mismatch_fingerprints(
    parsed: Mapping[str, Any],
) -> Counter[tuple[str, str, str, str, str]]:
    fingerprints: Counter[tuple[str, str, str, str, str]] = Counter()
    warnings = parsed.get("warnings")
    if not isinstance(warnings, Sequence) or isinstance(
        warnings, (str, bytes, bytearray)
    ):
        return fingerprints
    for warning in warnings:
        if not isinstance(warning, Mapping) or warning.get("code") != "SOURCE_METADATA_MISMATCH":
            continue
        provenance_value = warning.get("provenance")
        provenance = (
            provenance_value if isinstance(provenance_value, Mapping) else {}
        )
        fingerprints[
            _mismatch_fingerprint(
                doc_id=parsed.get("doc_id"),
                rcept_no=parsed.get("rcept_no"),
                source_file=provenance.get("source_file"),
                file_role=provenance.get("file_role"),
                details=warning.get("details"),
            )
        ] += 1
    return fingerprints


def _reconcile_mismatch_evidence(
    parsed: Mapping[str, Any],
    manifest_record: Mapping[str, Any],
    mismatch: dict[str, Any],
) -> dict[str, bool]:
    """Cross-check one Phase 2 warning against content-embedded source evidence."""

    canonical_field = str(mismatch.get("canonical_field") or "")
    source_field = str(mismatch.get("source_field") or "")
    expected_path = mismatch.get("source_file")
    expected_role = mismatch.get("file_role")
    expected_source_value = mismatch.get("phase2_compared_source_value")

    matching_components: list[Mapping[str, Any]] = []
    for component in _components(parsed):
        source_file = component.get("source_file")
        source_record = source_file if isinstance(source_file, Mapping) else {}
        if (
            source_record.get("relative_path") == expected_path
            and source_record.get("role") == expected_role
        ):
            matching_components.append(component)

    component = matching_components[0] if len(matching_components) == 1 else {}
    source_metadata_value = component.get("source_metadata")
    source_metadata = (
        source_metadata_value
        if isinstance(source_metadata_value, Mapping)
        else {}
    )
    observed_comparison_value = (
        str(source_metadata.get(source_field) or "")
        if source_field in source_metadata
        else None
    )

    embedded_warning_present = False
    parsed_warnings = parsed.get("warnings")
    if isinstance(parsed_warnings, Sequence) and not isinstance(
        parsed_warnings, (str, bytes, bytearray)
    ):
        for warning in parsed_warnings:
            if not isinstance(warning, Mapping):
                continue
            provenance_value = warning.get("provenance")
            provenance = (
                provenance_value if isinstance(provenance_value, Mapping) else {}
            )
            details_value = warning.get("details")
            details = details_value if isinstance(details_value, Mapping) else {}
            if (
                warning.get("code") == "SOURCE_METADATA_MISMATCH"
                and dict(details) == dict(mismatch.get("phase2_warning_details") or {})
                and provenance.get("source_file") == expected_path
                and provenance.get("file_role") == expected_role
            ):
                embedded_warning_present = True
                break

    checks = {
        "warning_scope_is_logical_document": mismatch.get(
            "phase2_warning_scope"
        )
        == "logical_document",
        "doc_id_matches_parsed": mismatch.get("doc_id") == parsed.get("doc_id"),
        "rcept_no_matches_parsed": mismatch.get("rcept_no")
        == parsed.get("rcept_no"),
        "rcept_no_matches_manifest": mismatch.get("rcept_no")
        == manifest_record.get("rcept_no"),
        "canonical_field_exists": canonical_field in manifest_record,
        "canonical_value_matches_manifest": mismatch.get("canonical_raw")
        == manifest_record.get(canonical_field),
        "source_component_unique": len(matching_components) == 1,
        "source_field_observed": source_field in source_metadata,
        "source_value_matches_phase2_warning": observed_comparison_value
        == expected_source_value,
        "phase2_warning_embedded_in_content": embedded_warning_present,
    }

    if len(matching_components) == 1 and source_field in source_metadata:
        raw_key = f"{source_field}_raw"
        provenance_key = f"{source_field}_provenance"
        source_raw = source_metadata.get(
            raw_key, source_metadata.get(source_field)
        )
        reason, severity, normalized_equal = classify_mismatch_reason(
            field=canonical_field,
            canonical_value=manifest_record.get(canonical_field),
            source_value=source_raw,
            listed_name_value=manifest_record.get("listed_name"),
            company_identity_ambiguous=bool(
                mismatch.get("source_alias_ambiguous")
            ),
        )
        mismatch.update(
            {
                "source_raw": source_raw,
                "source_normalized": normalize_comparison_text(source_raw),
                "source_value_provenance": source_metadata.get(provenance_key),
                "canonical_normalized": normalize_comparison_text(
                    manifest_record.get(canonical_field)
                ),
                "normalized_equal": normalized_equal,
                "reason_code": str(reason),
                "severity": severity,
                "classification_basis": (
                    "legal-designator/display form matches corp_name or listed_name"
                    if reason == MismatchReason.DISPLAY_NAME_DIFFERENCE
                    else "conservative field-specific comparison; no alias inference"
                ),
            }
        )
    mismatch["evidence_checks"] = checks
    mismatch["evidence_verified"] = all(checks.values())
    return checks


def _warning_codes(parsed: Mapping[str, Any]) -> set[str]:
    codes: set[str] = set()
    warnings = parsed.get("warnings")
    if isinstance(warnings, Sequence) and not isinstance(warnings, (str, bytes, bytearray)):
        for item in warnings:
            if isinstance(item, Mapping) and item.get("code"):
                codes.add(str(item["code"]))
    for component in _components(parsed):
        component_warnings = component.get("warnings")
        if isinstance(component_warnings, Sequence) and not isinstance(
            component_warnings, (str, bytes, bytearray)
        ):
            for item in component_warnings:
                if isinstance(item, Mapping) and item.get("code"):
                    codes.add(str(item["code"]))
    return codes


def _main_content(parsed: Mapping[str, Any]) -> Mapping[str, Any]:
    main = parsed.get("main_document")
    if not isinstance(main, Mapping):
        return {}
    content = main.get("content")
    return content if isinstance(content, Mapping) else {}


def _main_format(parsed: Mapping[str, Any]) -> str | None:
    main = parsed.get("main_document")
    if not isinstance(main, Mapping):
        return None
    source_file = main.get("source_file")
    if not isinstance(source_file, Mapping):
        return None
    value = source_file.get("detected_format")
    return str(value) if value is not None else None


def _source_paths_valid(parsed: Mapping[str, Any], manifest_path: object) -> bool:
    try:
        base = canonical_relative_path(manifest_path)
    except ValueError:
        return False
    source_files = parsed.get("source_files")
    if not isinstance(source_files, Sequence) or isinstance(
        source_files, (str, bytes, bytearray)
    ):
        return False
    for item in source_files:
        if not isinstance(item, Mapping):
            return False
        try:
            source_path = canonical_relative_path(item.get("relative_path"))
        except ValueError:
            return False
        if source_path != base and not source_path.startswith(base.rstrip("/") + "/"):
            return False
    return True


def _join_warnings(
    parsed: Mapping[str, Any], manifest_record: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    checks: dict[str, bool] = {
        "parsed_doc_id_matches_manifest": parsed.get("doc_id")
        == manifest_record.get("doc_id"),
        "parsed_rcept_no_matches_manifest": parsed.get("rcept_no")
        == manifest_record.get("rcept_no"),
        "metadata_authority_is_manifest": False,
        "embedded_manifest_matches": False,
        "top_level_canonical_fields_match": True,
        "main_content_present": bool(_main_content(parsed)),
        "source_file_count_matches": False,
        "source_paths_under_manifest": _source_paths_valid(
            parsed, manifest_record.get("file_path")
        ),
    }
    metadata = parsed.get("metadata")
    if isinstance(metadata, Mapping):
        checks["metadata_authority_is_manifest"] = metadata.get("authority") == "manifest"
        embedded = metadata.get("manifest")
        checks["embedded_manifest_matches"] = isinstance(embedded, Mapping) and dict(
            embedded
        ) == dict(manifest_record)
    for field in CANONICAL_METADATA_FIELDS:
        if parsed.get(field) != manifest_record.get(field):
            checks["top_level_canonical_fields_match"] = False
            break
    source_files = parsed.get("source_files")
    if isinstance(source_files, Sequence) and not isinstance(
        source_files, (str, bytes, bytearray)
    ):
        checks["source_file_count_matches"] = len(source_files) == manifest_record.get(
            "n_files"
        )

    warnings: list[dict[str, Any]] = []
    for check_name, passed in checks.items():
        if passed:
            continue
        warnings.append(
            metadata_warning(
                "METADATA_CONTENT_LINK_INVALID",
                "Phase 2 content and manifest metadata failed a one-to-one linkage check",
                severity="error",
                details={"failed_check": check_name},
            )
        )
    parse_status = parsed.get("parse_status")
    if parse_status not in {"success", "partial", "failed"}:
        warnings.append(
            metadata_warning(
                "PARSE_STATUS_INVALID",
                "Phase 2 parse_status is outside the contract vocabulary",
                severity="error",
                field="parse_status",
                details={"observed": parse_status},
            )
        )
    return warnings, checks


def _new_field_stats() -> dict[str, Any]:
    return {
        "non_null_count": 0,
        "null_count": 0,
        "empty_count": 0,
        "whitespace_only_count": 0,
        "type_counts": Counter(),
        "unique_values": set(),
        "format_violation_count": 0,
        "nfc_normalization_needed_count": 0,
    }


def _update_field_stats(stats: dict[str, Any], value: object) -> None:
    stats["type_counts"][type(value).__name__] += 1
    if value is None:
        stats["null_count"] += 1
        return
    stats["non_null_count"] += 1
    if isinstance(value, str):
        if value == "":
            stats["empty_count"] += 1
        elif not value.strip():
            stats["whitespace_only_count"] += 1
        if value != normalize_nfc(value):
            stats["nfc_normalization_needed_count"] += 1
    try:
        stats["unique_values"].add((type(value).__name__, value))
    except TypeError:
        stats["unique_values"].add((type(value).__name__, json.dumps(value, sort_keys=True)))


def _finalize_field_stats(stats: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "non_null_count": stats["non_null_count"],
        "null_count": stats["null_count"],
        "empty_count": stats["empty_count"],
        "whitespace_only_count": stats["whitespace_only_count"],
        "type_counts": dict(stats["type_counts"]),
        "unique_value_count": len(stats["unique_values"]),
        "format_violation_count": stats["format_violation_count"],
        "nfc_normalization_needed_count": stats[
            "nfc_normalization_needed_count"
        ],
    }


def _descriptor_buckets(descriptor: Mapping[str, Any]) -> set[tuple[str, str]]:
    buckets: set[tuple[str, str]] = {
        ("doc_group", str(descriptor["doc_group"])),
        ("parse_status", str(descriptor["parse_status"])),
        (
            "group_status",
            f"{descriptor['doc_group']}:{descriptor['parse_status']}",
        ),
        ("correction", "true" if descriptor["is_correction"] else "false"),
        ("section", "present" if descriptor["has_sections"] else "absent"),
        ("table", "present" if descriptor["has_tables"] else "absent"),
        ("metadata_mismatch", "present" if descriptor["has_mismatch"] else "absent"),
        ("year", str(descriptor["year"])),
    }
    if descriptor["doc_group"] == "periodic":
        buckets.add(("periodic_subtype", str(descriptor["doc_subtype"])))
        buckets.add(
            (
                "periodic_cardinality",
                "multi" if int(descriptor["n_files"]) > 1 else "single",
            )
        )
    if descriptor["has_mismatch"]:
        buckets.add(("mismatch_group", str(descriptor["doc_group"])))
    return buckets


def _stable_selection_key(doc_id: str) -> str:
    digest = hashlib.sha256(
        f"{SAMPLE_POLICY_VERSION}\0{doc_id}".encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _percentile(values: Sequence[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(int((len(ordered) - 1) * percentile), len(ordered) - 1)
    return ordered[index]


def _select_sample(
    descriptors: Sequence[dict[str, Any]], sample_count: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if sample_count < 1:
        raise ValueError("sample_count must be positive")
    if len(descriptors) >= 50 and not 50 <= sample_count <= 100:
        raise ValueError(
            "A full-corpus handoff sample must contain between 50 and 100 documents"
        )
    if sample_count > len(descriptors):
        sample_count = len(descriptors)

    by_id = {str(item["doc_id"]): item for item in descriptors}
    selected: dict[str, dict[str, Any]] = {}
    mandatory_ids: set[str] = set()
    reasons: dict[str, set[str]] = defaultdict(set)

    def add(item: Mapping[str, Any], reason: str, *, mandatory: bool = True) -> None:
        doc_id = str(item["doc_id"])
        selected[doc_id] = by_id[doc_id]
        reasons[doc_id].add(reason)
        if mandatory:
            mandatory_ids.add(doc_id)

    for item in descriptors:
        if item["file_format"] == "pdf+html":
            add(item, "PDF_HTML_REPRESENTATION")
        if item["title_missing"]:
            add(item, "TITLE_MISSING")
        if item["rcept_date_prefix_mismatch"]:
            add(item, "RCEPT_DATE_PREFIX_MISMATCH")
        if "TABLE_SPAN_NORMALIZATION_FAILED" in item["warning_codes"]:
            add(item, "TABLE_SPAN_ANOMALY")

    warning_codes = sorted(
        {code for item in descriptors for code in item["warning_codes"]}
    )
    for code in warning_codes:
        candidates = [item for item in descriptors if code in item["warning_codes"]]
        if candidates:
            add(
                min(candidates, key=lambda item: (item["line_size_bytes"], item["doc_id"])),
                "WARNING_CODE_COVERAGE",
            )

    mismatch_roles = sorted(
        {role for item in descriptors for role in item["mismatch_roles"] if role}
    )
    for role in mismatch_roles:
        candidates = [item for item in descriptors if role in item["mismatch_roles"]]
        if candidates:
            add(
                min(candidates, key=lambda item: (item["line_size_bytes"], item["doc_id"])),
                "SOURCE_METADATA_MISMATCH",
            )

    corp_listed_pairs = sorted(
        {
            tuple(item["corp_listed_pair"])
            for item in descriptors
            if item["corp_listed_pair"] is not None
        }
    )
    for pair in corp_listed_pairs:
        candidates = [item for item in descriptors if item["corp_listed_pair"] == list(pair)]
        if candidates:
            add(
                min(candidates, key=lambda item: (item["line_size_bytes"], item["doc_id"])),
                "CORP_LISTED_NAME_DIFFERENCE",
            )

    p95_size = _percentile(
        [int(item["line_size_bytes"]) for item in descriptors], 0.95
    )
    large_candidates = [
        item for item in descriptors if int(item["line_size_bytes"]) >= p95_size
    ]
    if large_candidates:
        add(
            min(large_candidates, key=lambda item: (item["line_size_bytes"], item["doc_id"])),
            "LARGE_RECORD",
        )

    if len(selected) > sample_count:
        raise RuntimeError(
            f"Mandatory representative sample cases exceed sample_count: {len(selected)} > {sample_count}"
        )

    targets: dict[tuple[str, str], int] = {}
    required_company_count = 1
    if sample_count >= 50:
        base_targets = {
            ("doc_group", "periodic"): 20,
            ("doc_group", "exchange"): 18,
            ("doc_group", "holding"): 18,
            ("doc_group", "major"): 18,
            ("parse_status", "success"): 40,
            ("parse_status", "partial"): 25,
            ("correction", "true"): 20,
            ("correction", "false"): 40,
            ("section", "present"): 30,
            ("section", "absent"): 12,
            ("table", "present"): 60,
            ("metadata_mismatch", "present"): 18,
            ("periodic_subtype", "annual"): 6,
            ("periodic_subtype", "half"): 6,
            ("periodic_subtype", "quarter"): 6,
            ("periodic_cardinality", "single"): 12,
            ("periodic_cardinality", "multi"): 8,
            ("mismatch_group", "periodic"): 6,
            ("mismatch_group", "holding"): 6,
            ("mismatch_group", "major"): 6,
        }
        targets.update(
            {
                bucket: max(1, base * sample_count // 80)
                for bucket, base in base_targets.items()
            }
        )
        for group in sorted(EXPECTED_DOC_GROUPS):
            for status in ("success", "partial"):
                targets[("group_status", f"{group}:{status}")] = max(
                    1, 3 * sample_count // 80
                )
        for year in ("2023", "2024", "2025", "2026"):
            targets[("year", year)] = max(1, 10 * sample_count // 80)
        required_company_count = max(1, 30 * sample_count // 80)
    else:
        for group in sorted({str(item["doc_group"]) for item in descriptors}):
            targets[("doc_group", group)] = 1
        for status in sorted({str(item["parse_status"]) for item in descriptors}):
            targets[("parse_status", status)] = 1

    def bucket_counts() -> Counter[tuple[str, str]]:
        counts: Counter[tuple[str, str]] = Counter()
        for item in selected.values():
            counts.update(_descriptor_buckets(item))
        return counts

    while len(selected) < sample_count:
        counts = bucket_counts()
        selected_companies = Counter(str(item["corp_code"]) for item in selected.values())
        distinct_companies = len(selected_companies)
        candidates: list[tuple[float, int, str, str, dict[str, Any]]] = []
        for item in descriptors:
            doc_id = str(item["doc_id"])
            if doc_id in selected:
                continue
            company = str(item["corp_code"])
            if selected_companies[company] >= 4 and distinct_companies < required_company_count:
                continue
            buckets = _descriptor_buckets(item)
            score = 0.0
            for bucket, target in targets.items():
                deficit = max(target - counts[bucket], 0)
                if deficit and bucket in buckets:
                    score += 10.0 + deficit / max(target, 1)
            if distinct_companies < required_company_count and company not in selected_companies:
                score += 12.0
            candidates.append(
                (
                    -score,
                    int(item["line_size_bytes"]),
                    _stable_selection_key(doc_id),
                    doc_id,
                    item,
                )
            )
        if not candidates:
            for item in descriptors:
                doc_id = str(item["doc_id"])
                if doc_id not in selected:
                    candidates.append(
                        (
                            0.0,
                            int(item["line_size_bytes"]),
                            _stable_selection_key(doc_id),
                            doc_id,
                            item,
                        )
                    )
        _, _, _, _, chosen = min(candidates)
        add(chosen, "QUOTA_COVERAGE", mandatory=False)

    counts = bucket_counts()
    deficits = {
        f"{dimension}:{bucket}": target - counts[(dimension, bucket)]
        for (dimension, bucket), target in targets.items()
        if counts[(dimension, bucket)] < target
    }
    distinct_companies = len({str(item["corp_code"]) for item in selected.values()})
    if distinct_companies < required_company_count:
        deficits["distinct_companies"] = required_company_count - distinct_companies
    if deficits:
        raise RuntimeError(f"Representative sample quotas were not met: {deficits}")

    selected_rows = sorted(selected.values(), key=lambda item: int(item["record_ordinal"]))
    counts_by_group = Counter(str(item["doc_group"]) for item in selected_rows)
    counts_by_status = Counter(str(item["parse_status"]) for item in selected_rows)
    selection_records: list[dict[str, Any]] = []
    priority = (
        "PDF_HTML_REPRESENTATION",
        "TITLE_MISSING",
        "RCEPT_DATE_PREFIX_MISMATCH",
        "TABLE_SPAN_ANOMALY",
        "WARNING_CODE_COVERAGE",
        "SOURCE_METADATA_MISMATCH",
        "CORP_LISTED_NAME_DIFFERENCE",
        "LARGE_RECORD",
        "QUOTA_COVERAGE",
    )
    for sample_ordinal, item in enumerate(selected_rows, start=1):
        doc_id = str(item["doc_id"])
        item_reasons = set(reasons[doc_id])
        item_reasons.update({"DOC_GROUP_COVERAGE", "PARSE_STATUS_COVERAGE", "YEAR_COVERAGE"})
        item_reasons.add("CORRECTION_CASE" if item["is_correction"] else "NON_CORRECTION_CASE")
        item_reasons.add("HAS_SECTION" if item["has_sections"] else "NO_SECTION")
        if item["has_tables"]:
            item_reasons.add("HAS_TABLE")
        if item["has_mismatch"]:
            item_reasons.add("SOURCE_METADATA_MISMATCH")
        if item["doc_group"] == "periodic":
            item_reasons.add(
                "PERIODIC_MULTI_FILE" if int(item["n_files"]) > 1 else "PERIODIC_SINGLE_FILE"
            )
        primary = next((code for code in priority if code in item_reasons), sorted(item_reasons)[0])
        selection_records.append(
            {
                "selection_schema_version": "1.0.0",
                "selection_policy_version": SAMPLE_POLICY_VERSION,
                "doc_id": doc_id,
                "rcept_no": item["rcept_no"],
                "sample_ordinal": sample_ordinal,
                "source_record_ordinal": item["record_ordinal"],
                "mandatory": doc_id in mandatory_ids,
                "primary_reason_code": primary,
                "reason_codes": sorted(item_reasons),
                "coverage": {
                    "doc_group": item["doc_group"],
                    "doc_subtype": item["doc_subtype"],
                    "parse_status": item["parse_status"],
                    "is_correction": item["is_correction"],
                    "file_cardinality": "multi" if int(item["n_files"]) > 1 else "single",
                    "main_format": item["main_format"],
                    "has_sections": item["has_sections"],
                    "has_tables": item["has_tables"],
                    "metadata_mismatch_fields": item["mismatch_fields"],
                    "metadata_mismatch_reasons": item["mismatch_reasons"],
                    "warning_codes": item["warning_codes"],
                    "year": item["year"],
                    "corp_code": item["corp_code"],
                },
                "stable_selection_key": _stable_selection_key(doc_id),
                "source_byte_offset": item["byte_offset"],
                "source_record_size_bytes": item["line_size_bytes"],
                "source_line_sha256": item["line_sha256"],
            }
        )
    return selection_records, {
        "sample_count": len(selection_records),
        "selection_policy_version": SAMPLE_POLICY_VERSION,
        "quota_validation_passed": True,
        "quota_targets": {
            f"{dimension}:{bucket}": target
            for (dimension, bucket), target in sorted(targets.items())
        },
        "quota_observed_counts": {
            f"{dimension}:{bucket}": counts[(dimension, bucket)]
            for dimension, bucket in sorted(targets)
        },
        "counts_by_doc_group": dict(sorted(counts_by_group.items())),
        "counts_by_parse_status": dict(sorted(counts_by_status.items())),
        "distinct_company_count": distinct_companies,
        "mandatory_document_count": len(mandatory_ids),
        "p95_source_record_size_bytes": p95_size,
        "source_lines_copied_verbatim": True,
    }


def _copy_sample_lines(
    parsed_path: Path,
    selection_records: Sequence[Mapping[str, Any]],
    sample_path: Path,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    total_bytes = 0
    with parsed_path.open("rb") as source, sample_path.open("wb") as output:
        for record in selection_records:
            source.seek(int(record["source_byte_offset"]))
            raw_line = source.read(int(record["source_record_size_bytes"]))
            observed_hash = hashlib.sha256(raw_line).hexdigest()
            if observed_hash != record["source_line_sha256"]:
                raise RuntimeError(
                    f"Sample source line changed for {record['doc_id']}: {observed_hash}"
                )
            if not raw_line.endswith(b"\n"):
                raise RuntimeError(f"Sample source line has no LF terminator: {record['doc_id']}")
            output.write(raw_line)
            digest.update(raw_line)
            total_bytes += len(raw_line)
    return digest.hexdigest(), total_bytes


def _publish_artifacts(
    stage_root: Path, artifacts: Sequence[tuple[Path, Path, bool]]
) -> None:
    """Publish staged files with rollback; manifest-marked files are moved last."""

    ordered = sorted(artifacts, key=lambda item: item[2])
    backup_dir = stage_root / "previous-generation"
    backup_dir.mkdir()
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for index, (_, final, _) in enumerate(ordered):
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists() and final.is_dir():
                raise ValueError(f"Output artifact path is a directory: {final}")
            if final.exists() or final.is_symlink():
                backup = backup_dir / f"{index:03d}-{final.name}"
                os.replace(final, backup)
                backups[final] = backup
        for staged, final, _ in ordered:
            os.replace(staged, final)
            published.append(final)
    except BaseException:
        for final in reversed(published):
            final.unlink(missing_ok=True)
        for final, backup in backups.items():
            if backup.exists() or backup.is_symlink():
                os.replace(backup, final)
        raise
    shutil.rmtree(stage_root)


def _safe_filter_contract(examples: Mapping[str, object]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in SAFE_FILTER_FIELDS:
        nullable = field == "doc_subtype"
        conditions = "exact match"
        if field == "doc_subtype":
            conditions = "null-aware exact match; null은 미분류이며 추론 금지"
        elif field == "rcept_date":
            conditions = "strict rcept_dt 형식 변환값; 날짜 범위 filter 가능"
        elif field in {"corp_code", "stock_code", "rcept_no"}:
            conditions = "문자열 exact match; 숫자 casting 금지"
        elif field in {"corp_name", "listed_name"}:
            conditions = "서로 합치지 말고 field별 exact/NFC-aware match"
        elif field == "is_correction":
            conditions = "boolean filter만 가능; 원공시 lineage 의미 없음"
        result[field] = {
            "type": "boolean" if field == "is_correction" else "string",
            "nullable": nullable,
            "canonical_source": "manifest.jsonl" if field != "rcept_date" else "strict format of manifest.rcept_dt",
            "filter_allowed": True,
            "conditions": conditions,
            "example": examples.get(field),
        }
    return result


def normalize_metadata_corpus(
    data_root: str | Path,
    *,
    parsed_path: str | Path,
    output_dir: str | Path,
    report_dir: str | Path,
    handoff_dir: str | Path,
    parse_warnings_path: str | Path | None = None,
    data_contract_path: str | Path | None = None,
    expected_document_count: int | None = 4204,
    expected_mismatch_count: int | None = 745,
    sample_count: int = 80,
    progress_every: int = 100,
) -> dict[str, Any]:
    """Validate Phase 2 metadata and publish an additive Phase 3 sidecar."""

    started = _utc_now()
    parsed = Path(parsed_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    reports = Path(report_dir).expanduser().resolve()
    handoff = Path(handoff_dir).expanduser().resolve()
    warnings_path = (
        Path(parse_warnings_path).expanduser().resolve()
        if parse_warnings_path is not None
        else parsed.parent / "parse_warnings.jsonl"
    )
    contract = (
        Path(data_contract_path).expanduser().resolve()
        if data_contract_path is not None
        else Path(__file__).resolve().parents[2] / "docs" / "data_contract.md"
    )
    if not parsed.is_file():
        raise FileNotFoundError(f"Phase 2 parsed dataset not found: {parsed}")
    parsed_initial_stat = parsed.stat()
    if not warnings_path.is_file():
        raise FileNotFoundError(f"Phase 2 warning sidecar not found: {warnings_path}")
    if not contract.is_file():
        raise FileNotFoundError(f"Data Contract not found: {contract}")
    with open_corpus_source(data_root) as source:
        _validate_output_locations(
            source,
            parsed,
            warnings_path,
            contract,
            output,
            reports,
            handoff,
        )
        for directory in (output, reports, handoff):
            directory.mkdir(parents=True, exist_ok=True)
        manifest_entry = source.find_manifest()
        if manifest_entry is None:
            raise ValueError("Exactly one manifest.jsonl is required")
        manifest = load_manifest(manifest_entry)
        if expected_document_count is not None and len(manifest.records) != expected_document_count:
            raise ValueError(
                f"Manifest document count differs from expected: {len(manifest.records)} != {expected_document_count}"
            )
        manifest_sha256 = _source_sha256(manifest_entry)
        manifest_by_doc_id = {
            str(record.get("doc_id") or ""): record for record in manifest.records
        }
        if len(manifest_by_doc_id) != len(manifest.records):
            raise ValueError("Manifest doc_id values are missing or duplicated")

        mismatches, warning_source = _load_phase2_mismatches(
            warnings_path, manifest_by_doc_id
        )
        mismatch_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for mismatch in mismatches:
            mismatch_by_doc[str(mismatch["doc_id"])].append(mismatch)

        stage_root = Path(tempfile.mkdtemp(prefix=".phase3-", dir=output))
        stage_output = stage_root / "processed"
        stage_reports = stage_root / "reports"
        stage_handoff = stage_root / "handoff"
        for directory in (stage_output, stage_reports, stage_handoff):
            directory.mkdir()
        staged_metadata = stage_output / METADATA_FILENAME
        staged_mismatches = stage_reports / MISMATCH_FILENAME
        staged_summary = stage_reports / METADATA_SUMMARY_FILENAME
        staged_report = stage_reports / METADATA_REPORT_FILENAME
        staged_handoff_metadata = stage_handoff / METADATA_FILENAME
        staged_sample = stage_handoff / "sample_disclosures.jsonl"
        staged_selection = stage_handoff / "sample_selection.jsonl"
        staged_contract = stage_handoff / "data_contract.md"
        staged_handoff_readme = stage_handoff / "README.md"
        staged_handoff_manifest = stage_handoff / "handoff_manifest.json"

        descriptors: list[dict[str, Any]] = []
        field_stats = {
            field: _new_field_stats()
            for field in (*CANONICAL_METADATA_FIELDS, "rcept_date")
        }
        seen_doc_ids: set[str] = set()
        seen_rcept_nos: set[str] = set()
        duplicate_doc_ids: set[str] = set()
        duplicate_rcept_nos: set[str] = set()
        parsed_doc_ids: set[str] = set()
        unexpected_content_ids: list[str] = []
        malformed_content_lines: list[dict[str, Any]] = []
        suspected_linkage: list[dict[str, Any]] = []
        status_counts: Counter[str] = Counter()
        validation_warning_counts: Counter[str] = Counter()
        parse_status_counts: Counter[str] = Counter()
        metadata_record_count = 0
        matched_count = 0
        content_missing_count = 0
        link_inconsistency_docs: set[str] = set()
        metadata_digest = hashlib.sha256()
        parsed_digest = hashlib.sha256()
        parsed_line_count = 0
        examples: dict[str, object] = {}
        comparison_coverage: Counter[str] = Counter()
        baseline_mismatch_fingerprints = Counter(
            _mismatch_fingerprint(
                doc_id=item.get("doc_id"),
                rcept_no=item.get("rcept_no"),
                source_file=item.get("source_file"),
                file_role=item.get("file_role"),
                details=item.get("phase2_warning_details"),
            )
            for item in mismatches
        )
        embedded_mismatch_fingerprints: Counter[
            tuple[str, str, str, str, str]
        ] = Counter()

        try:
            with parsed.open("rb") as input_stream, staged_metadata.open("wb") as metadata_stream:
                record_ordinal = 0
                while True:
                    byte_offset = input_stream.tell()
                    raw_line = input_stream.readline()
                    if not raw_line:
                        break
                    parsed_digest.update(raw_line)
                    if not raw_line.strip():
                        continue
                    parsed_line_count += 1
                    try:
                        parsed_record = json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        malformed_content_lines.append(
                            {"line_number": parsed_line_count, "error": str(exc)}
                        )
                        continue
                    if not isinstance(parsed_record, Mapping):
                        malformed_content_lines.append(
                            {
                                "line_number": parsed_line_count,
                                "error": "JSONL row is not an object",
                            }
                        )
                        continue
                    record_ordinal += 1
                    doc_id = str(parsed_record.get("doc_id") or "")
                    rcept_no = str(parsed_record.get("rcept_no") or "")
                    embedded_mismatch_fingerprints.update(
                        _embedded_mismatch_fingerprints(parsed_record)
                    )
                    if doc_id in parsed_doc_ids:
                        duplicate_doc_ids.add(doc_id)
                    parsed_doc_ids.add(doc_id)
                    if rcept_no in seen_rcept_nos:
                        duplicate_rcept_nos.add(rcept_no)
                    seen_rcept_nos.add(rcept_no)
                    if doc_id in seen_doc_ids:
                        continue
                    seen_doc_ids.add(doc_id)
                    manifest_record = manifest_by_doc_id.get(doc_id)
                    if manifest_record is None:
                        unexpected_content_ids.append(doc_id)
                        continue

                    matched_count += 1
                    canonical, canonical_warnings = build_canonical_metadata(
                        manifest_record
                    )
                    metadata_warnings = list(canonical_warnings)
                    join_warnings, join_checks = _join_warnings(
                        parsed_record, manifest_record
                    )
                    metadata_warnings.extend(join_warnings)
                    if join_warnings:
                        link_inconsistency_docs.add(doc_id)
                        suspected_linkage.append(
                            {
                                "doc_id": doc_id,
                                "code": "METADATA_CONTENT_LINK_INVALID",
                                "reason": ", ".join(
                                    key for key, value in join_checks.items() if not value
                                ),
                            }
                        )
                    if not join_checks["main_content_present"]:
                        content_missing_count += 1

                    document_mismatches = mismatch_by_doc.get(doc_id, [])
                    for mismatch in document_mismatches:
                        evidence_checks = _reconcile_mismatch_evidence(
                            parsed_record, manifest_record, mismatch
                        )
                        if not all(evidence_checks.values()):
                            warning = metadata_warning(
                                "MISMATCH_BASELINE_EVIDENCE_DRIFT",
                                "Phase 2 mismatch warning no longer matches embedded source evidence",
                                severity="error",
                                field=str(mismatch["canonical_field"]),
                                details={
                                    "mismatch_id": mismatch["mismatch_id"],
                                    "failed_checks": [
                                        key
                                        for key, value in evidence_checks.items()
                                        if not value
                                    ],
                                },
                            )
                            metadata_warnings.append(warning)
                            link_inconsistency_docs.add(doc_id)
                            suspected_linkage.append(
                                {
                                    "doc_id": doc_id,
                                    "code": warning["code"],
                                    "reason": ", ".join(
                                        key
                                        for key, value in evidence_checks.items()
                                        if not value
                                    ),
                                }
                            )
                        field = str(mismatch["canonical_field"])
                        if manifest_record.get(field) != mismatch.get("canonical_raw"):
                            warning = metadata_warning(
                                "MISMATCH_BASELINE_CANONICAL_DRIFT",
                                "Phase 2 mismatch canonical value differs from the current manifest",
                                severity="error",
                                field=field,
                                details={"mismatch_id": mismatch["mismatch_id"]},
                            )
                            metadata_warnings.append(warning)
                            link_inconsistency_docs.add(doc_id)
                            suspected_linkage.append(
                                {
                                    "doc_id": doc_id,
                                    "code": warning["code"],
                                    "reason": str(mismatch["mismatch_id"]),
                                }
                            )
                        metadata_warnings.append(
                            metadata_warning(
                                "SOURCE_METADATA_MISMATCH_REVALIDATED",
                                "Source observation differs from manifest authority; manifest value was retained",
                                severity=str(mismatch["severity"]),
                                field=field,
                                details={
                                    "mismatch_id": mismatch["mismatch_id"],
                                    "reason_code": mismatch["reason_code"],
                                },
                            )
                        )

                    status = validation_status(
                        canonical_warnings, len(document_mismatches)
                    )
                    status_counts[str(status)] += 1
                    for warning in metadata_warnings:
                        validation_warning_counts[str(warning["code"])] += 1
                    parse_status = str(parsed_record.get("parse_status") or "")
                    parse_status_counts[parse_status] += 1

                    source_metadata = extract_source_observations(parsed_record)
                    for observation in source_metadata["observations"]:
                        for value in observation["values"].values():
                            comparison_coverage[str(value["canonical_field"])] += 1

                    sidecar_record = {
                        "schema_version": PHASE3_SCHEMA_VERSION,
                        "content_schema_version": CONTENT_SCHEMA_VERSION,
                        "doc_id": doc_id,
                        "rcept_no": rcept_no,
                        "metadata": canonical,
                        "source_metadata": source_metadata,
                        "metadata_validation": {
                            "status": str(status),
                            "mismatch_count": len(document_mismatches),
                            "warnings": metadata_warnings,
                            "canonical_warning_count": len(canonical_warnings),
                            "checks": join_checks,
                        },
                        "parse_status": parse_status,
                        "content_reference": {
                            "dataset": "parsed_disclosures.jsonl",
                            "join_key": "doc_id",
                            "secondary_check": "rcept_no",
                        },
                    }
                    encoded = _json_bytes(sidecar_record)
                    metadata_stream.write(encoded)
                    metadata_digest.update(encoded)
                    metadata_record_count += 1

                    format_fields = {
                        str(warning.get("field"))
                        for warning in canonical_warnings
                        if warning.get("code") in FORMAT_ERROR_CODES
                        and warning.get("field") is not None
                    }
                    for field in (*CANONICAL_METADATA_FIELDS, "rcept_date"):
                        value = canonical.get(field)
                        _update_field_stats(field_stats[field], value)
                        if field in format_fields:
                            field_stats[field]["format_violation_count"] += 1
                        if field not in examples and value is not None:
                            examples[field] = value

                    content = _main_content(parsed_record)
                    warnings = sorted(_warning_codes(parsed_record))
                    mismatch_fields = sorted(
                        {str(item["canonical_field"]) for item in document_mismatches}
                    )
                    mismatch_reasons = sorted(
                        {str(item["reason_code"]) for item in document_mismatches}
                    )
                    mismatch_roles = sorted(
                        {
                            str(item["file_role"])
                            for item in document_mismatches
                            if item.get("file_role") is not None
                        }
                    )
                    corp_name = canonical.get("corp_name")
                    listed_name = canonical.get("listed_name")
                    corp_listed_pair = (
                        [corp_name, listed_name]
                        if isinstance(corp_name, str)
                        and isinstance(listed_name, str)
                        and normalize_nfc(corp_name) != normalize_nfc(listed_name)
                        else None
                    )
                    rcept_dt = canonical.get("rcept_dt")
                    prefix_mismatch = (
                        isinstance(rcept_no, str)
                        and isinstance(rcept_dt, str)
                        and len(rcept_no) >= 8
                        and rcept_no[:8] != rcept_dt
                    )
                    line_hash = hashlib.sha256(raw_line).hexdigest()
                    descriptors.append(
                        {
                            "record_ordinal": record_ordinal,
                            "byte_offset": byte_offset,
                            "line_size_bytes": len(raw_line),
                            "line_sha256": line_hash,
                            "doc_id": doc_id,
                            "rcept_no": rcept_no,
                            "doc_group": canonical.get("doc_group"),
                            "doc_subtype": canonical.get("doc_subtype"),
                            "parse_status": parse_status,
                            "is_correction": canonical.get("is_correction"),
                            "n_files": manifest_record.get("n_files") or 0,
                            "file_format": manifest_record.get("file_format"),
                            "main_format": _main_format(parsed_record),
                            "has_sections": bool(content.get("sections")),
                            "has_tables": bool(content.get("tables")),
                            "title_missing": not bool(content.get("title")),
                            "has_mismatch": bool(document_mismatches),
                            "mismatch_fields": mismatch_fields,
                            "mismatch_reasons": mismatch_reasons,
                            "mismatch_roles": mismatch_roles,
                            "warning_codes": warnings,
                            "corp_listed_pair": corp_listed_pair,
                            "rcept_date_prefix_mismatch": prefix_mismatch,
                            "year": (
                                str(canonical.get("rcept_date"))[:4]
                                if canonical.get("rcept_date")
                                else None
                            ),
                            "corp_code": canonical.get("corp_code"),
                            "corp_name": canonical.get("corp_name"),
                        }
                    )
                    if progress_every > 0 and metadata_record_count % progress_every == 0:
                        LOGGER.info(
                            "Validated %d/%d Phase 3 metadata records",
                            metadata_record_count,
                            len(manifest.records),
                        )
                    del parsed_record

            manifest_without_content = sorted(set(manifest_by_doc_id) - parsed_doc_ids)

            with staged_mismatches.open("wb") as stream:
                for mismatch in mismatches:
                    _write_json_line(stream, mismatch)

            selection_records, sample_summary = _select_sample(
                descriptors, sample_count
            )
            sample_sha256, sample_size = _copy_sample_lines(
                parsed, selection_records, staged_sample
            )
            with staged_selection.open("wb") as stream:
                for record in selection_records:
                    _write_json_line(stream, record)

            shutil.copyfile(staged_metadata, staged_handoff_metadata)
            shutil.copyfile(contract, staged_contract)
            if (
                staged_handoff_metadata.stat().st_size != staged_metadata.stat().st_size
                or _sha256_file(staged_handoff_metadata) != metadata_digest.hexdigest()
            ):
                raise RuntimeError("Handoff metadata copy is not byte-identical")
            if _sha256_file(staged_contract) != _sha256_file(contract):
                raise RuntimeError("Handoff Data Contract copy is not byte-identical")

            finalized_completeness = {
                field: _finalize_field_stats(stats)
                for field, stats in field_stats.items()
            }
            uniqueness: dict[str, Any] = {}
            for field, interpretation in (
                ("doc_id", "전체 corpus에서 unique여야 함"),
                ("rcept_no", "전체 corpus에서 unique여야 함"),
                ("corp_code", "회사별 반복이 정상"),
                ("stock_code", "회사별 반복이 정상"),
            ):
                item = finalized_completeness[field]
                uniqueness[field] = {
                    "non_null_count": item["non_null_count"],
                    "unique_value_count": item["unique_value_count"],
                    "duplicate_occurrence_count": max(
                        item["non_null_count"] - item["unique_value_count"], 0
                    ),
                    "interpretation": interpretation,
                }

            counts_by_field = Counter(str(item["canonical_field"]) for item in mismatches)
            normalized_field_counts = {
                field: counts_by_field.get(field, 0) for field in MISMATCH_FIELDS
            }
            for field, count in counts_by_field.items():
                if field not in normalized_field_counts:
                    normalized_field_counts["other"] += count
            counts_by_reason = Counter(str(item["reason_code"]) for item in mismatches)
            all_reasons = {
                str(reason): counts_by_reason.get(str(reason), 0)
                for reason in MismatchReason
            }
            counts_by_severity = Counter(str(item["severity"]) for item in mismatches)
            mismatch_doc_ids = {str(item["doc_id"]) for item in mismatches}
            canonical_companies = {
                str(item["canonical_normalized"])
                for item in mismatches
                if item["canonical_field"] == "corp_name"
            }
            source_variants = {
                str(item["source_normalized"])
                for item in mismatches
                if item["canonical_field"] == "corp_name"
            }
            pair_counts = Counter(
                (
                    str(item["canonical_raw"]),
                    str(item["source_raw"]),
                    str(item["reason_code"]),
                )
                for item in mismatches
            )
            mismatch_examples_by_id: dict[str, dict[str, Any]] = {}

            def add_mismatch_example(
                example: Mapping[str, Any], category: str
            ) -> None:
                mismatch_id = str(example["mismatch_id"])
                if mismatch_id not in mismatch_examples_by_id:
                    pair = (
                        str(example["canonical_raw"]),
                        str(example["source_raw"]),
                        str(example["reason_code"]),
                    )
                    mismatch_examples_by_id[mismatch_id] = {
                        **{
                            key: example.get(key)
                            for key in (
                                "mismatch_id",
                                "doc_id",
                                "rcept_no",
                                "canonical_field",
                                "canonical_raw",
                                "source_raw",
                                "reason_code",
                                "severity",
                                "source_file",
                                "file_role",
                            )
                        },
                        "pair_occurrence_count": pair_counts[pair],
                        "example_categories": [],
                    }
                mismatch_examples_by_id[mismatch_id]["example_categories"].append(
                    category
                )

            for reason in sorted(
                {str(item["reason_code"]) for item in mismatches}
            ):
                candidates = [
                    item for item in mismatches if item["reason_code"] == reason
                ]
                add_mismatch_example(
                    max(
                        candidates,
                        key=lambda item: (
                            pair_counts[
                                (
                                    str(item["canonical_raw"]),
                                    str(item["source_raw"]),
                                    str(item["reason_code"]),
                                )
                            ],
                            str(item["mismatch_id"]),
                        ),
                    ),
                    f"reason:{reason}",
                )
            for field in sorted(
                {str(item["canonical_field"]) for item in mismatches}
            ):
                candidates = [
                    item for item in mismatches if item["canonical_field"] == field
                ]
                add_mismatch_example(
                    max(
                        candidates,
                        key=lambda item: (
                            pair_counts[
                                (
                                    str(item["canonical_raw"]),
                                    str(item["source_raw"]),
                                    str(item["reason_code"]),
                                )
                            ],
                            str(item["mismatch_id"]),
                        ),
                    ),
                    f"field:{field}",
                )
            for pair, _ in pair_counts.most_common(20):
                example = next(
                    item
                    for item in mismatches
                    if (
                        str(item["canonical_raw"]),
                        str(item["source_raw"]),
                        str(item["reason_code"]),
                    )
                    == pair
                )
                add_mismatch_example(example, "top_frequency")
                if len(mismatch_examples_by_id) >= 20:
                    break
            mismatch_examples = list(mismatch_examples_by_id.values())

            corp_listed_count = sum(
                1
                for record in manifest.records
                if isinstance(record.get("corp_name"), str)
                and isinstance(record.get("listed_name"), str)
                and normalize_nfc(record["corp_name"])
                != normalize_nfc(record["listed_name"])
            )
            prefix_difference_count = sum(
                1
                for record in manifest.records
                if isinstance(record.get("rcept_no"), str)
                and isinstance(record.get("rcept_dt"), str)
                and len(record["rcept_no"]) >= 8
                and record["rcept_no"][:8] != record["rcept_dt"]
            )

            quality_gates = {
                "manifest_count_matches_expected": expected_document_count is None
                or len(manifest.records) == expected_document_count,
                "manifest_has_no_malformed_rows": manifest.malformed_lines == 0,
                "manifest_file_paths_unique": not manifest.duplicate_file_paths,
                "manifest_doc_ids_unique": manifest.summary.get(
                    "duplicate_doc_id_count", 0
                )
                == 0,
                "manifest_rcept_nos_unique": manifest.summary.get(
                    "duplicate_rcept_no_count", 0
                )
                == 0,
                "parsed_count_matches_manifest": parsed_line_count
                == len(manifest.records),
                "metadata_count_matches_manifest": metadata_record_count
                == len(manifest.records),
                "join_is_one_to_one": matched_count == len(manifest.records),
                "no_manifest_without_content": not manifest_without_content,
                "no_content_without_manifest": not unexpected_content_ids,
                "no_malformed_content_rows": not malformed_content_lines,
                "doc_id_unique": not duplicate_doc_ids
                and len(seen_doc_ids) == len(manifest.records),
                "rcept_no_unique": not duplicate_rcept_nos
                and len(seen_rcept_nos) == len(manifest.records),
                "no_link_inconsistency": not link_inconsistency_docs,
                "no_invalid_metadata": status_counts[
                    str(MetadataValidationStatus.INVALID)
                ]
                == 0,
                "phase2_warning_sidecar_has_no_malformed_rows": warning_source[
                    "malformed_line_count"
                ]
                == 0,
                "phase2_mismatch_baseline_reconciled": expected_mismatch_count
                is None
                or len(mismatches) == expected_mismatch_count,
                "all_mismatch_documents_joined": mismatch_doc_ids.issubset(
                    parsed_doc_ids & set(manifest_by_doc_id)
                ),
                "all_mismatch_evidence_verified": all(
                    bool(item.get("evidence_verified")) for item in mismatches
                ),
                "mismatch_warning_multiset_exact": (
                    baseline_mismatch_fingerprints
                    == embedded_mismatch_fingerprints
                ),
                "sample_quota_validation_passed": bool(
                    sample_summary["quota_validation_passed"]
                ),
                "sample_is_full_dataset_subset": all(
                    str(record["doc_id"]) in parsed_doc_ids
                    for record in selection_records
                ),
                "sample_source_lines_copied_verbatim": True,
                "content_dataset_not_rewritten": (
                    parsed.stat().st_size == parsed_initial_stat.st_size
                    and parsed.stat().st_mtime_ns == parsed_initial_stat.st_mtime_ns
                ),
                "correction_lineage_not_generated": True,
            }
            ready_for_downstream = all(quality_gates.values())
            finalized_status_counts = {
                str(status): status_counts.get(str(status), 0)
                for status in MetadataValidationStatus
            }
            finalized_parse_status_counts = {
                status: parse_status_counts.get(status, 0)
                for status in ("success", "partial", "failed")
            }

            staged_metadata_info = _artifact_info(
                staged_metadata,
                relative_path="../data/processed/metadata_normalized.jsonl",
                record_count=metadata_record_count,
                schema_ref="../docs/data_contract.md",
                bundled=False,
                sha256=metadata_digest.hexdigest(),
            )
            staged_handoff_metadata_info = _artifact_info(
                staged_handoff_metadata,
                relative_path="metadata_normalized.jsonl",
                record_count=metadata_record_count,
                schema_ref="data_contract.md",
                bundled=True,
                sha256=metadata_digest.hexdigest(),
            )
            staged_sample_info = _artifact_info(
                staged_sample,
                relative_path="sample_disclosures.jsonl",
                record_count=len(selection_records),
                schema_ref="../docs/phase2_schema.md",
                bundled=True,
                sha256=sample_sha256,
            )
            if staged_sample_info["size_bytes"] != sample_size:
                raise RuntimeError("Sample byte count changed after copy")
            staged_selection_info = _artifact_info(
                staged_selection,
                relative_path="sample_selection.jsonl",
                record_count=len(selection_records),
                schema_ref="data_contract.md#대표-sample-계약",
                bundled=True,
            )
            staged_contract_info = _artifact_info(
                staged_contract,
                relative_path="data_contract.md",
                record_count=None,
                schema_ref=None,
                bundled=True,
                media_type="text/markdown",
            )
            staged_mismatch_info = _artifact_info(
                staged_mismatches,
                relative_path="../reports/metadata_mismatches.jsonl",
                record_count=len(mismatches),
                schema_ref="../docs/data_contract.md#mismatch-계약",
                bundled=False,
            )

            finished = _utc_now()
            summary: dict[str, Any] = {
                "schema_version": PHASE3_SCHEMA_VERSION,
                "content_schema_version": CONTENT_SCHEMA_VERSION,
                "run": {
                    "started_at_utc": started.isoformat(),
                    "finished_at_utc": finished.isoformat(),
                    "duration_seconds": round((finished - started).total_seconds(), 3),
                    "python_version": platform.python_version(),
                    "python_executable": sys.executable,
                    "platform": platform.platform(),
                    "expected_document_count": expected_document_count,
                    "expected_mismatch_count": expected_mismatch_count,
                    "sample_count_requested": sample_count,
                    "streaming_jsonl": True,
                    "full_content_loaded_into_memory": False,
                },
                "source": {
                    "kind": source.kind,
                    "path": str(source.location),
                    "logical_root": source.logical_root,
                    "manifest_relative_path": manifest_entry.relative_path,
                    "manifest_sha256": manifest_sha256,
                    "canonical_metadata_source": "manifest.jsonl",
                    "parsed_path": str(parsed),
                    "parsed_size_bytes": parsed.stat().st_size,
                    "parsed_sha256": parsed_digest.hexdigest(),
                    "parse_warnings": warning_source,
                },
                "corpus": {
                    "logical_document_count": len(manifest.records),
                    "metadata_record_count": metadata_record_count,
                    "parse_status_counts": finalized_parse_status_counts,
                },
                "joins": {
                    "join_key": "doc_id",
                    "secondary_check": "rcept_no",
                    "expected_cardinality": "one_to_one",
                    "manifest_document_count": len(manifest.records),
                    "parsed_document_count": parsed_line_count,
                    "metadata_record_count": metadata_record_count,
                    "matched_count": matched_count,
                    "manifest_without_content_count": len(manifest_without_content),
                    "manifest_without_content_examples": manifest_without_content[:50],
                    "content_without_manifest_count": len(unexpected_content_ids),
                    "content_without_manifest_examples": unexpected_content_ids[:50],
                    "duplicate_doc_id_count": len(duplicate_doc_ids),
                    "duplicate_rcept_no_count": len(duplicate_rcept_nos),
                    "malformed_content_line_count": len(malformed_content_lines),
                    "malformed_content_line_examples": malformed_content_lines[:20],
                    "link_inconsistency_count": len(link_inconsistency_docs),
                    "content_missing_count": content_missing_count,
                    "suspected_linkage_documents": suspected_linkage,
                },
                "validation": {
                    "status_counts": finalized_status_counts,
                    "warning_counts_by_code": dict(
                        sorted(validation_warning_counts.items())
                    ),
                },
                "completeness": finalized_completeness,
                "uniqueness": uniqueness,
                "comparison_coverage": {
                    field: {
                        "source_observation_count": comparison_coverage.get(field, 0),
                        "unobserved_is_not_match": True,
                    }
                    for field in CANONICAL_METADATA_FIELDS
                },
                "mismatches": {
                    "phase2_occurrence_count": len(mismatches),
                    "embedded_occurrence_count": sum(
                        embedded_mismatch_fingerprints.values()
                    ),
                    "warning_multiset_exact": baseline_mismatch_fingerprints
                    == embedded_mismatch_fingerprints,
                    "baseline_only_occurrence_count": sum(
                        (baseline_mismatch_fingerprints - embedded_mismatch_fingerprints).values()
                    ),
                    "embedded_only_occurrence_count": sum(
                        (embedded_mismatch_fingerprints - baseline_mismatch_fingerprints).values()
                    ),
                    "document_count": len(mismatch_doc_ids),
                    "counts_by_field": normalized_field_counts,
                    "counts_by_reason": all_reasons,
                    "counts_by_severity": dict(sorted(counts_by_severity.items())),
                    "canonical_company_count": len(canonical_companies),
                    "source_company_variant_count": len(source_variants),
                    "ambiguous_source_alias_count": len(
                        warning_source["ambiguous_source_aliases"]
                    ),
                    "examples": mismatch_examples,
                    "comparison_policy_version": COMPARISON_POLICY_VERSION,
                },
                "canonicalization": {
                    "authority": "manifest.jsonl",
                    "rcept_date_rule": "strict YYYYMMDD -> YYYY-MM-DD from rcept_dt only",
                    "rcept_date_prefix_difference_count": prefix_difference_count,
                    "corp_listed_name_difference_count": corp_listed_count,
                    "identifier_numeric_casting": False,
                    "source_metadata_overwrites_canonical": False,
                    "missing_value_inference": False,
                    "correction_lineage_generated": False,
                },
                "safe_filter_fields": _safe_filter_contract(examples),
                "sample": sample_summary,
                "quality_gates": quality_gates,
                "ready_for_downstream": ready_for_downstream,
                "known_limitations": [
                    "corp_name is the manifest canonical identity and may differ from historical legal/display names in source filings",
                    (
                        "nullable metadata remains unfilled: "
                        f"doc_subtype={finalized_completeness['doc_subtype']['null_count']}, "
                        f"base_year={finalized_completeness['base_year']['null_count']}, "
                        f"base_month={finalized_completeness['base_month']['null_count']}"
                    ),
                    "is_correction does not identify a parent, root, or correction chain",
                    "source metadata fields not observed by the parser are not asserted equal",
                    "Phase 2 partial status remains a separate parse-quality signal",
                    "No PostgreSQL schema, chunking, embedding, search, or reranking is included",
                ],
                "output_files": {
                    "metadata_normalized": {
                        "path": str((output / METADATA_FILENAME).resolve()),
                        **staged_metadata_info,
                    },
                    "metadata_mismatches": {
                        "path": str((reports / MISMATCH_FILENAME).resolve()),
                        **staged_mismatch_info,
                    },
                    "handoff_metadata": {
                        "path": str((handoff / METADATA_FILENAME).resolve()),
                        **staged_handoff_metadata_info,
                    },
                    "sample_disclosures": {
                        "path": str((handoff / "sample_disclosures.jsonl").resolve()),
                        **staged_sample_info,
                    },
                    "sample_selection": {
                        "path": str((handoff / "sample_selection.jsonl").resolve()),
                        **staged_selection_info,
                    },
                    "data_contract": {
                        "path": str((handoff / "data_contract.md").resolve()),
                        **staged_contract_info,
                    },
                },
            }

            staged_summary.write_bytes(_json_bytes(summary, pretty=True))
            staged_report.write_text(
                render_metadata_report(summary), encoding="utf-8", newline="\n"
            )
            staged_handoff_readme.write_text(
                render_handoff_readme(summary), encoding="utf-8", newline="\n"
            )

            artifacts = {
                "content_dataset": {
                    "relative_path": "../data/processed/parsed_disclosures.jsonl",
                    "bundled": False,
                    "media_type": "application/x-ndjson",
                    "encoding": "utf-8",
                    "newline": "LF",
                    "schema_ref": "../docs/phase2_schema.md",
                    "record_count": parsed_line_count,
                    "size_bytes": parsed.stat().st_size,
                    "sha256": parsed_digest.hexdigest(),
                },
                "metadata_dataset": staged_handoff_metadata_info,
                "sample_dataset": staged_sample_info,
                "sample_selection": staged_selection_info,
                "data_contract": staged_contract_info,
                "metadata_mismatches": staged_mismatch_info,
                "metadata_summary": _artifact_info(
                    staged_summary,
                    relative_path="../reports/metadata_summary.json",
                    record_count=1,
                    schema_ref="data_contract.md",
                    bundled=False,
                    media_type="application/json",
                ),
                "metadata_report": _artifact_info(
                    staged_report,
                    relative_path="../reports/metadata_report.md",
                    record_count=None,
                    schema_ref=None,
                    bundled=False,
                    media_type="text/markdown",
                ),
                "handoff_readme": _artifact_info(
                    staged_handoff_readme,
                    relative_path="README.md",
                    record_count=None,
                    schema_ref=None,
                    bundled=True,
                    media_type="text/markdown",
                ),
            }
            run_identity_inputs = {
                "manifest_sha256": manifest_sha256,
                "parsed_sha256": parsed_digest.hexdigest(),
                "parse_warnings_sha256": warning_source["sha256"],
                "data_contract_sha256": staged_contract_info["sha256"],
                "comparison_policy_version": COMPARISON_POLICY_VERSION,
                "sample_policy_version": SAMPLE_POLICY_VERSION,
                "sample_count": sample_count,
                "expected_document_count": expected_document_count,
                "expected_mismatch_count": expected_mismatch_count,
            }
            run_id = hashlib.sha256(
                json.dumps(
                    run_identity_inputs,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            handoff_manifest = {
                "manifest_schema_version": "1.0.0",
                "handoff_schema_version": PHASE3_SCHEMA_VERSION,
                "content_schema_version": CONTENT_SCHEMA_VERSION,
                "metadata_schema_version": PHASE3_SCHEMA_VERSION,
                "generated_at_utc": finished.isoformat(),
                "run_id": run_id,
                "run_identity_inputs": run_identity_inputs,
                "generator": {
                    "package": "disclosure-preprocess",
                    "version": "0.3.0",
                    "python_version": platform.python_version(),
                },
                "canonical_metadata_source": {
                    "authority": "manifest",
                    "source_kind": source.kind,
                    "source_path": str(source.location),
                    "manifest_relative_path": manifest_entry.relative_path,
                    "manifest_sha256": manifest_sha256,
                },
                "artifacts": artifacts,
                "joins": [
                    {
                        "left_artifact": "content_dataset",
                        "right_artifact": "metadata_dataset",
                        "join_key": "doc_id",
                        "secondary_check": "rcept_no",
                        "expected_cardinality": "one_to_one",
                        "matched_count": matched_count,
                        "left_unmatched_count": len(unexpected_content_ids),
                        "right_unmatched_count": len(manifest_without_content),
                        "duplicate_key_count": len(duplicate_doc_ids),
                    }
                ],
                "corpus_counts": {
                    "manifest": len(manifest.records),
                    "content": parsed_line_count,
                    "metadata": metadata_record_count,
                    "sample": len(selection_records),
                    "parse_status": finalized_parse_status_counts,
                    "metadata_validation_status": finalized_status_counts,
                },
                "sample_selection": sample_summary,
                "quality_gates": quality_gates,
                "ready_for_downstream": ready_for_downstream,
                "safe_filter_fields": list(SAFE_FILTER_FIELDS),
                "null_semantics": "null means unknown/not available from the canonical source; it is not zero, none, or not applicable",
                "known_limitations": summary["known_limitations"],
                "excluded_scope": [
                    "correction lineage",
                    "PostgreSQL/pgvector",
                    "chunking/embedding",
                    "keyword/vector search",
                    "reranking",
                    "LLM extraction or summarization",
                ],
            }
            staged_handoff_manifest.write_bytes(
                _json_bytes(handoff_manifest, pretty=True)
            )

            if not ready_for_downstream:
                raise RuntimeError(
                    "Phase 3 quality gates failed; staged outputs were not published: "
                    + json.dumps(
                        {key: value for key, value in quality_gates.items() if not value},
                        sort_keys=True,
                    )
                )

            publish_items = [
                (staged_metadata, output / METADATA_FILENAME, False),
                (staged_mismatches, reports / MISMATCH_FILENAME, False),
                (staged_summary, reports / METADATA_SUMMARY_FILENAME, False),
                (staged_report, reports / METADATA_REPORT_FILENAME, False),
                (staged_handoff_metadata, handoff / METADATA_FILENAME, False),
                (staged_sample, handoff / "sample_disclosures.jsonl", False),
                (staged_selection, handoff / "sample_selection.jsonl", False),
                (staged_contract, handoff / "data_contract.md", False),
                (staged_handoff_readme, handoff / "README.md", False),
                (staged_handoff_manifest, handoff / "handoff_manifest.json", True),
            ]
            _publish_artifacts(stage_root, publish_items)
            return summary
        except BaseException:
            if stage_root.exists():
                shutil.rmtree(stage_root)
            raise


__all__ = ["normalize_metadata_corpus"]
