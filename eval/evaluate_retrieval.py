"""Score ranked receipt_no predictions against a retrieval JSONL benchmark.

Prediction rows accept the current `question_id`/`predicted_receipt_nos` names and
the legacy `id`/`ranked_receipt_nos` names.
This separation keeps benchmark scoring independent from a DB driver/API runner.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    # utf-8-sig accepts both plain UTF-8 and Windows tools' optional BOM.
    with path.open(encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
    return rows


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile, suitable for a small transparent V1 report."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def evaluate(gold: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    predicted = {row.get("question_id") or row["id"]: row for row in predictions}
    reciprocal_ranks: list[float] = []
    hits = {1: 0, 3: 0, 5: 0}
    latencies: list[float] = []
    missing: list[str] = []
    failures: list[dict[str, Any]] = []
    by_type_rows: dict[str, list[dict[str, Any]]] = {}
    for item in gold:
        question_id = item.get("question_id") or item["id"]
        result = predicted.get(question_id)
        if result is None:
            ranked, latency = [], None
            missing.append(question_id)
        else:
            ranked = result.get("predicted_receipt_nos", result.get("ranked_receipt_nos", []))
            latency = result.get("latency_ms")
        expected = set(item.get("expected_receipt_nos") or [item["expected_receipt_no"]])
        rank = next((index for index, value in enumerate(ranked, 1) if value in expected), None)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        for k in hits:
            hits[k] += int(any(value in expected for value in ranked[:k]))
        if latency is not None:
            latencies.append(float(latency))
        row_score = {
            "question_id": question_id,
            "query_type": item.get("query_type") or item.get("type") or "unknown",
            "expected_receipt_nos": sorted(expected),
            "predicted_receipt_nos": ranked,
            "rank": rank,
            "latency_ms": latency,
            "error": result.get("error") if result else "missing prediction",
        }
        by_type_rows.setdefault(row_score["query_type"], []).append(row_score)
        if rank is None:
            failures.append(row_score)
    count = len(gold)
    if not count:
        raise ValueError("benchmark is empty")
    def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        row_latencies = [float(row["latency_ms"]) for row in rows if row["latency_ms"] is not None]
        return {
            "question_count": len(rows),
            "top_1_accuracy": sum(row["rank"] == 1 for row in rows) / len(rows),
            "recall_at_3": sum(row["rank"] is not None and row["rank"] <= 3 for row in rows) / len(rows),
            "recall_at_5": sum(row["rank"] is not None and row["rank"] <= 5 for row in rows) / len(rows),
            "mrr": sum(1 / row["rank"] if row["rank"] else 0 for row in rows) / len(rows),
            "average_latency_ms": statistics.fmean(row_latencies) if row_latencies else None,
        }

    return {
        "question_count": count,
        "prediction_count": len(predictions),
        "top_1_accuracy": hits[1] / count,
        "recall_at_3": hits[3] / count,
        "recall_at_5": hits[5] / count,
        "mrr": sum(reciprocal_ranks) / count,
        "average_latency_ms": statistics.fmean(latencies) if latencies else None,
        "p50_latency_ms": percentile(latencies, 0.50) if latencies else None,
        "p95_latency_ms": percentile(latencies, 0.95) if latencies else None,
        "max_latency_ms": max(latencies) if latencies else None,
        "latency_sample_count": len(latencies),
        "missing_prediction_ids": missing,
        "by_query_type": {name: aggregate(rows) for name, rows in sorted(by_type_rows.items())},
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path(__file__).with_name("retrieval_questions.jsonl"))
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(read_jsonl(args.gold), read_jsonl(args.predictions))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
