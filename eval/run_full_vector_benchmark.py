"""Run independent section-level Vector Retrieval over the full benchmark."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import read_jsonl, validate_benchmark
from src.db.load_postgres import connect
from src.retrieval.embeddings import ClovaEmbeddingV2
from src.retrieval.vector_sections import search_vector_sections


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def run(args: argparse.Namespace) -> dict[str, int]:
    benchmark = read_jsonl(args.gold)
    validate_benchmark(benchmark)
    provider = ClovaEmbeddingV2(args.api_key)
    output: list[dict[str, Any]] = []
    skipped = 0
    with connect(args.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for item in benchmark:
            if item["retrieval_target"] == "table":
                output.append({"question_id": item["question_id"], "mode": "vector",
                               "condition": args.condition, "latency_ms": None,
                               "section_results": [], "table_results": [],
                               "error": "excluded_table_only_target"})
                skipped += 1
                continue
            measurements: list[float] = []
            results: list[dict[str, Any]] = []
            error = None
            repeats = args.warm_repeats if args.condition == "warm" else 1
            try:
                for _ in range(max(1, repeats)):
                    started = time.perf_counter()
                    results = search_vector_sections(
                        connection, item["question"], provider, top_k=args.top_k,
                        company=item.get("company") if item.get("company_filter_required") else None,
                        date_from=_date(item.get("date_from")), date_to=_date(item.get("date_to")),
                        report_type=item.get("report_type_filter"), receipt_no=item.get("receipt_no_filter"),
                        corpus_namespace=item["loader_namespace"],
                    )
                    measurements.append((time.perf_counter() - started) * 1000)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            output.append({"question_id": item["question_id"], "mode": "vector",
                           "condition": args.condition, "latency_ms": measurements[-1] if measurements else None,
                           "latency_samples_ms": measurements, "section_results": results,
                           "table_results": [], "error": error})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in output:
            handle.write(json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")
    return {"questions": len(benchmark), "excluded_table_only": skipped}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--api-key", default=os.getenv("CLOVASTUDIO_API_KEY"))
    parser.add_argument("--condition", choices=("cold", "warm"), default="cold")
    parser.add_argument("--warm-repeats", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    if not args.database_url or not args.api_key:
        parser.error("DATABASE_URL and CLOVASTUDIO_API_KEY are required")
    print(json.dumps(run(args), indent=2))


if __name__ == "__main__":
    main()
