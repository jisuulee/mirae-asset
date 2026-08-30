from pathlib import Path
import inspect
import os
import unittest

from src.db.load_postgres import connect

from src.retrieval.title_paths import fuse_named_channels, search_title_paths


class Cursor:
    description=[("section_id",),("receipt_no",),("score",)]
    def __init__(self):self.sql="";self.params=()
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,params):self.sql,self.params=sql,params
    def fetchall(self):return [(1,"R1",.8)]
class Connection:
    def __init__(self):self.last=None;self.cursors=[]
    def cursor(self):self.last=Cursor();self.cursors.append(self.last);return self.last


def result(section,**extra):return {"section_id":section,"score":1.0,"receipt_no":f"R{section}","source_locator":f"x:{section}","provenance":{"file_id":section},**extra}


def test_title_fts_uses_only_title_path_columns_and_original_query():
    connection=Connection();rows=search_title_paths(connection,"  연구개발\u3000현황 ",mode="fts",top_k=3)
    sql=connection.last.sql.lower();assert rows[0]["section_id"]==1
    assert "title_path_search_tsv" in sql and "body_text" not in sql
    assert "연구개발 현황" in connection.last.params and connection.last.params[-1]==3


def test_title_trigram_and_metadata_filters():
    connection=Connection();search_title_paths(connection,"매출현황",mode="trigram",company="회사 A",receipt_no="R1",corpus_namespace="periodic-full-v1")
    sql=connection.last.sql
    assert "title_path_search_normalized" in sql and "title_path_search_nospace" in sql
    assert "company_name_normalized" in sql and "s.receipt_no" in sql
    assert "periodic-full-v1" in connection.last.params


def test_combined_calls_bounded_title_channels():
    connection=Connection();rows=search_title_paths(connection,"연구개발",mode="combined",top_k=5)
    assert len(connection.cursors)==2 and rows[0]["section_id"]==1
    assert all(cursor.params[-1]==100 for cursor in connection.cursors)


def test_three_way_rrf_dedup_order_and_provenance():
    rows=fuse_named_channels({"fts":[result(1),result(2)],"vector":[result(2),result(3)],"title":[result(3),result(2)]},top_k=3)
    assert len(rows)==3 and rows[0]["section_id"]==2
    assert rows[0]["fts_rank"]==2 and rows[0]["vector_rank"]==1 and rows[0]["title_rank"]==2
    assert rows[0]["provenance"]=={"file_id":2}
    again=fuse_named_channels({"title":[result(3),result(2)],"vector":[result(2),result(3)],"fts":[result(1),result(2)]},top_k=3)
    assert [x["section_id"] for x in again]==[x["section_id"] for x in rows]


def test_empty_title_query_and_multi_gold_compatibility():
    assert search_title_paths(Connection(),"  ")==[]
    rows=fuse_named_channels({"title":[result(2)]},top_k=10)
    assert {x["section_id"] for x in rows}&{1,2}=={2}


def test_migration_is_idempotent_and_has_all_indexes():
    sql=Path("src/db/migrations/007_title_path_search.sql").read_text(encoding="utf-8")
    assert sql.count("ADD COLUMN IF NOT EXISTS")==3
    assert sql.count("CREATE INDEX IF NOT EXISTS")==3
    assert "body_text" not in sql


def test_api_has_no_gold_or_benchmark_routing_and_does_not_mutate_query():
    parameters=set(inspect.signature(search_title_paths).parameters)
    assert not parameters&{"gold_section_ids","question_id","category","difficulty"}
    query="연구개발 현황";search_title_paths(Connection(),query,mode="fts")
    assert query=="연구개발 현황"


@unittest.skipUnless(os.getenv("DATABASE_URL"),"DATABASE_URL required")
def test_title_migration_clean_and_second_run_are_safe():
    sql=Path("src/db/migrations/007_title_path_search.sql").read_text(encoding="utf-8")
    with connect(os.environ["DATABASE_URL"]) as connection:
        try:
            connection.execute("CREATE SCHEMA title_path_migration_test")
            connection.execute("SET LOCAL search_path=title_path_migration_test,public")
            connection.execute("CREATE TABLE document_sections(section_id bigint,title_normalized text,section_path text)")
            connection.execute(sql);connection.execute(sql)
            columns={x[0] for x in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='title_path_migration_test' AND table_name='document_sections'").fetchall()}
            assert {"title_path_search_normalized","title_path_search_nospace","title_path_search_tsv"}<=columns
            indexes=connection.execute("SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='title_path_migration_test' AND i.indisvalid AND i.indisready").fetchone()[0]
            assert indexes==3
        finally:
            connection.rollback()
