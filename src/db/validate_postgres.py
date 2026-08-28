"""Automated integrity and quality validation for PostgreSQL Retrieval V1."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from load_postgres import connect


def _scalar(connection: Any, sql: str, params: tuple = ()) -> Any:
    return connection.execute(sql, params).fetchone()[0]


def validate(database_url: str) -> dict[str, Any]:
    with connect(database_url) as connection:
        counts = {t: _scalar(connection, f"SELECT count(*) FROM {t}") for t in
                  ("companies", "disclosures", "disclosure_files", "contracts", "correction_links")}
        fields = {}
        for field in ("contract_amount", "counterparty", "contract_start_date", "contract_end_date", "sales_ratio"):
            normalized_null = _scalar(connection, f"SELECT count(*) FROM contracts WHERE {field} IS NULL")
            raw_null = _scalar(connection, f"SELECT count(*) FROM contracts WHERE {field}_raw IS NULL")
            fields[field] = {
                "normalized_null_count": normalized_null,
                "normalized_null_rate": round(normalized_null / counts["contracts"], 6),
                "raw_null_count": raw_null,
                "raw_null_rate": round(raw_null / counts["contracts"], 6),
            }
        duplicate_receipts = _scalar(connection, "SELECT count(*) FROM (SELECT receipt_no FROM disclosures GROUP BY receipt_no HAVING count(*)>1) x")
        orphan_checks = {
            "disclosures_company": _scalar(connection, "SELECT count(*) FROM disclosures d LEFT JOIN companies c USING(company_id) WHERE c.company_id IS NULL"),
            "files_disclosure": _scalar(connection, "SELECT count(*) FROM disclosure_files f LEFT JOIN disclosures d USING(receipt_no) WHERE d.receipt_no IS NULL"),
            "contracts_disclosure": _scalar(connection, "SELECT count(*) FROM contracts c LEFT JOIN disclosures d USING(receipt_no) WHERE d.receipt_no IS NULL"),
            "contracts_file": _scalar(connection, "SELECT count(*) FROM contracts c LEFT JOIN disclosure_files f ON f.file_id=c.source_file_id WHERE f.file_id IS NULL"),
            "correction_predecessor": _scalar(connection, "SELECT count(*) FROM correction_links l LEFT JOIN disclosures d ON d.receipt_no=l.predecessor_receipt_no WHERE d.receipt_no IS NULL"),
            "correction_successor": _scalar(connection, "SELECT count(*) FROM correction_links l LEFT JOIN disclosures d ON d.receipt_no=l.successor_receipt_no WHERE d.receipt_no IS NULL"),
        }
        correction = {
            "non_explicit_or_non_high": _scalar(connection, "SELECT count(*) FROM correction_links WHERE link_method<>'explicit_original_filing_date' OR confidence<>'high'"),
            "similarity_candidates": _scalar(
                connection,
                "SELECT count(*) FROM correction_links WHERE link_method ILIKE %s",
                ("%similar%",),
            ),
            "multiple_predecessors": _scalar(connection, "SELECT count(*) FROM (SELECT successor_receipt_no FROM correction_links GROUP BY successor_receipt_no HAVING count(*)>1) x"),
            "cycles": _scalar(connection, """WITH RECURSIVE walk(start_node,node,path,cycle) AS (
                SELECT predecessor_receipt_no, successor_receipt_no,
                  ARRAY[predecessor_receipt_no::text,successor_receipt_no::text]::text[], false
                FROM correction_links
                UNION ALL SELECT w.start_node,l.successor_receipt_no,w.path||l.successor_receipt_no,l.successor_receipt_no=ANY(w.path)
                FROM walk w JOIN correction_links l ON l.predecessor_receipt_no=w.node WHERE NOT w.cycle)
                SELECT count(*) FROM walk WHERE cycle"""),
        }
        schema = {
            "pg_trgm": bool(_scalar(connection, "SELECT count(*) FROM pg_extension WHERE extname='pg_trgm'")),
            "tables": [r[0] for r in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")],
            "foreign_keys": _scalar(connection, "SELECT count(*) FROM pg_constraint WHERE contype='f' AND connamespace='public'::regnamespace"),
            "unique_constraints": _scalar(connection, "SELECT count(*) FROM pg_constraint WHERE contype IN ('p','u') AND connamespace='public'::regnamespace"),
            "check_constraints": _scalar(connection, "SELECT count(*) FROM pg_constraint WHERE contype='c' AND connamespace='public'::regnamespace"),
            "indexes": _scalar(connection, "SELECT count(*) FROM pg_indexes WHERE schemaname='public'"),
        }
        provenance_missing = _scalar(connection, "SELECT count(*) FROM contracts WHERE field_provenance='{}'::jsonb OR source_file_id IS NULL OR parser_version='' ")
        result = {
            "counts": counts,
            "duplicate_receipt_no": duplicate_receipts,
            "orphan_foreign_keys": orphan_checks,
            "field_quality": fields,
            "provenance_missing": provenance_missing,
            "correction_validation": correction,
            "schema_validation": schema,
        }
        result["passed"] = (
            duplicate_receipts == 0 and not any(orphan_checks.values()) and provenance_missing == 0
            and not any(correction.values()) and schema["pg_trgm"]
        )
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    result = validate(args.database_url)
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
