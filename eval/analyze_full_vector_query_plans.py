"""Capture DB-only exact cosine plans using an already stored 1024-d vector."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from src.db.load_postgres import connect


def node_types(node: dict[str, Any]) -> list[str]:
    values = [node.get("Node Type", "unknown")]
    for child in node.get("Plans", []):
        values.extend(node_types(child))
    return values


def explain(connection: Any, vector: str, extra_clause: str, extra_params: list[Any]) -> dict[str, Any]:
    sql = f"""
      EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
      WITH chunk_candidates AS (
        SELECT c.section_id,c.vector_chunk_id,c.chunk_order,c.receipt_no,
               e.embedding <=> %s::vector AS distance
        FROM section_vector_chunks c
        JOIN section_chunk_embeddings ce USING(vector_chunk_id)
        JOIN section_embedding_vectors e USING(embedding_id)
        JOIN document_sections s USING(section_id)
        JOIN disclosures d ON d.receipt_no=c.receipt_no
        JOIN companies co ON co.company_id=d.company_id
        JOIN disclosure_files f ON f.file_id=c.file_id
        WHERE c.loader_namespace=%s AND e.embedding_model=%s AND e.embedding_version=%s
          AND e.embedding_dimension=1024 {extra_clause}
      ), section_best AS (
        SELECT DISTINCT ON (section_id) * FROM chunk_candidates
        ORDER BY section_id,distance,chunk_order
      )
      SELECT * FROM section_best ORDER BY distance,section_id LIMIT 10
    """
    params = [vector, "periodic-full-v1", "clova-studio/bge-m3", "embedding-v2", *extra_params]
    payload = connection.execute(sql, tuple(params)).fetchone()[0][0]
    return {
        "planning_time_ms": payload["Planning Time"], "execution_time_ms": payload["Execution Time"],
        "node_types": node_types(payload["Plan"]), "plan": payload,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output", type=Path, default=Path("reports/periodic_full_vector_query_plans.json"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL is required")
    with connect(args.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        vector = connection.execute("""SELECT embedding::text FROM section_embedding_vectors
            WHERE embedding_model='clova-studio/bge-m3' AND embedding_version='embedding-v2'
            ORDER BY embedding_id LIMIT 1""").fetchone()[0]
        company = connection.execute("""SELECT co.company_name_normalized FROM section_vector_chunks c
            JOIN disclosures d USING(receipt_no) JOIN companies co USING(company_id)
            WHERE c.loader_namespace='periodic-full-v1' GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""").fetchone()[0]
        cases = {
            "namespace_only": ("", []),
            "company_filter": ("AND co.company_name_normalized=%s", [company]),
            "receipt_filter": ("AND c.receipt_no=%s", ["20240514001522"]),
            "year_report_filter": ("AND d.receipt_date BETWEEN %s AND %s AND d.report_type=%s",
                                   ["2025-01-01", "2025-12-31", "annual"]),
        }
        report = {
            "condition": "DB-only EXPLAIN ANALYZE; existing stored vector; warm state not forced or flushed",
            "distance": "exact cosine (<=>)", "ann_index": None,
            "plans": {name: explain(connection, vector, clause, params) for name, (clause, params) in cases.items()},
        }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({name: {"planning_time_ms": value["planning_time_ms"],
                             "execution_time_ms": value["execution_time_ms"],
                             "node_types": sorted(set(value["node_types"]))}
                      for name, value in report["plans"].items()}, indent=2))


if __name__ == "__main__":
    main()
