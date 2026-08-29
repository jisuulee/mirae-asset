"""Run the pilot section keyword API against a reviewed JSONL benchmark."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from src.db.load_postgres import connect
from src.retrieval.sections import search_sections


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def run(args: argparse.Namespace) -> None:
    gold = read_jsonl(args.gold)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    connection_start = time.perf_counter()
    with connect(args.database_url) as connection:
        connection_ms = (time.perf_counter() - connection_start) * 1000
        output = []
        for index, item in enumerate(gold):
            params = {key: item[key] for key in ("company", "date_from", "date_to", "report_type", "section_title", "receipt_no") if item.get(key) is not None}
            measurements = []
            results = []
            repeats = args.warm_repeats if args.condition == "warm" else 1
            for _ in range(max(1, repeats)):
                started = time.perf_counter()
                results = search_sections(connection, item.get("query", ""), top_k=args.top_k, mode=args.mode, corpus_namespace=args.namespace, **params)
                measurements.append((time.perf_counter() - started) * 1000)
            output.append({
                "question_id": item["question_id"], "mode": args.mode, "condition": args.condition,
                "connection_ms": connection_ms if index == 0 else 0.0,
                "latency_ms": measurements[-1], "first_query_ms": measurements[0],
                "predicted_section_ids": [row["section_id"] for row in results],
                "predicted_receipt_nos": [row["receipt_no"] for row in results], "results": results,
            })
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in output:
            handle.write(json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("eval/periodic_keyword_questions.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--mode", choices=("fts", "trigram", "combined"), default="combined")
    parser.add_argument("--condition", choices=("cold", "warm"), default="cold")
    parser.add_argument("--warm-repeats", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--namespace", default="periodic-pilot-v1")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    run(args)


if __name__ == "__main__":
    main()
