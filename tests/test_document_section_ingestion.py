from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from src.db.load_document_sections import (
    FULL_INDEX_MIGRATION,
    KEYWORD_MIGRATION,
    PERIODIC_MIGRATIONS,
    classify_sections,
    infer_file_role,
    load_pilot,
)
from src.db.load_postgres import connect
from src.rag.table_projection import SEARCHABLE_TEXT_LIMIT, project_table


def section(key: str, body: str = "", tables: list[str] | None = None, parent: str | None = None) -> dict:
    return {"section_key": key, "body_text": body, "table_ids": tables or [], "parent_section_key": parent}



class SectionPolicyTest(unittest.TestCase):
    def test_keyword_migration_recreates_fts_index_after_search_vector(self) -> None:
        sql = KEYWORD_MIGRATION.read_text(encoding="utf-8")
        drop_position = sql.index("DROP COLUMN IF EXISTS search_tsv")
        column_position = sql.index("ADD COLUMN search_tsv")
        index_position = sql.index("CREATE INDEX IF NOT EXISTS document_sections_search_idx")
        self.assertLess(drop_position, column_position)
        self.assertLess(column_position, index_position)

    def test_full_index_migration_is_available_to_loader(self) -> None:
        sql = FULL_INDEX_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("document_sections_search_idx", sql)
        self.assertIn("document_sections_loader_namespace_idx", sql)
        self.assertEqual(
            ["002_periodic_retrieval_tables.sql", "003_keyword_search.sql", "004_full_keyword_indexes.sql"],
            [migration.name for migration in PERIODIC_MIGRATIONS],
        )

    def test_empty_table_only_and_parent_policy(self) -> None:
        rows = [section("parent"), section("child", "text", parent="parent"), section("drop"), section("table", "[TABLE: t1]", ["t1"])]
        retained, counts = classify_sections(rows)
        self.assertEqual({"parent", "child", "table"}, {row["section_key"] for row in retained})
        self.assertEqual(1, counts["excluded"])
        self.assertEqual(1, counts["heading_parent"])
        self.assertEqual(1, counts["table_only"])

    def test_attachment_roles(self) -> None:
        self.assertEqual("audit_report", infer_file_role({"source_file": "x_00760.xml", "parsed_as_main": False}))
        self.assertEqual("consolidated_audit_report", infer_file_role({"source_file": "x_00761.xml", "parsed_as_main": False}))
        self.assertEqual("alternative_html", infer_file_role({"source_file": "viewer.html", "parsed_as_main": False}))

    def test_table_projection_is_bounded_and_preserves_spans(self) -> None:
        table = {"table_id": "1:t000007", "receipt_no": "1", "section_key": "s", "caption": "caption", "rows": [[{"text": "H", "is_header": True, "rowspan": "2", "colspan": "3"}, {"text": "x" * 20000}]], "source_file": "x.xml", "source_locator": "/TABLE[1]", "provenance": {"source_line": 1}, "searchable_text": "x" * 20000}
        result = project_table(table, archive_path="tables.jsonl", archive_line=7)
        self.assertLessEqual(len(result["searchable_text"]), SEARCHABLE_TEXT_LIMIT)
        self.assertTrue(result["has_rowspan"])
        self.assertTrue(result["has_colspan"])
        self.assertEqual(4, result["column_count"])
        self.assertIn("line:7", result["archive_identifier"])


@unittest.skipUnless(os.getenv("DATABASE_URL"), "DATABASE_URL is required for PostgreSQL integration tests")
class DocumentSectionDatabaseTest(unittest.TestCase):
    export_dir = Path("artifacts/periodic_parser_full")

    def test_parent_source_pdf_and_idempotency_state(self) -> None:
        with connect(os.environ["DATABASE_URL"]) as connection:
            count = connection.execute("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'='periodic-full-v1'").fetchone()[0]
            self.assertEqual(82862, count)
            self.assertEqual(0, connection.execute("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'='periodic-full-v1' AND metadata->>'parent_section_key' IS NOT NULL AND parent_section_id IS NULL").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'='periodic-full-v1' AND metadata->>'source_type'='pdf' AND metadata#>>'{provenance,page_number}' IS NULL").fetchone()[0])
            self.assertEqual(1054, connection.execute("SELECT count(DISTINCT receipt_no) FROM disclosure_files WHERE receipt_no IN (SELECT receipt_no FROM document_sections WHERE metadata->>'loader_namespace'='periodic-full-v1') AND file_role='main'").fetchone()[0])

    def test_table_projection_and_fk(self) -> None:
        with connect(os.environ["DATABASE_URL"]) as connection:
            self.assertEqual(1401752, connection.execute("SELECT count(*) FROM retrieval_tables WHERE metadata->>'loader_namespace'='periodic-full-v1'").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT count(*) FROM retrieval_tables t LEFT JOIN document_sections s ON s.section_id=t.section_id WHERE t.metadata->>'loader_namespace'='periodic-full-v1' AND s.section_id IS NULL").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT count(*) FROM retrieval_tables WHERE length(searchable_text)>12000").fetchone()[0])

    def test_injected_failure_rolls_back(self) -> None:
        before = None
        with connect(os.environ["DATABASE_URL"]) as connection:
            before = connection.execute("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'='periodic-full-v1'").fetchone()[0]
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            load_pilot(os.environ["DATABASE_URL"], export_dir=self.export_dir, raw_root=next(p / "corpus" / "raw" for p in Path("data").iterdir() if (p / "corpus" / "raw").exists()), fail_after_sections=True, loader_namespace="periodic-full-v1")
        with connect(os.environ["DATABASE_URL"]) as connection:
            after = connection.execute("SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'='periodic-full-v1'").fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
