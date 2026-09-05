"""Quality metrics and sample-gold comparisons for periodic parser exports."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean, median
from typing import Any

from .build_periodic_export import load_jsonl


def distribution(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"min": 0, "median": 0, "mean": 0, "max": 0}
    return {"min": min(values), "median": median(values), "mean": round(mean(values), 3), "max": max(values)}


def scan_tables(path: Path) -> tuple[set[str], int]:
    table_ids: set[str] = set()
    provenance_missing = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                table = json.loads(line)
                table_ids.add(table["table_id"])
                provenance_missing += not bool(table.get("provenance"))
    return table_ids, provenance_missing


def quality_metrics(documents: list[dict[str, Any]], sections: list[dict[str, Any]], table_ids: set[str], table_provenance_missing: int) -> dict[str, Any]:
    referenced = {tid for s in sections for tid in s.get("table_ids", [])}
    return {
        "documents": len(documents),
        "parse_success_rate": sum(d.get("parse_status") in {"success", "partial"} for d in documents) / len(documents) if documents else 0,
        "strict_success_rate": sum(d.get("parse_status") == "success" for d in documents) / len(documents) if documents else 0,
        "section_creation_rate": sum(d.get("section_count", 0) > 0 for d in documents) / len(documents) if documents else 0,
        "empty_section_ratio": sum(not (s.get("body_text") or "").strip() for s in sections) / len(sections) if sections else 0,
        "parent_path_success_rate": sum(bool(s.get("section_path")) and (s.get("level") in {None, 1} or bool(s.get("parent_section_key"))) for s in sections) / len(sections) if sections else 0,
        "provenance_missing_ratio": sum(not s.get("provenance") for s in sections) / len(sections) if sections else 0,
        "table_provenance_missing_ratio": table_provenance_missing / len(table_ids) if table_ids else 0,
        "orphan_table_ratio": len(table_ids - referenced) / len(table_ids) if table_ids else 0,
        "source_sha_present_rate": sum(bool(d.get("source_sha256")) for d in documents) / len(documents) if documents else 0,
        "parser_exceptions": 0,
        "sections_per_document": distribution([d.get("section_count", 0) for d in documents]),
        "tables_per_document": distribution([d.get("table_count", 0) for d in documents]),
        "warning_code_counts": Counter(code for d in documents for code in d.get("warning_codes", [])),
    }


def _canonical_main(record: dict[str, Any]) -> dict[str, Any]:
    return (record.get("main_document") or {}).get("content") or {}


def _tokens(value: str) -> set[str]:
    return {token for token in value.split() if not token.startswith("[TABLE:")}


def compare_samples(documents: list[dict[str, Any]], sections: list[dict[str, Any]], sample_path: Path) -> list[dict[str, Any]]:
    canonical = {str(row["rcept_no"]): row for row in load_jsonl(sample_path) if row.get("doc_group") == "periodic"}
    sections_by_receipt = {receipt: [section for section in sections if section["receipt_no"] == receipt] for receipt in canonical}
    comparisons = []
    for document in documents:
        receipt = document["receipt_no"]
        if receipt not in canonical:
            continue
        gold = canonical[receipt]
        main = _canonical_main(gold)
        gold_blocks = main.get("block_sequence", [])
        gold_sections = main.get("sections", [])
        gold_tables = main.get("tables", [])
        ours_titles = document.get("section_titles", [])
        gold_titles = [s.get("title") or s.get("raw_title") for s in gold_sections]
        ours_sections = sections_by_receipt.get(receipt, [])
        ours_boundaries = [(s.get("start_block_index"), s.get("end_block_index")) for s in ours_sections]
        gold_boundaries = [(s.get("start_block_index"), s.get("end_block_index")) for s in gold_sections]
        boundary_pairs = list(zip(ours_boundaries, gold_boundaries))
        ours_body_tokens = _tokens(" ".join(s.get("body_text") or "" for s in ours_sections))
        gold_body_tokens = _tokens(main.get("body_text") or "")
        ours_types = document.get("block_types", [])
        gold_types = [b.get("block_type") for b in gold_blocks]
        gold_hashes = {s.get("sha256") for s in gold.get("source_files", []) if s.get("sha256")}
        comparisons.append({
            "receipt_no": receipt,
            "strict": {
                "source_sha_match": document.get("source_sha256") in gold_hashes,
                "provenance_complete": document.get("block_provenance_complete", False),
            },
            "heuristic": {
                "our_block_count": len(ours_types), "canonical_block_count": len(gold_types),
                "block_type_sequence_ratio": round(SequenceMatcher(None, ours_types, gold_types, autojunk=False).ratio(), 4),
                "our_section_count": len(ours_titles), "canonical_section_count": len(gold_titles),
                "section_title_sequence_ratio": round(SequenceMatcher(None, ours_titles, gold_titles, autojunk=False).ratio(), 4),
                "our_table_count": document.get("table_count", 0), "canonical_table_count": len(gold_tables),
                "boundary_pair_exact_ratio": round(sum(a == b for a, b in boundary_pairs) / len(boundary_pairs), 4) if boundary_pairs else None,
                "body_token_jaccard": round(len(ours_body_tokens & gold_body_tokens) / len(ours_body_tokens | gold_body_tokens), 4) if ours_body_tokens or gold_body_tokens else 1.0,
                "our_body_chars": document.get("body_chars", 0),
                "canonical_body_chars": len(main.get("body_text") or ""),
            },
        })
    return comparisons


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--sample", type=Path, default=Path("role2_final/data/sample_disclosures.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    documents = load_jsonl(args.export_dir / "documents.jsonl")
    sections = load_jsonl(args.export_dir / "sections.jsonl")
    table_ids, table_provenance_missing = scan_tables(args.export_dir / "tables.jsonl")
    build_summary = json.loads((args.export_dir / "build_summary.json").read_text(encoding="utf-8"))
    metrics = quality_metrics(documents, sections, table_ids, table_provenance_missing)
    metrics["parser_exceptions"] = len(build_summary.get("exceptions", []))
    result = {"quality_metrics": metrics, "sample_comparisons": compare_samples(documents, sections, args.sample)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=dict), encoding="utf-8")


if __name__ == "__main__":
    main()
