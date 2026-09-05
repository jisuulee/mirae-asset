#!/usr/bin/env python3
"""Independently validate published Phase 5 artifacts.

This script intentionally does not import the Phase 5 assembly implementation.
It recomputes hashes, cardinalities, joins, provenance counts, and correction
graph safety from the published files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "5.0.0"
EXPECTED_COUNTS = {
    "parsed_disclosures": 4204,
    "normalized_metadata": 4204,
    "correction_lineage": 1004,
    "correction_chains": 407,
    "document_index": 4204,
}


class ValidationFailure(RuntimeError):
    pass


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(path: Path, *, schema: str | None = None) -> tuple[list[dict[str, Any]], str]:
    result: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationFailure(f"invalid JSONL {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValidationFailure(f"non-object JSONL {path}:{line_number}")
            if schema is not None and row.get("schema_version") != schema:
                raise ValidationFailure(
                    f"schema mismatch {path}:{line_number}: {row.get('schema_version')} != {schema}"
                )
            result.append(row)
    return result, digest.hexdigest()


def _unique(rows: Sequence[Mapping[str, Any]], key: str, label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value:
            raise ValidationFailure(f"{label} has empty {key}")
        if value in result:
            raise ValidationFailure(f"{label} duplicates {key}={value}")
        result[value] = row
    return result


def _load_manifest(source: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
    path = Path(str(source.get("path") or ""))
    if not path.exists():
        raise ValidationFailure(f"manifest data root not found: {path}")
    raw = b""
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            matches = [
                name
                for name in archive.namelist()
                if unicodedata.normalize("NFC", Path(name.replace("\\", "/")).name)
                == "manifest.jsonl"
            ]
            if len(matches) != 1:
                raise ValidationFailure(f"expected one ZIP manifest, observed {len(matches)}")
            raw = archive.read(matches[0])
    elif path.is_dir():
        matches = list(path.rglob("manifest.jsonl"))
        if len(matches) != 1:
            raise ValidationFailure(f"expected one directory manifest, observed {len(matches)}")
        raw = matches[0].read_bytes()
    else:
        raise ValidationFailure(f"unsupported manifest data root: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationFailure(f"invalid manifest line {line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValidationFailure(f"non-object manifest line {line_number}")
        rows.append(row)
    return rows, hashlib.sha256(raw).hexdigest()


def _observe_provenance(kind: str, value: object, counts: Counter[str], missing: Counter[str]) -> None:
    counts[kind] += 1
    if not isinstance(value, Mapping) or not value.get("source_file") or not value.get("file_role"):
        missing[kind] += 1


def _table_has_text(table: object) -> bool:
    if not isinstance(table, Mapping) or not isinstance(table.get("raw_rows"), list):
        return False
    return any(
        isinstance(cell, Mapping)
        and isinstance(cell.get("raw_text"), str)
        and bool(str(cell["raw_text"]).strip())
        for row in table["raw_rows"]
        if isinstance(row, list)
        for cell in row
    )


def _scan_parsed(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    doc_ids: set[str] = set()
    receipts: set[str] = set()
    statuses: Counter[str] = Counter()
    provenance: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    count = 0
    source_count = 0
    source_sha_count = 0
    main_title_count = 0
    main_body_count = 0
    main_section_docs = 0
    main_section_count = 0
    main_table_docs = 0
    main_table_count = 0
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationFailure(f"invalid parsed JSONL line {line_number}: {exc}") from exc
            if not isinstance(record, Mapping):
                raise ValidationFailure(f"non-object parsed line {line_number}")
            count += 1
            doc_id = str(record.get("doc_id") or "")
            receipt = str(record.get("rcept_no") or "")
            if not doc_id or doc_id in doc_ids:
                raise ValidationFailure(f"missing or duplicate parsed doc_id at line {line_number}")
            if not receipt or receipt in receipts:
                raise ValidationFailure(f"missing or duplicate parsed rcept_no at line {line_number}")
            doc_ids.add(doc_id)
            receipts.add(receipt)
            statuses[str(record.get("parse_status") or "failed")] += 1
            sources = record.get("source_files")
            if not isinstance(sources, list):
                raise ValidationFailure(f"source_files missing for {doc_id}")
            source_count += len(sources)
            source_sha_count += sum(
                isinstance(source, Mapping)
                and isinstance(source.get("sha256"), str)
                and len(str(source["sha256"])) == 64
                for source in sources
            )
            components: list[Mapping[str, Any]] = []
            main = record.get("main_document")
            if isinstance(main, Mapping):
                components.append(main)
            for key in ("attachments", "alternative_representations"):
                values = record.get(key)
                if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
                    components.extend(value for value in values if isinstance(value, Mapping))
            main_content = main.get("content") if isinstance(main, Mapping) else None
            if isinstance(main_content, Mapping):
                title = main_content.get("title")
                tables = main_content.get("tables")
                sections = main_content.get("sections")
                body = main_content.get("body_text")
                main_title_count += isinstance(title, str) and bool(title.strip())
                has_body = isinstance(body, str) and bool(body.strip())
                if not has_body and isinstance(tables, list):
                    has_body = any(_table_has_text(table) for table in tables)
                main_body_count += has_body
                section_count = len(sections) if isinstance(sections, list) else 0
                table_count = len(tables) if isinstance(tables, list) else 0
                main_section_docs += section_count > 0
                main_section_count += section_count
                main_table_docs += table_count > 0
                main_table_count += table_count
            for component in components:
                content = component.get("content")
                if not isinstance(content, Mapping):
                    # content_included=false alternative representations have
                    # no serialized structured content to validate.
                    continue
                _observe_provenance("document", content.get("provenance"), provenance, missing)
                title = content.get("title")
                if isinstance(title, str) and title.strip():
                    _observe_provenance("title", content.get("title_provenance"), provenance, missing)
                sections = content.get("sections")
                if isinstance(sections, list):
                    for section in sections:
                        _observe_provenance("section", section.get("provenance") if isinstance(section, Mapping) else None, provenance, missing)
                blocks = content.get("block_sequence")
                if isinstance(blocks, list):
                    for block in blocks:
                        _observe_provenance("block", block.get("provenance") if isinstance(block, Mapping) else None, provenance, missing)
                        if isinstance(block, Mapping) and isinstance(block.get("items"), list):
                            for item in block["items"]:
                                _observe_provenance("list_item", item.get("provenance") if isinstance(item, Mapping) else None, provenance, missing)
                tables = content.get("tables")
                if isinstance(tables, list):
                    for table in tables:
                        _observe_provenance("table", table.get("provenance") if isinstance(table, Mapping) else None, provenance, missing)
                        if not isinstance(table, Mapping) or not isinstance(table.get("raw_rows"), list):
                            continue
                        for row in table["raw_rows"]:
                            if not isinstance(row, list):
                                continue
                            for cell in row:
                                _observe_provenance("table_cell", cell.get("provenance") if isinstance(cell, Mapping) else None, provenance, missing)
    return {
        "sha256": digest.hexdigest(),
        "record_count": count,
        "doc_ids": doc_ids,
        "rcept_nos": receipts,
        "parse_status_counts": dict(statuses),
        "physical_source_count": source_count,
        "source_sha256_count": source_sha_count,
        "main_title_count": main_title_count,
        "main_body_count": main_body_count,
        "main_section_document_count": main_section_docs,
        "main_section_count": main_section_count,
        "main_table_document_count": main_table_docs,
        "main_table_count": main_table_count,
        "all_physical_table_count": provenance["table"],
        "provenance_item_count": sum(provenance.values()),
        "provenance_missing_count": sum(missing.values()),
    }


def _validate_graph(
    metadata_by_id: Mapping[str, Mapping[str, Any]],
    lineage: Sequence[Mapping[str, Any]],
    chains: Sequence[Mapping[str, Any]],
    index_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    by_receipt = {
        str(row["metadata"]["rcept_no"]): row
        for row in metadata_by_id.values()
    }
    correction_ids = {
        doc_id
        for doc_id, row in metadata_by_id.items()
        if row.get("metadata", {}).get("is_correction") is True
    }
    lineage_by_id = _unique(lineage, "doc_id", "lineage")
    if set(lineage_by_id) != correction_ids:
        raise ValidationFailure("lineage rows are not exactly the canonical correction set")
    edges: dict[str, str] = {}
    future = cross_company = evidence_less = 0
    status_counts: Counter[str] = Counter()
    for row in lineage:
        doc_id = str(row["doc_id"])
        resolution = row.get("resolution")
        if not isinstance(resolution, Mapping):
            raise ValidationFailure(f"resolution missing for {doc_id}")
        status = str(resolution.get("status"))
        status_counts[status] += 1
        target_receipt = resolution.get("target_rcept_no")
        if status in {"linked_exact", "linked_inferred"}:
            if not isinstance(target_receipt, str) or target_receipt not in by_receipt:
                raise ValidationFailure(f"linked target absent for {doc_id}")
            target = by_receipt[target_receipt]
            current_meta = metadata_by_id[doc_id]["metadata"]
            target_meta = target["metadata"]
            cross_company += current_meta.get("corp_code") != target_meta.get("corp_code")
            future += str(target_meta.get("rcept_date")) > str(current_meta.get("rcept_date"))
            evidence = row.get("correction_evidence")
            evidence_less += not isinstance(evidence, Mapping) or int(evidence.get("evidence_count") or 0) < 1
            edges[str(row["rcept_no"])] = target_receipt
        elif target_receipt is not None:
            raise ValidationFailure(f"non-linked status has target for {doc_id}")
    if future or cross_company or evidence_less:
        raise ValidationFailure(
            f"graph safety violation future={future} cross_company={cross_company} evidence_less={evidence_less}"
        )
    cycles = 0
    for start in edges:
        seen: set[str] = set()
        current = start
        while current in edges:
            if current in seen:
                cycles += 1
                break
            seen.add(current)
            current = edges[current]
    if cycles:
        raise ValidationFailure(f"lineage cycle count={cycles}")

    member_ids: set[str] = set()
    complete = partial = 0
    unsafe_flags = 0
    for chain in chains:
        is_complete = chain.get("is_complete") is True
        complete += is_complete
        partial += not is_complete
        members = chain.get("members")
        if not isinstance(members, list) or not members:
            raise ValidationFailure("empty or invalid correction chain")
        latest = chain.get("latest_rcept_no")
        if is_complete:
            root = chain.get("root_rcept_no")
            if not isinstance(root, str) or root not in by_receipt:
                raise ValidationFailure("complete chain has no internal root")
            if by_receipt[root]["metadata"].get("is_correction") is True:
                raise ValidationFailure("complete chain root is a correction")
        for member in members:
            if not isinstance(member, Mapping):
                raise ValidationFailure("invalid chain member")
            doc_id = str(member.get("doc_id") or "")
            receipt = str(member.get("rcept_no") or "")
            if doc_id in member_ids:
                raise ValidationFailure(f"document appears in multiple chains: {doc_id}")
            member_ids.add(doc_id)
            index_lineage = index_by_id[doc_id].get("lineage", {})
            latest_flag = index_lineage.get("is_latest_in_complete_chain")
            superseded_flag = index_lineage.get("is_superseded_in_complete_chain")
            if is_complete:
                if latest_flag is not (receipt == latest) or superseded_flag is not (receipt != latest):
                    unsafe_flags += 1
            elif latest_flag is not None or superseded_flag is not None:
                unsafe_flags += 1
    for row in lineage:
        status = row["resolution"]["status"]
        if status in {"ambiguous", "unresolved"}:
            projection = index_by_id[str(row["doc_id"])]["lineage"]
            if projection.get("is_latest_in_complete_chain") is not None or projection.get("is_superseded_in_complete_chain") is not None:
                unsafe_flags += 1
    if unsafe_flags:
        raise ValidationFailure(f"unsafe complete-chain boolean count={unsafe_flags}")
    return {
        "future_target_count": future,
        "cross_company_link_count": cross_company,
        "evidence_less_link_count": evidence_less,
        "cycle_count": cycles,
        "complete_chain_count": complete,
        "partial_chain_count": partial,
        "unsafe_latest_boolean_count": unsafe_flags,
        **{f"status_{key}": value for key, value in status_counts.items()},
    }


def validate_project(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    manifest_path = root / "data" / "final" / "artifact_manifest.json"
    artifact_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if artifact_manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValidationFailure("artifact manifest schema is not 5.0.0")
    if artifact_manifest.get("status") != "PASS":
        raise ValidationFailure("artifact manifest is not PASS")
    artifacts = artifact_manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValidationFailure("artifact manifest has no artifacts object")
    required = set(EXPECTED_COUNTS)
    if not required.issubset(artifacts):
        raise ValidationFailure(f"artifact manifest missing {sorted(required - set(artifacts))}")
    paths: dict[str, Path] = {}
    for name, expected_count in EXPECTED_COUNTS.items():
        info = artifacts[name]
        path = root / str(info["path"])
        paths[name] = path
        if not path.is_file():
            raise ValidationFailure(f"artifact not found: {path}")
        if path.stat().st_size != int(info["size_bytes"]):
            raise ValidationFailure(f"artifact size mismatch: {name}")
        if int(info["record_count"]) != expected_count:
            raise ValidationFailure(f"artifact manifest record count mismatch: {name}")

    metadata, metadata_hash = _rows(paths["normalized_metadata"], schema="3.0.0")
    lineage, lineage_hash = _rows(paths["correction_lineage"], schema="4.0.0")
    chains, chains_hash = _rows(paths["correction_chains"], schema="4.0.0")
    index, index_hash = _rows(paths["document_index"], schema="5.0.0")
    for name, observed in {
        "normalized_metadata": metadata_hash,
        "correction_lineage": lineage_hash,
        "correction_chains": chains_hash,
        "document_index": index_hash,
    }.items():
        if observed != artifacts[name]["sha256"]:
            raise ValidationFailure(f"artifact SHA-256 mismatch: {name}")
    for name, rows in {
        "normalized_metadata": metadata,
        "correction_lineage": lineage,
        "correction_chains": chains,
        "document_index": index,
    }.items():
        if len(rows) != EXPECTED_COUNTS[name]:
            raise ValidationFailure(f"record count mismatch: {name}")

    metadata_by_id = _unique(metadata, "doc_id", "metadata")
    index_by_id = _unique(index, "doc_id", "document index")
    manifest_rows, manifest_hash = _load_manifest(artifact_manifest["source"])
    manifest_by_id = _unique(manifest_rows, "doc_id", "manifest")
    if manifest_hash != artifact_manifest["source"]["manifest_sha256"]:
        raise ValidationFailure("manifest SHA-256 mismatch")

    parsed_stats = _scan_parsed(paths["parsed_disclosures"])
    if parsed_stats["sha256"] != artifacts["parsed_disclosures"]["sha256"]:
        raise ValidationFailure("artifact SHA-256 mismatch: parsed_disclosures")
    if parsed_stats["record_count"] != EXPECTED_COUNTS["parsed_disclosures"]:
        raise ValidationFailure("parsed record count mismatch")
    expected_ids = set(manifest_by_id)
    if set(metadata_by_id) != expected_ids or parsed_stats["doc_ids"] != expected_ids or set(index_by_id) != expected_ids:
        raise ValidationFailure("manifest/metadata/parsed/index doc_id sets are not identical")
    for doc_id in expected_ids:
        manifest_row = manifest_by_id[doc_id]
        metadata_row = metadata_by_id[doc_id]
        index_row = index_by_id[doc_id]
        receipt = str(manifest_row.get("rcept_no") or "")
        if str(metadata_row.get("rcept_no") or "") != receipt or str(index_row.get("rcept_no") or "") != receipt:
            raise ValidationFailure(f"rcept_no join mismatch: {doc_id}")
        canonical = metadata_row.get("metadata", {})
        if index_row.get("metadata") != canonical:
            raise ValidationFailure(f"index canonical metadata mismatch: {doc_id}")
        for field in (
            "doc_id", "corp_code", "corp_name", "listed_name", "stock_code",
            "industry", "sector", "doc_group", "doc_subtype", "report_nm",
            "is_correction", "rcept_no", "rcept_dt", "flr_nm", "base_year", "base_month",
        ):
            if canonical.get(field) != manifest_row.get(field):
                raise ValidationFailure(f"manifest metadata mismatch {doc_id}:{field}")

    graph = _validate_graph(metadata_by_id, lineage, chains, index_by_id)
    summary = json.loads((root / "reports" / "final_quality_summary.json").read_text(encoding="utf-8"))
    if summary.get("status") != "PASS" or not all(summary.get("quality_gates", {}).values()):
        raise ValidationFailure("final quality summary is not an all-PASS result")
    for key in (
        "physical_source_count", "source_sha256_count", "main_title_count",
        "main_body_count", "main_section_document_count", "main_section_count",
        "main_table_document_count", "main_table_count", "all_physical_table_count",
        "provenance_item_count", "provenance_missing_count",
    ):
        if parsed_stats[key] != summary["content_quality"][key]:
            raise ValidationFailure(f"independent content metric mismatch: {key}")
    if parsed_stats["parse_status_counts"] != {k: v for k, v in summary["parse_status_counts"].items() if v}:
        raise ValidationFailure("independent parse-status counts mismatch")
    if parsed_stats["provenance_missing_count"] != 0:
        raise ValidationFailure("structured provenance is missing")

    handoff = root / "handoff" / "role2"
    if (handoff / "artifact_manifest.json").read_bytes() != manifest_path.read_bytes():
        raise ValidationFailure("Role 2 artifact manifest is not byte-identical")
    if _hash_file(handoff / "document_index.jsonl") != index_hash:
        raise ValidationFailure("Role 2 document index is not byte-identical")
    if (handoff / "data_contract.md").read_bytes() != (root / "docs" / "data_contract.md").read_bytes():
        raise ValidationFailure("Role 2 data contract is not the canonical contract")
    sample, sample_hash = _rows(handoff / "sample_disclosures.jsonl")
    sample_index, _ = _rows(handoff / "sample_document_index.jsonl", schema="5.0.0")
    selection, _ = _rows(handoff / "sample_selection.jsonl", schema="5.0.0")
    if not (50 <= len(sample) <= 100) or len(sample) != len(sample_index) or len(sample) != len(selection):
        raise ValidationFailure("Role 2 sample cardinality is invalid")
    if sample_hash != artifact_manifest["delivery"]["files"]["sample_disclosures.jsonl"]["sha256"]:
        raise ValidationFailure("Role 2 sample hash mismatch")
    sample_ids = {str(row["doc_id"]) for row in sample}
    if sample_ids != {str(row["doc_id"]) for row in sample_index} or sample_ids != {str(row["doc_id"]) for row in selection}:
        raise ValidationFailure("Role 2 sample doc_id sets differ")
    for row in selection:
        reasons = row.get("selection_reasons")
        if not isinstance(reasons, list) or not reasons:
            raise ValidationFailure(f"sample selection reason missing: {row.get('doc_id')}")

    failures_path = root / "reports" / "final_validation_failures.jsonl"
    if failures_path.read_bytes().strip():
        raise ValidationFailure("final_validation_failures.jsonl is not empty")
    return {
        "ready": True,
        "schema_version": SCHEMA_VERSION,
        "document_count": len(index),
        "sample_count": len(sample),
        "artifact_hashes_verified": len(EXPECTED_COUNTS),
        "manifest_hash_verified": True,
        "cross_phase_join_verified": True,
        "provenance_missing_count": parsed_stats["provenance_missing_count"],
        "complete_chain_count": graph["complete_chain_count"],
        "partial_chain_count": graph["partial_chain_count"],
        "unsafe_latest_boolean_count": graph["unsafe_latest_boolean_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = validate_project(args.project_root)
    except Exception as exc:
        sys.stdout.buffer.write(
            (
                json.dumps(
                    {"ready": False, "error": str(exc)},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
        )
        return 1
    sys.stdout.buffer.write(
        (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
