"""Validate snapshot-local gold IDs against stable corpus provenance."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import read_jsonl, validate_benchmark
from src.db.load_postgres import connect


def validate_database(connection: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    validate_benchmark(rows)
    mismatches: list[dict[str, str]] = []
    checked_sections = checked_tables = 0
    for item in rows:
        for gold in item["gold_provenance"]:
            section_id = gold.get("section_id")
            if section_id is None:
                mismatches.append({"question_id": item["question_id"], "reason": "missing section_id"})
                continue
            actual = connection.execute("""
                SELECT s.receipt_no,s.source_locator,s.section_order,
                       s.metadata->>'section_key',f.file_id,f.sha256
                FROM document_sections s JOIN disclosure_files f ON f.file_id=s.file_id
                WHERE s.section_id=%s AND s.metadata->>'loader_namespace'=%s
            """, (section_id, item["loader_namespace"])).fetchone()
            checked_sections += 1
            expected = (str(gold["receipt_no"]), str(gold.get("section_source_locator") or gold["source_locator"]), int(gold["section_order"]),
                        str(gold["section_key"]), int(gold["file_id"]), str(gold.get("source_sha256") or ""))
            normalized = None if actual is None else (str(actual[0]), str(actual[1]), int(actual[2]),
                                                       str(actual[3]), int(actual[4]), str(actual[5] or ""))
            if normalized != expected:
                mismatches.append({"question_id": item["question_id"], "reason": "section provenance mismatch"})
            table_id = gold.get("table_id")
            if table_id:
                table = connection.execute("""
                    SELECT receipt_no,section_id,source_locator,archive_identifier
                    FROM retrieval_tables WHERE table_id=%s AND metadata->>'loader_namespace'=%s
                """, (table_id, item["loader_namespace"])).fetchone()
                checked_tables += 1
                expected_table = (str(gold["receipt_no"]), int(section_id), str(gold["source_locator"]),
                                  str(gold.get("table_archive_identifier") or ""))
                normalized_table = None if table is None else (str(table[0]), int(table[1]), str(table[2]), str(table[3] or ""))
                if normalized_table != expected_table:
                    mismatches.append({"question_id": item["question_id"], "reason": "table provenance mismatch"})
    return {"questions": len(rows), "sections_checked": checked_sections,
            "tables_checked": checked_tables, "mismatch_count": len(mismatches), "mismatches": mismatches}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    with connect(args.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        result = validate_database(connection, read_jsonl(args.benchmark))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["mismatch_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()