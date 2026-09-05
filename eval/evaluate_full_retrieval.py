"""Evaluate the versioned full-corpus periodic Retrieval benchmark."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

K_VALUES = (1, 3, 5, 10)
REQUIRED_FIELDS = {
    "question_id", "benchmark_version", "category", "question", "difficulty",
    "retrieval_target", "company_filter_required", "year_filter_required",
    "gold_receipt_nos", "gold_section_ids", "gold_table_ids",
    "gold_source_locators", "gold_type", "gold_provenance",
    "human_review_status", "loader_namespace", "corpus_snapshot", "split",
}
VALID_TARGETS = {"section", "table", "either", "pdf_page"}
VALID_REVIEW = {"reviewed", "needs_review", "ambiguous"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def validate_benchmark(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("benchmark is empty")
    ids: set[str] = set()
    for row in rows:
        missing = REQUIRED_FIELDS - row.keys()
        if missing:
            raise ValueError(f"{row.get('question_id', '<unknown>')}: missing {sorted(missing)}")
        qid = row["question_id"]
        if qid in ids:
            raise ValueError(f"duplicate question_id: {qid}")
        ids.add(qid)
        if row["retrieval_target"] not in VALID_TARGETS:
            raise ValueError(f"{qid}: invalid retrieval_target")
        if row["human_review_status"] not in VALID_REVIEW:
            raise ValueError(f"{qid}: invalid human_review_status")
        if row["difficulty"] not in {"easy", "medium", "hard"}:
            raise ValueError(f"{qid}: invalid difficulty")
        if not row["gold_receipt_nos"]:
            raise ValueError(f"{qid}: missing receipt gold")
        if row["retrieval_target"] in {"section", "pdf_page"} and not row["gold_section_ids"]:
            raise ValueError(f"{qid}: missing section/page gold")
        if row["retrieval_target"] == "table" and not row["gold_table_ids"]:
            raise ValueError(f"{qid}: missing table gold")
        if not row["gold_provenance"]:
            raise ValueError(f"{qid}: missing stable gold provenance")
        for gold in row["gold_provenance"]:
            stable = {"receipt_no", "source_locator", "section_order", "section_key"}
            if not stable.issubset(gold):
                raise ValueError(f"{qid}: incomplete stable provenance")
        if row["retrieval_target"] == "pdf_page":
            if row.get("source_type") != "pdf":
                raise ValueError(f"{qid}: PDF target without PDF source_type")
            if any(not str(locator).startswith("page:") for locator in row["gold_source_locators"]):
                raise ValueError(f"{qid}: PDF gold must use page locators")


def deduplicate_receipts(results: Iterable[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ranked: list[str] = []
    for result in results:
        receipt = str(result.get("receipt_no") or "")
        if receipt and receipt not in seen:
            seen.add(receipt)
            ranked.append(receipt)
    return ranked


def _rank(predicted: list[Any], expected: set[Any]) -> int | None:
    return next((index for index, value in enumerate(predicted, 1) if value in expected), None)


def _coverage(predicted: list[Any], expected: set[Any], k: int = 10) -> float:
    return len(expected.intersection(predicted[:k])) / len(expected) if expected else 0.0


def _average_precision(predicted: list[Any], expected: set[Any], k: int = 10) -> float:
    if not expected:
        return 0.0
    hits = 0
    total = 0.0
    seen: set[Any] = set()
    for rank, value in enumerate(predicted[:k], 1):
        if value in expected and value not in seen:
            seen.add(value)
            hits += 1
            total += hits / rank
    return total / min(len(expected), k)


def _ndcg(predicted: list[Any], expected: set[Any], k: int = 10) -> float:
    dcg = sum(1.0 / math.log2(rank + 1) for rank, value in enumerate(predicted[:k], 1) if value in expected)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(expected), k) + 1))
    return dcg / ideal if ideal else 0.0


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)]


def _level_metrics(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    eligible = [row for row in rows if row[f"{prefix}_eligible"]]
    ranks = [row[f"{prefix}_rank"] for row in eligible]
    result: dict[str, Any] = {"question_count": len(eligible)}
    for k in K_VALUES:
        result["top1" if k == 1 else f"recall_at_{k}"] = (
            sum(rank is not None and rank <= k for rank in ranks) / len(eligible) if eligible else None
        )
    result["mrr"] = statistics.fmean(1 / rank if rank else 0 for rank in ranks) if eligible else None
    result["relevant_set_coverage_at_10"] = (
        statistics.fmean(row[f"{prefix}_coverage_at_10"] for row in eligible) if eligible else None
    )
    result["map_at_10"] = statistics.fmean(row[f"{prefix}_ap_at_10"] for row in eligible) if eligible else None
    result["ndcg_at_10"] = statistics.fmean(row[f"{prefix}_ndcg_at_10"] for row in eligible) if eligible else None
    return result


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    return {
        "question_count": len(rows),
        "section": _level_metrics(rows, "section"),
        "receipt": _level_metrics(rows, "receipt"),
        "table": _level_metrics(rows, "table"),
        "latency": {
            "sample_count": len(latencies),
            "average_ms": statistics.fmean(latencies) if latencies else None,
            "p50_ms": _percentile(latencies, 0.5),
            "p95_ms": _percentile(latencies, 0.95),
            "max_ms": max(latencies) if latencies else None,
        },
    }


def _failure_category(item: dict[str, Any], scored: dict[str, Any]) -> str:
    if item["human_review_status"] == "ambiguous":
        return "gold_ambiguity"
    if item["retrieval_target"] == "table":
        return "table_target"
    if item["retrieval_target"] == "pdf_page":
        return "pdf"
    if item["category"] == "spacing_or_expression_variation":
        return "korean_spacing"
    if item["category"] == "ambiguous_similar_sections":
        return "ambiguous_section"
    if item["category"] in {"narrative_description", "difficult_or_ambiguous"}:
        return "semantic_paraphrase"
    if scored["receipt_rank"] is not None:
        return "ranking"
    if item.get("year_filter_required") or item.get("report_type_filter"):
        return "metadata_filter"
    if item["category"] == "partial_korean_phrase":
        return "tokenizer"
    return "lexical_mismatch"


def evaluate(gold: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    validate_benchmark(gold)
    by_id = {row["question_id"]: row for row in predictions}
    scored: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for item in gold:
        prediction = by_id.get(item["question_id"], {})
        section_results = prediction.get("section_results", prediction.get("results", []))
        table_results = prediction.get("table_results", [])
        section_ids = [row.get("section_id") for row in section_results if row.get("section_id") is not None]
        table_ids = [row.get("table_id") for row in table_results if row.get("table_id") is not None]
        target_results = table_results if item["retrieval_target"] == "table" else section_results
        receipts = deduplicate_receipts(target_results)
        expected_sections = set(item["gold_section_ids"])
        expected_tables = set(item["gold_table_ids"])
        expected_receipts = set(item["gold_receipt_nos"])
        excluded = prediction.get("error") == "excluded_table_only_target"
        section_eligible = not excluded and item["retrieval_target"] in {"section", "pdf_page", "either"} and bool(expected_sections)
        table_eligible = not excluded and item["retrieval_target"] in {"table", "either"} and bool(expected_tables)
        row = {
            "question_id": item["question_id"],
            "category": item["category"],
            "difficulty": item["difficulty"],
            "retrieval_target": item["retrieval_target"],
            "report_type": item.get("report_type_filter") or "none",
            "source_type": item.get("source_type") or "xml",
            "human_review_status": item["human_review_status"],
            "split": item["split"],
            "latency_ms": prediction.get("latency_ms"),
            "error": prediction.get("error") or ("missing_prediction" if not prediction else None),
            "section_eligible": section_eligible,
            "section_rank": _rank(section_ids, expected_sections) if section_eligible else None,
            "section_coverage_at_10": _coverage(section_ids, expected_sections),
            "section_ap_at_10": _average_precision(section_ids, expected_sections),
            "section_ndcg_at_10": _ndcg(section_ids, expected_sections),
            "table_eligible": table_eligible,
            "table_rank": _rank(table_ids, expected_tables) if table_eligible else None,
            "table_coverage_at_10": _coverage(table_ids, expected_tables),
            "table_ap_at_10": _average_precision(table_ids, expected_tables),
            "table_ndcg_at_10": _ndcg(table_ids, expected_tables),
            "receipt_eligible": not excluded and bool(expected_receipts),
            "receipt_rank": _rank(receipts, expected_receipts) if not excluded else None,
            "receipt_coverage_at_10": _coverage(receipts, expected_receipts),
            "receipt_ap_at_10": _average_precision(receipts, expected_receipts),
            "receipt_ndcg_at_10": _ndcg(receipts, expected_receipts),
            "predicted_receipt_nos": receipts,
            "section_results": section_results,
            "table_results": table_results,
        }
        scored.append(row)
        primary_rank = row["table_rank"] if item["retrieval_target"] == "table" else row["section_rank"]
        if primary_rank is None and not excluded:
            failures.append({
                **row,
                "expected_receipt_nos": item["gold_receipt_nos"],
                "expected_section_ids": item["gold_section_ids"],
                "expected_table_ids": item["gold_table_ids"],
                "failure_category": _failure_category(item, row),
                "notes": item.get("review_notes", ""),
            })

    def groups(field: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in scored:
            grouped[str(row[field])].append(row)
        return {key: _aggregate(value) for key, value in sorted(grouped.items())}

    return {
        "benchmark_version": gold[0]["benchmark_version"],
        "question_count": len(gold),
        "prediction_count": len(predictions),
        "overall": _aggregate(scored),
        "reviewed_only": _aggregate([row for row in scored if row["human_review_status"] == "reviewed"]),
        "by_category": groups("category"),
        "by_difficulty": groups("difficulty"),
        "by_retrieval_target": groups("retrieval_target"),
        "by_report_type": groups("report_type"),
        "by_source_type": groups("source_type"),
        "by_review_status": groups("human_review_status"),
        "by_split": groups("split"),
        "failure_taxonomy": dict(sorted(__import__("collections").Counter(row["failure_category"] for row in failures).items())),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--failures-output", type=Path)
    args = parser.parse_args()
    report = evaluate(read_jsonl(args.gold), read_jsonl(args.predictions))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    if args.failures_output:
        args.failures_output.parent.mkdir(parents=True, exist_ok=True)
        args.failures_output.write_text(json.dumps(report["failures"], ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "failures"}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
