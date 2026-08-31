"""Execute the curated contract benchmark against PostgreSQL Retrieval V0."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

from db.load_postgres import connect
from build_contract_benchmark import execute
from evaluate_retrieval import evaluate, read_jsonl


def timed_connection(database_url: str):
    started = time.perf_counter()
    connection = connect(database_url)
    return connection, (time.perf_counter() - started) * 1000


def run_pass(connection, questions, repetitions=1):
    rows = []
    for item in questions:
        samples = []
        results = []
        error = None
        try:
            for _ in range(repetitions):
                started = time.perf_counter()
                results = execute(connection, item["query"])
                samples.append((time.perf_counter() - started) * 1000)
        except Exception as exc:  # Keep benchmark accounting complete.
            error = f"{type(exc).__name__}: {exc}"
        rows.append({
            "question_id": item["question_id"],
            "query_type": item["query_type"],
            "predicted_receipt_nos": [row["receipt_no"] for row in results[:5]],
            "latency_ms": statistics.fmean(samples) if samples else None,
            "latency_samples_ms": samples,
            "error": error,
        })
    return rows


def write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--gold", type=Path, default=Path(__file__).with_name("retrieval_questions.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--warm-repetitions", type=int, default=5)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    questions = read_jsonl(args.gold)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    connection, cold_connection_ms = timed_connection(args.database_url)
    try:
        cold = run_pass(connection, questions, repetitions=1)
        # One unmeasured full pass establishes the repeat-query warm condition.
        run_pass(connection, questions, repetitions=1)
        warm = run_pass(connection, questions, repetitions=args.warm_repetitions)
    finally:
        connection.close()

    cold_path = args.output_dir / "retrieval_predictions_cold.jsonl"
    warm_path = args.output_dir / "retrieval_predictions_warm.jsonl"
    write_jsonl(cold_path, cold)
    write_jsonl(warm_path, warm)
    baseline = {
        "benchmark": str(args.gold),
        "cold_definition": "first ordered pass after container restart and new DB connection; OS/Docker cache cannot be fully controlled",
        "warm_definition": f"one unmeasured full pass, then mean of {args.warm_repetitions} repetitions per query",
        "connection_latency_ms": cold_connection_ms,
        "cold": evaluate(questions, cold),
        "warm": evaluate(questions, warm),
        "prediction_files": {"cold": str(cold_path), "warm": str(warm_path)},
    }
    output = args.output_dir / "retrieval_sql_baseline.json"
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(baseline, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
