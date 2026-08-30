"""Capture representative exact/HNSW plans without embedding API calls."""

from __future__ import annotations

import argparse, json, os
from pathlib import Path

from src.db.load_postgres import connect


def _nodes(node):
    result = [{k: node.get(k) for k in ("Node Type", "Index Name", "Actual Rows") if node.get(k) is not None}]
    for child in node.get("Plans", []): result.extend(_nodes(child))
    return result


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    p.add_argument("--output", type=Path, default=Path("reports/periodic_full_hnsw_query_plans.json")); a=p.parse_args()
    if not a.database_url: p.error("DATABASE_URL is required")
    with connect(a.database_url) as c, c.cursor() as cur:
        cur.execute("SELECT embedding::text,company_name_normalized,receipt_no,receipt_date,report_type FROM section_vector_hnsw_projection LIMIT 1")
        vector,company,receipt,day,report=cur.fetchone()
        cases={"namespace": ("loader_namespace=%s AND embedding_model=%s AND embedding_version=%s", ['periodic-full-v1','clova-studio/bge-m3','embedding-v2']),
               "company": ("loader_namespace=%s AND embedding_model=%s AND embedding_version=%s AND company_name_normalized=%s", ['periodic-full-v1','clova-studio/bge-m3','embedding-v2',company]),
               "receipt": ("loader_namespace=%s AND embedding_model=%s AND embedding_version=%s AND receipt_no=%s", ['periodic-full-v1','clova-studio/bge-m3','embedding-v2',receipt]),
               "year_report": ("loader_namespace=%s AND embedding_model=%s AND embedding_version=%s AND receipt_date BETWEEN %s AND %s AND report_type=%s", ['periodic-full-v1','clova-studio/bge-m3','embedding-v2',day.replace(month=1,day=1),day.replace(month=12,day=31),report])}
        output={}
        for name,(where,params) in cases.items():
            cur.execute("SET LOCAL hnsw.ef_search=400");cur.execute("SET LOCAL hnsw.iterative_scan='strict_order'");cur.execute("SET LOCAL enable_sort=off")
            cur.execute(f"EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT vector_chunk_id FROM section_vector_hnsw_projection WHERE {where} ORDER BY embedding <=> %s::vector(1024) LIMIT 100", (*params,vector))
            plan=cur.fetchone()[0][0];cur.execute("SET LOCAL enable_sort=on")
            output[name]={"planning_ms":plan["Planning Time"],"execution_ms":plan["Execution Time"],"nodes":_nodes(plan["Plan"])}
    a.output.write_text(json.dumps(output,indent=2)+"\n",encoding="utf-8");print(a.output)


if __name__ == "__main__": main()
