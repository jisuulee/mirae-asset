"""Capture representative plans from the exact current retrieval SQL."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from src.db.load_postgres import connect
from src.retrieval.sections import search_sections
from src.retrieval.tables import search_tables


class CaptureCursor:
    description: list[Any] = []
    def __init__(self, owner: "CaptureConnection"): self.owner = owner
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def execute(self, sql, params): self.owner.sql, self.owner.params = sql, params
    def fetchall(self): return []


class CaptureConnection:
    def __init__(self): self.sql = ""; self.params: tuple[Any, ...] = ()
    def cursor(self): return CaptureCursor(self)


def captured(kind: str, mode: str) -> tuple[str, tuple[Any, ...]]:
    connection = CaptureConnection()
    if kind == "section":
        search_sections(connection, "회사의 개요", mode=mode,
                        corpus_namespace="periodic-full-v1", top_k=10)
    else:
        search_tables(connection, "발행일 만기일 발행액", mode=mode,
                      corpus_namespace="periodic-full-v1", top_k=10)
    return connection.sql, connection.params


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output", type=Path, default=Path("reports/periodic_full_keyword_query_plans.json"))
    args = parser.parse_args()
    if not args.database_url: parser.error("DATABASE_URL is required")
    plans = {}
    with connect(args.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for kind in ("section", "table"):
            for mode in ("fts", "trigram", "combined"):
                sql, params = captured(kind, mode)
                plan = connection.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).fetchone()[0][0]
                plans[f"{kind}_{mode}"] = plan
    args.output.write_text(json.dumps(plans, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    for key, plan in plans.items():
        node = plan["Plan"]
        print(f"{key}: {plan['Planning Time']:.3f}ms planning, {plan['Execution Time']:.3f}ms execution, root={node['Node Type']}")


if __name__ == "__main__": main()