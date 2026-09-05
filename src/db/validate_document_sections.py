"""Validate periodic pilot section and table-projection ingestion."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from src.db.load_postgres import connect


def validate(
    database_url: str,
    *,
    export_dir: Path,
    loader_namespace: str = "periodic-pilot-v1",
) -> dict[str, Any]:
    build = json.loads((export_dir / "build_summary.json").read_text(encoding="utf-8"))
    receipts = build["receipt_nos"]
    with connect(database_url) as connection:
        scalar = lambda sql, params=(): connection.execute(sql, params).fetchone()[0]
        sections = scalar("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'=%s", (loader_namespace,))
        tables = scalar("SELECT count(*) FROM retrieval_tables WHERE metadata->>'loader_namespace'=%s", (loader_namespace,))
        parent_expected = scalar("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'=%s AND metadata->>'parent_section_key' IS NOT NULL", (loader_namespace,))
        parent_resolved = scalar("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'=%s AND metadata->>'parent_section_key' IS NOT NULL AND parent_section_id IS NOT NULL", (loader_namespace,))
        metrics = {
            "target_receipts": len(receipts),
            "disclosure_mapped": scalar("SELECT count(*) FROM disclosures WHERE receipt_no=ANY(%s)", (receipts,)),
            "receipts_with_main_file": scalar("SELECT count(DISTINCT receipt_no) FROM disclosure_files WHERE receipt_no=ANY(%s) AND file_role='main'", (receipts,)),
            "source_files_mapped": scalar("SELECT count(*) FROM disclosure_files WHERE receipt_no=ANY(%s)", (receipts,)),
            "sections": sections,
            "tables": tables,
            "parent_links_expected": parent_expected,
            "parent_links_resolved": parent_resolved,
            "parent_resolution_rate": parent_resolved / parent_expected if parent_expected else 1.0,
            "section_provenance_missing": scalar("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'=%s AND NOT (metadata ? 'provenance')", (loader_namespace,)),
            "table_provenance_missing": scalar("SELECT count(*) FROM retrieval_tables WHERE metadata->>'loader_namespace'=%s AND provenance='{}'::jsonb", (loader_namespace,)),
            "orphan_sections": scalar("SELECT count(*) FROM document_sections s LEFT JOIN disclosures d USING(receipt_no) LEFT JOIN disclosure_files f ON f.file_id=s.file_id WHERE s.metadata->>'loader_namespace'=%s AND (d.receipt_no IS NULL OR f.file_id IS NULL)", (loader_namespace,)),
            "orphan_tables": scalar("SELECT count(*) FROM retrieval_tables t LEFT JOIN document_sections s ON s.section_id=t.section_id LEFT JOIN disclosure_files f ON f.file_id=t.file_id WHERE t.metadata->>'loader_namespace'=%s AND (s.section_id IS NULL OR f.file_id IS NULL)", (loader_namespace,)),
            "duplicate_sections": scalar("SELECT count(*) FROM (SELECT file_id,section_order FROM document_sections WHERE metadata->>'loader_namespace'=%s GROUP BY file_id,section_order HAVING count(*)>1) x", (loader_namespace,)),
            "duplicate_tables": scalar("SELECT count(*) FROM (SELECT table_id FROM retrieval_tables WHERE metadata->>'loader_namespace'=%s GROUP BY table_id HAVING count(*)>1) x", (loader_namespace,)),
            "hierarchy_only_sections": scalar("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'=%s AND metadata->>'section_kind'='hierarchy_only'", (loader_namespace,)),
            "table_only_sections": scalar("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'=%s AND metadata->>'section_kind'='table_only'", (loader_namespace,)),
            "pdf_sections": scalar("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'=%s AND metadata->>'source_type'='pdf'", (loader_namespace,)),
            "pdf_page_provenance_missing": scalar("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'=%s AND metadata->>'source_type'='pdf' AND metadata#>>'{provenance,page_number}' IS NULL", (loader_namespace,)),
            "contracts": scalar("SELECT count(*) FROM contracts"),
            "correction_links": scalar("SELECT count(*) FROM correction_links"),
        }
        metrics["file_roles"] = dict(connection.execute("SELECT file_role,count(*) FROM disclosure_files WHERE receipt_no=ANY(%s) GROUP BY file_role ORDER BY file_role", (receipts,)).fetchall())
        metrics["content_by_file_role"] = [
            {"file_role": row[0], "sections": row[1], "tables": row[2]}
            for row in connection.execute(
                "SELECT f.file_role,count(DISTINCT s.section_id),count(DISTINCT t.retrieval_table_id) "
                "FROM disclosure_files f LEFT JOIN document_sections s ON s.file_id=f.file_id AND s.metadata->>'loader_namespace'=%s "
                "LEFT JOIN retrieval_tables t ON t.file_id=f.file_id AND t.metadata->>'loader_namespace'=%s "
                "WHERE f.receipt_no=ANY(%s) GROUP BY f.file_role ORDER BY f.file_role",
                (loader_namespace, loader_namespace, receipts),
            ).fetchall()
        ]
        metrics["passed"] = all((
            metrics["disclosure_mapped"] == len(receipts),
            metrics["receipts_with_main_file"] == len(receipts),
            metrics["parent_links_expected"] == metrics["parent_links_resolved"],
            metrics["section_provenance_missing"] == 0,
            metrics["table_provenance_missing"] == 0,
            metrics["orphan_sections"] == 0,
            metrics["orphan_tables"] == 0,
            metrics["duplicate_sections"] == 0,
            metrics["duplicate_tables"] == 0,
            metrics["pdf_page_provenance_missing"] == 0,
            metrics["contracts"] == 1106,
            metrics["correction_links"] == 461,
        ))
        return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--export-dir", type=Path, default=Path("artifacts/periodic_parser_pilot"))
    parser.add_argument("--loader-namespace", default="periodic-pilot-v1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    result = validate(args.database_url, export_dir=args.export_dir, loader_namespace=args.loader_namespace)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
