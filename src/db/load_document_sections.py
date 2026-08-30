"""Transactional, idempotent loader for periodic section/table exports."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from src.db.load_postgres import connect
from src.db.load_retrieval_tables import load_table_projections

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "src" / "db" / "migrations" / "002_periodic_retrieval_tables.sql"
TABLE_MARKER = re.compile(r"\[TABLE:\s*[^\]]+\]")
KEYWORD_MIGRATION = ROOT / "src" / "db" / "migrations" / "003_keyword_search.sql"
FULL_INDEX_MIGRATION = ROOT / "src" / "db" / "migrations" / "004_full_keyword_indexes.sql"
TITLE_PATH_MIGRATION = ROOT / "src" / "db" / "migrations" / "007_title_path_search.sql"
PERIODIC_MIGRATIONS = (MIGRATION, KEYWORD_MIGRATION, FULL_INDEX_MIGRATION, TITLE_PATH_MIGRATION)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def classify_sections(sections: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    parents = {section.get("parent_section_key") for section in sections if section.get("parent_section_key")}
    counts = {"empty_no_table": 0, "table_only": 0, "heading_parent": 0, "excluded": 0, "narrative": 0}
    retained: list[dict[str, Any]] = []
    for section in sections:
        narrative = TABLE_MARKER.sub("", section.get("body_text") or "").strip()
        has_tables = bool(section.get("table_ids"))
        is_parent = section["section_key"] in parents
        if not narrative and not has_tables:
            counts["empty_no_table"] += 1
            if is_parent:
                counts["heading_parent"] += 1
                section = {**section, "section_kind": "hierarchy_only"}
                retained.append(section)
            else:
                counts["excluded"] += 1
        elif not narrative and has_tables:
            counts["table_only"] += 1
            retained.append({**section, "section_kind": "table_only"})
        else:
            counts["narrative"] += 1
            retained.append({**section, "section_kind": "narrative"})
    return retained, counts


def infer_file_role(source: dict[str, Any]) -> str:
    name = Path(source["source_file"]).name.lower()
    if source.get("parsed_as_main"):
        return "main"
    if "_00761" in name:
        return "consolidated_audit_report"
    if "_00760" in name:
        return "audit_report"
    if name.endswith((".html", ".htm")):
        return "alternative_html"
    return "attachment"


def _ensure_files(
    connection: Any, documents: list[dict[str, Any]], raw_root: Path
) -> tuple[dict[tuple[str, str], tuple[int, str]], dict[str, int]]:
    mapping: dict[tuple[str, str], tuple[int, str]] = {}
    inserted = 0
    role_updates = 0
    data_root = ROOT / "data"
    for document in documents:
        receipt = document["receipt_no"]
        for order, source in enumerate(document["source_files"], start=1):
            path = raw_root / Path(source["source_file"])
            if not path.exists():
                raise FileNotFoundError(path)
            role = infer_file_role(source)
            row = connection.execute(
                "SELECT file_id,file_role FROM disclosure_files WHERE receipt_no=%s AND sha256=%s::char(64)",
                (receipt, source["source_sha256"]),
            ).fetchone()
            if row:
                file_id, old_role = row
                if old_role != role:
                    connection.execute("UPDATE disclosure_files SET file_role=%s WHERE file_id=%s", (role, file_id))
                    role_updates += 1
            else:
                relative = str(path.relative_to(data_root))
                file_id = connection.execute(
                    "INSERT INTO disclosure_files(receipt_no,relative_path,file_role,byte_size,sha256,source_order) "
                    "VALUES (%s,%s,%s,%s,%s::char(64),%s) RETURNING file_id",
                    (receipt, relative, role, path.stat().st_size, source["source_sha256"], order),
                ).fetchone()[0]
                inserted += 1
            mapping[(receipt, source["source_file"])] = (file_id, role)
    return mapping, {"files_inserted": inserted, "file_roles_updated": role_updates, "files_mapped": len(mapping)}


def load_periodic(
    database_url: str,
    *,
    export_dir: Path,
    raw_root: Path,
    loader_namespace: str = "periodic-pilot-v1",
    fail_after_sections: bool = False,
) -> dict[str, Any]:
    documents = _load_jsonl(export_dir / "documents.jsonl")
    raw_sections = _load_jsonl(export_dir / "sections.jsonl")
    receipts = {document["receipt_no"] for document in documents}
    if {section["receipt_no"] for section in raw_sections} - receipts:
        raise ValueError("section export contains receipts outside document summary")
    sections, policy = classify_sections(raw_sections)

    with connect(database_url) as connection:
        for migration in PERIODIC_MIGRATIONS:
            connection.execute(migration.read_text(encoding="utf-8"))
        disclosure_count = connection.execute(
            "SELECT count(*) FROM disclosures WHERE receipt_no=ANY(%s)", (list(receipts),)
        ).fetchone()[0]
        if disclosure_count != len(receipts):
            raise RuntimeError(f"pilot disclosures missing: {len(receipts)-disclosure_count}")
        file_map, file_metrics = _ensure_files(connection, documents, raw_root)
        main_file = {
            document["receipt_no"]: file_map[(document["receipt_no"], document["source_file"])]
            for document in documents
        }
        connection.execute("CREATE TEMP TABLE pilot_section_keys(section_key text PRIMARY KEY) ON COMMIT DROP")
        upsert_sql = """
            INSERT INTO document_sections(
              receipt_no,file_id,parent_section_id,section_order,section_path,
              title_raw,title_normalized,body_text,source_locator,metadata)
            VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (file_id,section_order) DO UPDATE SET
              receipt_no=excluded.receipt_no, parent_section_id=NULL,
              section_path=excluded.section_path, title_raw=excluded.title_raw,
              title_normalized=excluded.title_normalized, body_text=excluded.body_text,
              source_locator=excluded.source_locator, metadata=excluded.metadata
        """
        rows = []
        keys = []
        pdf_sections = 0
        for section in sections:
            file_id, file_role = main_file[section["receipt_no"]]
            source_type = Path(section["source_file"]).suffix.lower().lstrip(".")
            provenance = section.get("provenance") or {}
            page_number = provenance.get("page_number")
            if source_type == "pdf":
                pdf_sections += 1
            metadata = {
                "loader_namespace": loader_namespace,
                "disclosure_id": section["receipt_no"],
                "section_key": section["section_key"],
                "parent_section_key": section.get("parent_section_key"),
                "level": section.get("level"),
                "block_ids": section.get("block_ids") or [],
                "table_ids": section.get("table_ids") or [],
                "source_sha256": section.get("source_sha256"),
                "source_type": source_type,
                "file_role": file_role,
                "corpus_role": "main" if file_role == "main" else "attachment",
                "parse_status": section.get("parse_status"),
                "warning_codes": section.get("warning_codes") or [],
                "section_kind": section["section_kind"],
                "title_kind": "untitled" if section.get("title_raw") is None else "source",
                "display_title": f"PDF page {page_number}" if source_type == "pdf" and page_number else section.get("title_normalized"),
                "provenance": provenance,
            }
            rows.append((
                section["receipt_no"], file_id, section["section_order"],
                section["section_path"], section.get("title_raw"),
                section.get("title_normalized"), section.get("body_text") or "",
                section["source_locator"], _json(metadata),
            ))
            keys.append((section["section_key"],))
        with connection.cursor() as cursor:
            cursor.executemany(upsert_sql, rows)
            cursor.executemany("INSERT INTO pilot_section_keys VALUES (%s)", keys)

        stale_deleted = connection.execute(
            "DELETE FROM document_sections WHERE metadata->>'loader_namespace'=%s "
            "AND NOT EXISTS (SELECT 1 FROM pilot_section_keys p WHERE p.section_key=document_sections.metadata->>'section_key')",
            (loader_namespace,),
        ).rowcount
        parent_updated = connection.execute(
            "UPDATE document_sections child SET parent_section_id=parent.section_id "
            "FROM document_sections parent WHERE child.metadata->>'loader_namespace'=%s "
            "AND parent.metadata->>'loader_namespace'=%s "
            "AND child.metadata->>'parent_section_key'=parent.metadata->>'section_key'",
            (loader_namespace, loader_namespace),
        ).rowcount
        if fail_after_sections:
            raise RuntimeError("injected failure after sections")
        table_metrics = load_table_projections(
            connection,
            tables_path=export_dir / "tables.jsonl",
            archive_path=(export_dir / "tables.jsonl").as_posix(),
            loader_namespace=loader_namespace,
        )
        result = {
            "receipts": len(receipts), "sections_input": len(raw_sections),
            "sections_loaded": len(sections), "pdf_sections": pdf_sections,
            "parent_links_expected": sum(bool(s.get("parent_section_key")) for s in sections),
            "parent_links_updated": parent_updated, "stale_sections_deleted": stale_deleted,
            "empty_policy": policy, **file_metrics, "tables": table_metrics,
        }
    return result



load_pilot = load_periodic

def default_raw_root() -> Path:
    return next(path / "corpus" / "raw" for path in (ROOT / "data").iterdir() if (path / "corpus" / "raw").exists())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--export-dir", type=Path, default=ROOT / "artifacts" / "periodic_parser_pilot")
    parser.add_argument("--raw-root", type=Path, default=default_raw_root())
    parser.add_argument("--loader-namespace", default="periodic-full-v1")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    print(json.dumps(load_periodic(args.database_url, export_dir=args.export_dir, raw_root=args.raw_root, loader_namespace=args.loader_namespace), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
