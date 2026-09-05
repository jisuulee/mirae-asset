"""Evaluate section and receipt recall for one keyword retrieval mode."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)]


def evaluate(gold: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {row["question_id"]: row for row in predictions}
    scored, failures = [], []
    for item in gold:
        result = by_id.get(item["question_id"], {})
        section_ids, receipts = result.get("predicted_section_ids", []), result.get("predicted_receipt_nos", [])
        expected_sections, expected_receipts = set(item.get("expected_section_ids", [])), set(item.get("expected_receipt_nos", []))
        section_rank = next((i for i, value in enumerate(section_ids, 1) if value in expected_sections), None)
        receipt_rank = next((i for i, value in enumerate(receipts, 1) if value in expected_receipts), None)
        row = {"question_id": item["question_id"], "query_type": item["query_type"], "expected_section_ids": sorted(expected_sections), "expected_receipt_nos": sorted(expected_receipts), "section_rank": section_rank, "receipt_rank": receipt_rank, "latency_ms": result.get("latency_ms"), "results": result.get("results", [])}
        scored.append(row)
        if section_rank is None:
            if not row["results"]:
                category = "no_result_tokenizer_or_filter"
            elif row["receipt_rank"] is not None and item["query_type"] == "pdf_narrative":
                category = "pdf_untitled_page_granularity"
            elif row["receipt_rank"] is not None and item["query_type"].startswith("unicode_spacing"):
                category = "spacing_normalization_or_ranking"
            elif row["receipt_rank"] is not None and item["query_type"] == "year_report_filtered":
                category = "metadata_filter_or_ranking"
            elif row["receipt_rank"] is not None:
                category = "section_ambiguity_or_ranking"
            else:
                category = "tokenizer_or_text_match"
            failures.append({**row, "failure_reason": "gold section not in top-k", "failure_category": category})

    def hit(rows: list[dict[str, Any]], key: str, k: int) -> float:
        return sum(row[key] is not None and row[key] <= k for row in rows) / len(rows) if rows else 0.0
    def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"question_count": len(rows), "top1_section": hit(rows,"section_rank",1), "recall_at_3_section": hit(rows,"section_rank",3), "recall_at_5_section": hit(rows,"section_rank",5), "recall_at_10_section": hit(rows,"section_rank",10), "mrr_section": statistics.fmean(1/r["section_rank"] if r["section_rank"] else 0 for r in rows) if rows else 0.0, "recall_at_5_receipt": hit(rows,"receipt_rank",5)}
    latencies = [float(row["latency_ms"]) for row in scored if row.get("latency_ms") is not None]
    grouped = defaultdict(list)
    for row in scored:
        grouped[row["query_type"]].append(row)
    return {"question_count": len(scored), "prediction_count": len(predictions), "top1_section": hit(scored,"section_rank",1), "recall_at_3_section": hit(scored,"section_rank",3), "recall_at_5_section": hit(scored,"section_rank",5), "recall_at_10_section": hit(scored,"section_rank",10), "mrr_section": statistics.fmean(1/r["section_rank"] if r["section_rank"] else 0 for r in scored) if scored else 0.0, "top1_receipt": hit(scored,"receipt_rank",1), "recall_at_5_receipt": hit(scored,"receipt_rank",5), "average_latency_ms": statistics.fmean(latencies) if latencies else None, "p50_latency_ms": percentile(latencies,.5), "p95_latency_ms": percentile(latencies,.95), "max_latency_ms": max(latencies) if latencies else None, "by_query_type": {key: aggregate(value) for key,value in sorted(grouped.items())}, "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("eval/periodic_keyword_questions.jsonl"))
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--failures-output", type=Path)
    args = parser.parse_args()
    report = evaluate(read_jsonl(args.gold), read_jsonl(args.predictions))
    if args.failures_output:
        args.failures_output.parent.mkdir(parents=True, exist_ok=True)
        args.failures_output.write_text(json.dumps(report["failures"], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
