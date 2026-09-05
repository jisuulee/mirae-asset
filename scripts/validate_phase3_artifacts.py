#!/usr/bin/env python3
"""Independently validate published Phase 3 artifacts without rescanning content."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from disclosure_preprocess.metadata_normalization import (  # noqa: E402
    format_rcept_date,
)


FORBIDDEN_PHASE3_KEYS = frozenset(
    {
        "target_rcept_no",
        "root_rcept_no",
        "parent_rcept_no",
        "correction_chain",
        "correction_reason",
        "embedding",
        "chunks",
        "reranking",
    }
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise AssertionError(f"Expected JSON object: {path}")
    return value


def _sha256(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            value = json.loads(raw_line)
            if not isinstance(value, dict):
                raise AssertionError(f"Non-object row at {path}:{line_number}")
            rows.append(value)
    return rows


def _assert_no_forbidden_keys(value: object, path: str = "$") -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_PHASE3_KEYS.intersection(value)
        if forbidden:
            raise AssertionError(f"Forbidden keys at {path}: {sorted(forbidden)}")
        for key, child in value.items():
            _assert_no_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_forbidden_keys(child, f"{path}[{index}]")


def _validate_sample_lines(
    parsed_path: Path,
    sample_path: Path,
    selection_rows: list[dict[str, Any]],
) -> None:
    with (
        parsed_path.open("rb") as parsed_stream,
        sample_path.open("rb") as sample_stream,
    ):
        for selection in selection_rows:
            sample_line = sample_stream.readline()
            if not sample_line:
                raise AssertionError("sample_disclosures.jsonl ended early")
            expected_size = int(selection["source_record_size_bytes"])
            expected_hash = str(selection["source_line_sha256"])
            if len(sample_line) != expected_size:
                raise AssertionError(
                    f"Sample byte length mismatch for {selection['doc_id']}"
                )
            if hashlib.sha256(sample_line).hexdigest() != expected_hash:
                raise AssertionError(f"Sample hash mismatch for {selection['doc_id']}")
            sample_record = json.loads(sample_line)
            if sample_record.get("doc_id") != selection["doc_id"]:
                raise AssertionError(f"Sample doc_id mismatch: {selection['doc_id']}")
            if sample_record.get("rcept_no") != selection["rcept_no"]:
                raise AssertionError(f"Sample rcept_no mismatch: {selection['doc_id']}")

            parsed_stream.seek(int(selection["source_byte_offset"]))
            original_line = parsed_stream.read(expected_size)
            if original_line != sample_line:
                raise AssertionError(
                    f"Sample is not byte-identical to Phase 2: {selection['doc_id']}"
                )
        if sample_stream.read(1):
            raise AssertionError("sample_disclosures.jsonl contains extra rows")


def validate_phase3_artifacts(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    output_metadata = root / "data" / "processed" / "metadata_normalized.jsonl"
    parsed_path = root / "data" / "processed" / "parsed_disclosures.jsonl"
    report_dir = root / "reports"
    handoff_dir = root / "handoff"
    summary = _load_json(report_dir / "metadata_summary.json")
    handoff_manifest = _load_json(handoff_dir / "handoff_manifest.json")

    assert summary["ready_for_downstream"] is True
    assert handoff_manifest["ready_for_downstream"] is True
    failed_gates = [
        key for key, passed in summary["quality_gates"].items() if passed is not True
    ]
    if failed_gates:
        raise AssertionError(f"Failed quality gates: {failed_gates}")

    expected_count = int(summary["corpus"]["logical_document_count"])
    metadata_rows = _jsonl_rows(output_metadata)
    if len(metadata_rows) != expected_count:
        raise AssertionError("metadata row count differs from logical corpus")
    doc_ids: set[str] = set()
    rcept_nos: set[str] = set()
    status_counts: Counter[str] = Counter()
    for row in metadata_rows:
        _assert_no_forbidden_keys(row)
        doc_id = row.get("doc_id")
        rcept_no = row.get("rcept_no")
        metadata = row.get("metadata")
        validation = row.get("metadata_validation")
        if not isinstance(doc_id, str) or not isinstance(rcept_no, str):
            raise AssertionError("Phase 3 identifiers must be strings")
        if doc_id in doc_ids or rcept_no in rcept_nos:
            raise AssertionError(f"Duplicate Phase 3 key: {doc_id}")
        doc_ids.add(doc_id)
        rcept_nos.add(rcept_no)
        if not isinstance(metadata, dict) or metadata.get("authority") != "manifest":
            raise AssertionError(f"Missing manifest authority: {doc_id}")
        for field in ("corp_code", "stock_code", "rcept_no", "doc_id"):
            if not isinstance(metadata.get(field), str):
                raise AssertionError(f"Non-string identifier {field}: {doc_id}")
        if metadata.get("rcept_date") != format_rcept_date(metadata.get("rcept_dt")):
            raise AssertionError(f"rcept_date is not a strict formatting: {doc_id}")
        if not isinstance(validation, dict):
            raise AssertionError(f"Missing metadata_validation: {doc_id}")
        status_counts[str(validation.get("status"))] += 1

    expected_statuses = summary["validation"]["status_counts"]
    for status in ("valid", "valid_with_warning", "invalid"):
        if status_counts.get(status, 0) != int(expected_statuses.get(status, 0)):
            raise AssertionError(f"Metadata status count mismatch: {status}")

    mismatch_rows = _jsonl_rows(report_dir / "metadata_mismatches.jsonl")
    if len(mismatch_rows) != int(summary["mismatches"]["phase2_occurrence_count"]):
        raise AssertionError("Mismatch occurrence count differs from summary")
    mismatch_ids: set[str] = set()
    mismatch_counts_by_reason: Counter[str] = Counter()
    mismatch_counts_by_field: Counter[str] = Counter()
    for row in mismatch_rows:
        mismatch_id = str(row.get("mismatch_id"))
        if mismatch_id in mismatch_ids:
            raise AssertionError(f"Duplicate mismatch_id: {mismatch_id}")
        mismatch_ids.add(mismatch_id)
        if row.get("doc_id") not in doc_ids:
            raise AssertionError(f"Mismatch orphan: {mismatch_id}")
        if row.get("evidence_verified") is not True:
            raise AssertionError(f"Unverified mismatch evidence: {mismatch_id}")
        mismatch_counts_by_reason[str(row.get("reason_code"))] += 1
        mismatch_counts_by_field[str(row.get("canonical_field"))] += 1
    for reason, count in summary["mismatches"]["counts_by_reason"].items():
        if mismatch_counts_by_reason.get(reason, 0) != int(count):
            raise AssertionError(f"Mismatch reason count differs: {reason}")
    for field, count in summary["mismatches"]["counts_by_field"].items():
        if field != "other" and mismatch_counts_by_field.get(field, 0) != int(count):
            raise AssertionError(f"Mismatch field count differs: {field}")

    selection_rows = _jsonl_rows(handoff_dir / "sample_selection.jsonl")
    sample_count = int(summary["sample"]["sample_count"])
    if len(selection_rows) != sample_count or not 50 <= sample_count <= 100:
        raise AssertionError("Handoff sample count is outside the contract")
    selected_ids = [str(row["doc_id"]) for row in selection_rows]
    if len(set(selected_ids)) != sample_count or not set(selected_ids).issubset(doc_ids):
        raise AssertionError("Handoff sample is not a unique metadata subset")
    ordinals = [int(row["source_record_ordinal"]) for row in selection_rows]
    if ordinals != sorted(ordinals):
        raise AssertionError("Handoff sample order differs from Phase 2 source order")
    _validate_sample_lines(
        parsed_path,
        handoff_dir / "sample_disclosures.jsonl",
        selection_rows,
    )

    artifacts = handoff_manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise AssertionError("handoff manifest artifacts must be an object")
    artifact_results: dict[str, dict[str, Any]] = {}
    for logical_name, raw_info in artifacts.items():
        if not isinstance(raw_info, dict):
            raise AssertionError(f"Invalid artifact descriptor: {logical_name}")
        artifact_path = (handoff_dir / str(raw_info["relative_path"])).resolve()
        if not artifact_path.is_file():
            raise AssertionError(f"Missing artifact: {artifact_path}")
        observed_size = artifact_path.stat().st_size
        if observed_size != int(raw_info["size_bytes"]):
            raise AssertionError(f"Artifact size mismatch: {logical_name}")
        # The 26.6GB content hash was calculated during the required one-pass
        # Phase 3 stream. Avoid an unnecessary second full scan here.
        observed_hash = (
            str(raw_info["sha256"])
            if logical_name == "content_dataset"
            else _sha256(artifact_path)
        )
        if observed_hash != raw_info["sha256"]:
            raise AssertionError(f"Artifact SHA-256 mismatch: {logical_name}")
        artifact_results[logical_name] = {
            "path": str(artifact_path),
            "size_bytes": observed_size,
            "sha256_verified": logical_name != "content_dataset",
        }

    output_metadata_hash = _sha256(output_metadata)
    handoff_metadata_hash = _sha256(handoff_dir / "metadata_normalized.jsonl")
    if output_metadata_hash != handoff_metadata_hash:
        raise AssertionError("Output and handoff metadata copies differ")
    # A handoff is an immutable Phase 3 snapshot. Later additive phases may
    # extend the canonical contract, so validate the bundled copy against the
    # SHA-256 pinned in its own manifest (already checked above), not against a
    # mutable future-version document.
    canonical_contract_matches_snapshot = _sha256(
        root / "docs" / "data_contract.md"
    ) == _sha256(handoff_dir / "data_contract.md")
    if parsed_path.stat().st_size != int(
        artifacts["content_dataset"]["size_bytes"]
    ):
        raise AssertionError("Phase 2 content size changed")

    stale_stages = list((root / "data" / "processed").glob(".phase3-*"))
    if stale_stages:
        raise AssertionError(f"Stale Phase 3 stages remain: {stale_stages}")

    return {
        "ready": True,
        "document_count": len(metadata_rows),
        "metadata_status_counts": {
            status: status_counts.get(status, 0)
            for status in ("valid", "valid_with_warning", "invalid")
        },
        "mismatch_count": len(mismatch_rows),
        "mismatch_document_count": len(
            {str(row["doc_id"]) for row in mismatch_rows}
        ),
        "sample_count": sample_count,
        "sample_source_lines_verified": sample_count,
        "artifact_count": len(artifact_results),
        "content_sha256_reused_from_one_pass": artifacts["content_dataset"][
            "sha256"
        ],
        "canonical_contract_matches_phase3_snapshot": canonical_contract_matches_snapshot,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Project root containing data, reports, docs, and handoff",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = validate_phase3_artifacts(args.project_root)
    except (AssertionError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Phase 3 artifact validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
