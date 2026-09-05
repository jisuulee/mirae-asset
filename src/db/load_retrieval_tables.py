"""Stream lossless table JSONL into a bounded PostgreSQL retrieval projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.rag.table_projection import project_table


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def load_table_projections(
    connection: Any,
    *,
    tables_path: Path,
    archive_path: str | None = None,
    loader_namespace: str = "periodic-pilot-v1",
    batch_size: int = 5000,
) -> dict[str, int]:
    archive_path = archive_path or tables_path.as_posix()
    section_rows = connection.execute(
        "SELECT section_id, receipt_no, file_id, metadata->>'section_key' "
        "FROM document_sections WHERE metadata->>'loader_namespace'=%s",
        (loader_namespace,),
    ).fetchall()
    section_map = {(row[1], row[3]): (row[0], row[2]) for row in section_rows}
    connection.execute("CREATE TEMP TABLE pilot_table_ids(table_id text PRIMARY KEY) ON COMMIT DROP")

    sql = """
        INSERT INTO retrieval_tables(
          table_id, receipt_no, file_id, section_id, table_order, caption,
          searchable_text, row_count, column_count, has_rowspan, has_colspan,
          source_locator, provenance, archive_path, archive_identifier, metadata)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb)
        ON CONFLICT (table_id) DO UPDATE SET
          receipt_no=excluded.receipt_no, file_id=excluded.file_id,
          section_id=excluded.section_id, table_order=excluded.table_order,
          caption=excluded.caption, searchable_text=excluded.searchable_text,
          row_count=excluded.row_count, column_count=excluded.column_count,
          has_rowspan=excluded.has_rowspan, has_colspan=excluded.has_colspan,
          source_locator=excluded.source_locator, provenance=excluded.provenance,
          archive_path=excluded.archive_path,
          archive_identifier=excluded.archive_identifier, metadata=excluded.metadata
    """
    batch: list[tuple[Any, ...]] = []
    id_batch: list[tuple[str]] = []
    projected = 0
    orphan = 0
    with tables_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            projection = project_table(
                json.loads(line), archive_path=archive_path, archive_line=line_number
            )
            section = section_map.get((projection["receipt_no"], projection["section_key"]))
            if section is None:
                orphan += 1
                continue
            section_id, file_id = section
            projection["metadata"]["loader_namespace"] = loader_namespace
            projection["metadata"]["source_file"] = projection.pop("source_file")
            batch.append((
                projection["table_id"], projection["receipt_no"], file_id,
                section_id, projection["table_order"], projection["caption"],
                projection["searchable_text"], projection["row_count"],
                projection["column_count"], projection["has_rowspan"],
                projection["has_colspan"], projection["source_locator"],
                _json(projection["provenance"]), projection["archive_path"],
                projection["archive_identifier"], _json(projection["metadata"]),
            ))
            id_batch.append((projection["table_id"],))
            if len(batch) >= batch_size:
                with connection.cursor() as cursor:
                    cursor.executemany(sql, batch)
                    cursor.executemany("INSERT INTO pilot_table_ids VALUES (%s) ON CONFLICT DO NOTHING", id_batch)
                projected += len(batch)
                batch.clear()
                id_batch.clear()
    if batch:
        with connection.cursor() as cursor:
            cursor.executemany(sql, batch)
            cursor.executemany("INSERT INTO pilot_table_ids VALUES (%s) ON CONFLICT DO NOTHING", id_batch)
        projected += len(batch)

    deleted = connection.execute(
        "DELETE FROM retrieval_tables WHERE metadata->>'loader_namespace'=%s "
        "AND NOT EXISTS (SELECT 1 FROM pilot_table_ids p WHERE p.table_id=retrieval_tables.table_id)",
        (loader_namespace,),
    ).rowcount
    return {"projected": projected, "orphan_skipped": orphan, "stale_deleted": deleted}
