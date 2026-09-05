"""Run Keyword Retrieval modes over the versioned full-corpus benchmark."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from datetime import date
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import read_jsonl, validate_benchmark
from src.db.load_postgres import connect
from src.retrieval.sections import search_sections
from src.retrieval.tables import search_tables


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _section_params(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "company": item.get("company") if item.get("company_filter_required") else None,
            "date_from": _date(item.get("date_from")),
            "date_to": _date(item.get("date_to")),
            "report_type": item.get("report_type_filter"),
            "receipt_no": item.get("receipt_no_filter"),
        }.items()
        if value is not None
    }


def run(args: argparse.Namespace) -> None:
    benchmark = read_jsonl(args.gold)
    validate_benchmark(benchmark)
    output: list[dict[str, Any]] = []
    with connect(args.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for item in benchmark:
            measurements: list[float] = []
            section_results: list[dict[str, Any]] = []
            table_results: list[dict[str, Any]] = []
            error = None
            repeats = args.warm_repeats if args.condition == "warm" else 1
            try:
                for _ in range(max(1, repeats)):
                    started = time.perf_counter()
                    target = item["retrieval_target"]
                    if target in {"section", "pdf_page", "either"}:
                        section_results = search_sections(
                            connection,
                            item["question"],
                            top_k=args.top_k,
                            mode=args.mode,
                            corpus_namespace=item["loader_namespace"],
                            **_section_params(item),
                        )
                    if target in {"table", "either"}:
                        table_results = search_tables(
                            connection,
                            item["question"],
                            top_k=args.top_k,
                            mode=args.mode,
                            corpus_namespace=item["loader_namespace"],
                        )
                    measurements.append((time.perf_counter() - started) * 1000)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            output.append({
                "question_id": item["question_id"],
                "mode": args.mode,
                "condition": args.condition,
                "latency_ms": measurements[-1] if measurements else None,
                "latency_samples_ms": measurements,
                "section_results": section_results,
                "table_results": table_results,
                "error": error,
            })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in output:
            handle.write(json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--mode", choices=("fts", "trigram", "combined"), required=True)
    parser.add_argument("--condition", choices=("cold", "warm"), default="cold")
    parser.add_argument("--warm-repeats", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    run(args)


if __name__ == "__main__":
    main()
