"""Evaluate Vector-only predictions and compare recovery against FTS misses."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import evaluate, read_jsonl


def vector_failure_category(failure: dict[str, Any]) -> str:
    if failure["human_review_status"] == "ambiguous":
        return "gold_ambiguity"
    if failure["retrieval_target"] == "pdf_page":
        return "pdf"
    if failure.get("error"):
        return "insufficient_context"
    if failure["difficulty"] == "hard":
        return "semantic_mismatch"
    if failure.get("predicted_receipt_nos") and set(failure["predicted_receipt_nos"]).intersection(failure["expected_receipt_nos"]):
        return "same_company_similar_section"
    if failure["category"] == "year_report_filtered":
        return "metadata_filter"
    return "overgeneralization"


def evaluate_vector(
    gold: list[dict[str, Any]], predictions: list[dict[str, Any]], fts_failures: list[dict[str, Any]],
    fts_predictions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report = evaluate(gold, predictions)
    for failure in report["failures"]:
        failure["failure_category"] = vector_failure_category(failure)
    report["failure_taxonomy"] = dict(sorted(Counter(
        failure["failure_category"] for failure in report["failures"]
    ).items()))
    vector_misses = {failure["question_id"] for failure in report["failures"]}
    prediction_by_id = {row["question_id"]: row for row in predictions}

    def recovery(category: str | None = None, difficulty: str | None = None) -> dict[str, Any]:
        eligible = [failure for failure in fts_failures
                    if (category is None or failure.get("failure_category") == category)
                    and (difficulty is None or failure.get("difficulty") == difficulty)
                    and prediction_by_id.get(failure["question_id"], {}).get("error") != "excluded_table_only_target"]
        recovered = [failure["question_id"] for failure in eligible if failure["question_id"] not in vector_misses]
        return {"fts_failure_count": len(eligible), "vector_recovered_count": len(recovered),
                "recovery_rate": len(recovered) / len(eligible) if eligible else None,
                "question_ids": recovered}

    categories = sorted({failure.get("failure_category") for failure in fts_failures if failure.get("failure_category")})
    report["fts_recovery"] = {category: recovery(category=category) for category in categories}
    report["fts_recovery"]["hard"] = recovery(difficulty="hard")

    if fts_predictions is not None:
        gold_by_id = {row["question_id"]: row for row in gold}
        fts_by_id = {row["question_id"]: row for row in fts_predictions}
        vector_by_id = {row["question_id"]: row for row in predictions}

        def top1_correct(prediction: dict[str, Any], expected: set[int]) -> bool:
            results = prediction.get("section_results", prediction.get("results", []))
            return bool(results and results[0].get("section_id") in expected)

        four_way_rows = []
        for qid, item in gold_by_id.items():
            if item["retrieval_target"] == "table" or not item["gold_section_ids"]:
                continue
            expected = set(item["gold_section_ids"])
            fts_ok = top1_correct(fts_by_id.get(qid, {}), expected)
            vector_ok = top1_correct(vector_by_id.get(qid, {}), expected)
            label = ("both_correct" if fts_ok and vector_ok else
                     "fts_only_correct" if fts_ok else
                     "vector_only_correct" if vector_ok else "both_wrong")
            four_way_rows.append({
                "question_id": qid, "category": item["category"], "difficulty": item["difficulty"],
                "human_review_status": item["human_review_status"], "split": item["split"],
                "source_type": item.get("source_type", "xml"), "classification": label,
            })

        def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
            counts = Counter(row["classification"] for row in rows)
            return {
                "question_count": len(rows),
                "counts": {name: counts.get(name, 0) for name in
                           ("both_correct", "fts_only_correct", "vector_only_correct", "both_wrong")},
                "questions": rows,
            }

        report["top1_four_way"] = {
            "all_candidate_section_eligible": summarize(four_way_rows),
            "reviewed_only": summarize([row for row in four_way_rows if row["human_review_status"] == "reviewed"]),
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--fts-failures", type=Path, default=Path("reports/periodic_full_keyword_failures.json"))
    parser.add_argument("--fts-predictions", type=Path, default=Path("artifacts/periodic_full_keyword_fts.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("reports/periodic_full_vector_baseline.json"))
    parser.add_argument("--failures-output", type=Path, default=Path("reports/periodic_full_vector_failures.json"))
    args = parser.parse_args()
    fts = json.loads(args.fts_failures.read_text(encoding="utf-8"))
    fts_predictions = read_jsonl(args.fts_predictions) if args.fts_predictions.exists() else None
    report = evaluate_vector(read_jsonl(args.gold), read_jsonl(args.predictions), fts, fts_predictions)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    args.failures_output.write_text(json.dumps(report["failures"], ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
