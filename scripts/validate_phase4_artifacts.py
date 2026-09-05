#!/usr/bin/env python3
"""Validate published Phase 4 sidecars without rescanning the 26.6GB content."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("rb") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise AssertionError(f"{path}:{number} is not an object")
            rows.append(row)
    return rows


def validate(root: Path) -> dict[str, Any]:
    root = root.resolve()
    summary = json.loads((root / "reports" / "correction_summary.json").read_text(encoding="utf-8"))
    lineage = _rows(root / "data" / "processed" / "correction_lineage.jsonl")
    chains = _rows(root / "data" / "processed" / "correction_chains.jsonl")
    ambiguous = _rows(root / "reports" / "correction_ambiguous.jsonl")
    unresolved = _rows(root / "reports" / "correction_unresolved.jsonl")
    external = _rows(root / "reports" / "correction_external_lookup.jsonl")
    profile = json.loads((root / "reports" / "correction_structure_summary.json").read_text(encoding="utf-8"))
    if len(lineage) != 1004 or len({row["doc_id"] for row in lineage}) != 1004:
        raise AssertionError("lineage cardinality/uniqueness mismatch")
    statuses = Counter(row["resolution"]["status"] for row in lineage)
    if dict(statuses) != dict(summary["resolution"]["status_counts"]):
        raise AssertionError("status summary mismatch")
    if len(ambiguous) != statuses["ambiguous"] or len(unresolved) != statuses["unresolved"]:
        raise AssertionError("exception sidecar counts differ")
    if len(chains) != summary["resolution"]["chain_count"]:
        raise AssertionError("chain count differs")
    if profile["correction_count"] != len(lineage):
        raise AssertionError("profile correction count differs")
    if not all(summary["quality_gates"].values()):
        raise AssertionError("published quality gate is false")
    if (root / "data" / "processed" / "parsed_disclosures.jsonl").stat().st_size != summary["corpus"]["parsed_size_bytes"]:
        raise AssertionError("Phase 2 content size changed after Phase 4")
    if (root / "data" / "processed" / "metadata_normalized.jsonl").stat().st_size != summary["corpus"]["metadata_size_bytes"]:
        raise AssertionError("Phase 3 metadata size changed after Phase 4")
    lineage_by_receipt = {row["rcept_no"]: row for row in lineage}
    lineage_by_doc_id = {row["doc_id"]: row for row in lineage}
    for row in lineage:
        status = row["resolution"]["status"]
        target = row["resolution"]["target_rcept_no"]
        if status.startswith("linked_") and not target:
            raise AssertionError(f"linked row without target: {row['doc_id']}")
        if status in {"ambiguous", "unresolved"} and target is not None:
            raise AssertionError(f"uncertain row has target: {row['doc_id']}")
        if row.get("is_correction") is not True:
            raise AssertionError(f"non-correction lineage row: {row['doc_id']}")
        evidence = row.get("correction_evidence", {}).get("evidence", [])
        if not evidence:
            raise AssertionError(f"correction row has no source evidence: {row['doc_id']}")
        for item in evidence:
            if not item.get("evidence_type") or not isinstance(item.get("locator"), dict):
                raise AssertionError(f"evidence locator missing: {row['doc_id']}")
    if {row["doc_id"] for row in ambiguous} != {
        row["doc_id"] for row in lineage if row["resolution"]["status"] == "ambiguous"
    }:
        raise AssertionError("ambiguous sidecar membership differs")
    if {row["doc_id"] for row in unresolved} != {
        row["doc_id"] for row in lineage if row["resolution"]["status"] == "unresolved"
    }:
        raise AssertionError("unresolved sidecar membership differs")
    chain_ids: set[str] = set()
    chain_member_receipts: set[str] = set()
    for chain in chains:
        chain_id = chain["chain_id"]
        if chain_id in chain_ids:
            raise AssertionError(f"duplicate chain_id: {chain_id}")
        chain_ids.add(chain_id)
        members = chain["members"]
        if chain["latest_rcept_no"] != members[-1]["rcept_no"]:
            raise AssertionError(f"chain latest mismatch: {chain_id}")
        if chain["is_complete"]:
            if chain["root_rcept_no"] is None:
                raise AssertionError(f"complete chain has no root: {chain_id}")
            if [item["sequence"] for item in members] != list(range(len(members))):
                raise AssertionError(f"complete chain sequence mismatch: {chain_id}")
        else:
            if chain["root_rcept_no"] is not None or any(item["sequence"] is not None for item in members):
                raise AssertionError(f"partial chain asserts root/sequence: {chain_id}")
        for member in members:
            if member["is_correction"]:
                chain_member_receipts.add(member["rcept_no"])
                lineage_row = lineage_by_receipt.get(member["rcept_no"])
                if lineage_row is None or lineage_row["lineage"]["chain_id"] != chain_id:
                    raise AssertionError(f"chain/lineage membership mismatch: {chain_id}")
    linked_receipts = {
        row["rcept_no"] for row in lineage if row["resolution"]["status"].startswith("linked_")
    }
    if not linked_receipts.issubset(chain_member_receipts):
        raise AssertionError("a linked correction is absent from chain output")
    if summary["execution"]["mode"] == "corpus-only" and external:
        raise AssertionError("corpus-only run produced external lookup rows")
    required = (
        root / "reports" / "correction_structure_profile.md",
        root / "reports" / "correction_structure_summary.json",
        root / "reports" / "correction_report.md",
        root / "docs" / "correction_schema.md",
    )
    if not all(path.is_file() for path in required):
        raise AssertionError("required Phase 4 report/schema is missing")
    return {
        "ready": True,
        "lineage_count": len(lineage),
        "chain_count": len(chains),
        "status_counts": dict(statuses),
        "ambiguous_count": len(ambiguous),
        "unresolved_count": len(unresolved),
        "external_lookup_count": len(external),
        "complete_chain_count": summary["resolution"]["complete_chain_count"],
        "partial_chain_count": summary["resolution"]["partial_chain_count"],
        "cycle_guard_count": summary["cycle_guard"]["detected_and_demoted_cycle_count"],
        "evidence_covered_count": sum(bool(row["correction_evidence"]["evidence"]) for row in lineage),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    try:
        result = validate(args.project_root)
    except (OSError, ValueError, AssertionError, json.JSONDecodeError) as exc:
        print(f"Phase 4 artifact validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
