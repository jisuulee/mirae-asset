from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
from types import SimpleNamespace
import urllib.error
from email.message import Message

import pytest

from eval.evaluate_full_retrieval import evaluate
from eval.evaluate_full_vector import evaluate_vector
from src.retrieval.embeddings import (
    ClovaEmbeddingV2,
    DeterministicTestEmbedding,
    EmbeddingResult,
    EmbeddingSpec,
    embedding_content_hash,
    validate_embedding,
)
from src.retrieval.vector_sections import chunk_section, load_section_embeddings, search_vector_sections


def section(**overrides):
    value = {
        "section_id": 1, "receipt_no": "r1", "file_id": 2, "parent_section_id": None,
        "section_order": 3, "section_path": "II. 사업 / 1. 개요", "title": "사업의 개요",
        "body_text": "회사는 반도체를 연구하고 생산합니다.", "source_locator": "xpath:/section",
        "loader_namespace": "periodic-full-v1", "section_kind": "narrative", "source_type": "xml",
    }
    value.update(overrides)
    return value


def test_content_hash_is_exact_and_stable():
    assert embedding_content_hash("가 나") == embedding_content_hash("가 나")
    assert embedding_content_hash("가 나") != embedding_content_hash("가나")


def test_short_section_stays_one_chunk_with_parent_provenance():
    chunks = chunk_section(section(parent_section_id=99))
    assert len(chunks) == 1
    assert chunks[0].section_id == 1 and chunks[0].parent_section_id == 99
    assert chunks[0].source_locator == "xpath:/section"
    assert "사업의 개요" in chunks[0].text


def test_long_section_chunks_and_maps_to_parent():
    chunks = chunk_section(section(body_text=("문단입니다. " * 900)), long_threshold=1000,
                           target_chars=600, overlap_chars=20)
    assert len(chunks) > 1
    assert [chunk.chunk_order for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.section_id == 1 for chunk in chunks)
    assert all(len(chunk.text) <= 600 for chunk in chunks)


def test_hierarchy_and_table_only_are_excluded():
    assert chunk_section(section(section_kind="hierarchy_only")) == []
    assert chunk_section(section(section_kind="table_only", body_text="[TABLE: t1]")) == []


def test_vector_dimension_validation():
    spec = EmbeddingSpec("x", "m", "v", 3)
    validate_embedding(EmbeddingResult((1.0, 2.0, 3.0)), spec)
    with pytest.raises(ValueError, match="dimension"):
        validate_embedding(EmbeddingResult((1.0, 2.0)), spec)


def test_provider_retry_after_is_response_driven():
    headers = Message()
    headers["Retry-After"] = "3.5"
    error = urllib.error.HTTPError("https://example.invalid", 429, "limited", headers, None)
    provider = ClovaEmbeddingV2("test")
    assert provider._retry_delay(error, 0) == 3.5
    assert provider._retry_delay(RuntimeError(), 2) == 4.0
    assert provider._duration("1m2.5s") == 62.5


class Result:
    def __init__(self, rows=()): self.rows = list(rows)
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0] if self.rows else None


class MemoryConnection:
    def __init__(self):
        self.vectors = {}; self.next_embedding = 1; self.next_chunk = 1; self.sql = []
    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if "FROM document_sections s" in sql and "section_kind'='narrative'" in sql:
            row = section()
            return Result([(row[k] for k in ())]) if False else Result([(
                1, "r1", 2, None, 3, row["section_path"], row["title"], row["body_text"],
                row["source_locator"], row["loader_namespace"], row["section_kind"], row["source_type"],
            )])
        if "SELECT content_hash,embedding_id" in sql:
            return Result([(key, value, 4) for key, value in self.vectors.items() if key in params[2]])
        if "INSERT INTO section_embedding_vectors" in sql:
            key = params[0]
            if key not in self.vectors:
                self.vectors[key] = self.next_embedding; self.next_embedding += 1
            return Result([(self.vectors[key],)])
        if "INSERT INTO section_vector_chunks" in sql:
            return Result([(self.next_chunk,)])
        if "INSERT INTO section_chunk_embeddings" in sql:
            self.next_chunk += 1; return Result()
        raise AssertionError(sql)


def test_idempotent_reembedding_reuses_content_hash():
    connection = MemoryConnection()
    provider = DeterministicTestEmbedding(4)
    first = load_section_embeddings(connection, provider, loader_namespace="periodic-full-v1")
    second = load_section_embeddings(connection, provider, loader_namespace="periodic-full-v1")
    assert first["embedded"] == 1 and first["reused"] == 0
    assert second["embedded"] == 0 and second["reused"] == 1
    assert provider.calls == 1


class Cursor:
    description = []
    def __init__(self, owner): self.owner = owner
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def execute(self, sql, params): self.owner.sql, self.owner.params = sql, params
    def fetchall(self): return []


class SearchConnection:
    def __init__(self): self.sql = ""; self.params = ()
    def cursor(self): return Cursor(self)


def test_vector_search_filters_namespace_top_k_and_provenance():
    connection = SearchConnection()
    provider = DeterministicTestEmbedding(4)
    assert search_vector_sections(
        connection, "연구개발 분야", provider, top_k=7, company="삼성전자",
        date_from=date(2024, 1, 1), date_to=date(2024, 12, 31), report_type="사업보고서",
        receipt_no="r1", corpus_namespace="periodic-full-v1",
    ) == []
    assert "c.loader_namespace=%s" in connection.sql
    assert "co.company_name_normalized=%s" in connection.sql
    assert "d.receipt_date >= %s" in connection.sql and "d.receipt_date <= %s" in connection.sql
    assert "c.source_locator" in connection.sql and "provenance" in connection.sql
    assert "periodic-full-v1" in connection.params and connection.params[-1] == 7


def test_vector_search_top_k_and_empty_query():
    provider = DeterministicTestEmbedding(4)
    with pytest.raises(ValueError, match="top_k"):
        search_vector_sections(SearchConnection(), "x", provider, top_k=0)
    assert search_vector_sections(SearchConnection(), " \u200b ", provider) == []
    assert provider.calls == 0


def test_vector_migration_is_separate_and_dimension_safe():
    sql = Path("src/db/migrations/005_pgvector.sql").read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "section_embedding_vectors" in sql and "section_vector_chunks" in sql
    assert "embedding_dimension" in sql and "content_hash" in sql
    assert "ALTER TABLE document_sections" not in sql
    assert "using hnsw" not in sql.lower()


def test_reviewed_only_and_vector_table_exclusion():
    base = {
        "benchmark_version": "v", "category": "narrative_description", "question": "q",
        "difficulty": "hard", "retrieval_target": "section", "company_filter_required": False,
        "year_filter_required": False, "gold_receipt_nos": ["r1"], "gold_section_ids": [1],
        "gold_table_ids": [], "gold_source_locators": ["x"], "gold_type": "section",
        "gold_provenance": [{"receipt_no": "r1", "source_locator": "x", "section_order": 1, "section_key": "k"}],
        "human_review_status": "reviewed", "loader_namespace": "periodic-full-v1",
        "corpus_snapshot": "s", "split": "regression", "source_type": "xml",
    }
    table = {**base, "question_id": "table", "retrieval_target": "table", "gold_type": "table",
             "gold_table_ids": ["t1"], "human_review_status": "needs_review"}
    section_gold = {**base, "question_id": "section"}
    report = evaluate([section_gold, table], [
        {"question_id": "section", "section_results": [{"section_id": 1, "receipt_no": "r1"}]},
        {"question_id": "table", "error": "excluded_table_only_target", "section_results": []},
    ])
    assert report["reviewed_only"]["section"]["top1"] == 1.0
    assert report["overall"]["receipt"]["question_count"] == 1
    assert report["overall"]["table"]["question_count"] == 0


def test_vector_recovery_and_top1_four_way_are_explicit():
    base = {
        "benchmark_version": "v", "category": "narrative_description", "question": "q",
        "difficulty": "hard", "retrieval_target": "section", "company_filter_required": False,
        "year_filter_required": False, "gold_receipt_nos": ["r1"], "gold_section_ids": [1],
        "gold_table_ids": [], "gold_source_locators": ["x"], "gold_type": "section",
        "gold_provenance": [{"receipt_no": "r1", "source_locator": "x", "section_order": 1, "section_key": "k"}],
        "human_review_status": "needs_review", "loader_namespace": "periodic-full-v1",
        "corpus_snapshot": "s", "split": "held_out", "source_type": "xml",
    }
    gold = [{**base, "question_id": "vector-only"}, {**base, "question_id": "fts-only"}]
    vector = [
        {"question_id": "vector-only", "section_results": [{"section_id": 1, "receipt_no": "r1"}]},
        {"question_id": "fts-only", "section_results": []},
    ]
    fts = [
        {"question_id": "vector-only", "section_results": []},
        {"question_id": "fts-only", "section_results": [{"section_id": 1, "receipt_no": "r1"}]},
    ]
    fts_failures = [{"question_id": "vector-only", "failure_category": "semantic_paraphrase",
                     "difficulty": "hard"}]
    report = evaluate_vector(gold, vector, fts_failures, fts)
    assert report["fts_recovery"]["semantic_paraphrase"]["vector_recovered_count"] == 1
    assert report["fts_recovery"]["hard"]["vector_recovered_count"] == 1
    assert report["top1_four_way"]["all_candidate_section_eligible"]["counts"] == {
        "both_correct": 0, "fts_only_correct": 1, "vector_only_correct": 1, "both_wrong": 0,
    }


def test_vector_benchmark_runner_marks_table_only_excluded(tmp_path, monkeypatch):
    import eval.run_full_vector_benchmark as runner

    base = {
        "benchmark_version": "v", "category": "narrative_description", "question": "q",
        "difficulty": "hard", "retrieval_target": "section", "company_filter_required": False,
        "year_filter_required": False, "gold_receipt_nos": ["r1"], "gold_section_ids": [1],
        "gold_table_ids": [], "gold_source_locators": ["x"], "gold_type": "section",
        "gold_provenance": [{"receipt_no": "r1", "source_locator": "x", "section_order": 1, "section_key": "k"}],
        "human_review_status": "reviewed", "loader_namespace": "periodic-full-v1",
        "corpus_snapshot": "s", "split": "regression", "source_type": "xml",
    }
    rows = [{**base, "question_id": "section"},
            {**base, "question_id": "table", "retrieval_target": "table", "gold_type": "table",
             "gold_table_ids": ["t1"], "human_review_status": "needs_review"}]
    gold = tmp_path / "gold.jsonl"
    gold.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    output = tmp_path / "predictions.jsonl"

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def execute(self, *_): return Result()

    monkeypatch.setattr(runner, "connect", lambda _: Connection())
    monkeypatch.setattr(runner, "ClovaEmbeddingV2", lambda _: DeterministicTestEmbedding(4))
    monkeypatch.setattr(runner, "search_vector_sections", lambda *_, **__: [{"section_id": 1, "receipt_no": "r1"}])
    metrics = runner.run(SimpleNamespace(
        gold=gold, output=output, api_key="test", database_url="test", condition="cold",
        warm_repeats=1, top_k=10,
    ))
    predictions = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert metrics == {"questions": 2, "excluded_table_only": 1}
    assert predictions[0]["section_results"][0]["section_id"] == 1
    assert predictions[1]["error"] == "excluded_table_only_target"


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL required")
def test_live_vector_migration_objects_exist():
    from src.db.load_postgres import connect

    with connect(os.environ["DATABASE_URL"]) as connection:
        extension = connection.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
        tables = connection.execute("""
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema='public' AND table_name=ANY(%s)
        """, (["section_embedding_vectors", "section_vector_chunks", "section_chunk_embeddings"],)).fetchone()[0]
    assert extension and tables == 3


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL required")
def test_live_exact_vector_search_rolls_back_fixture():
    from src.db.load_postgres import connect

    provider = DeterministicTestEmbedding(4)
    query = "연구개발 분야"
    result = provider.embed([query])[0]
    literal = "[" + ",".join(str(value) for value in result.vector) + "]"

    class RollbackFixture(Exception):
        pass

    with pytest.raises(RollbackFixture):
        with connect(os.environ["DATABASE_URL"]) as connection:
            source = connection.execute("""
                SELECT section_id,receipt_no,file_id,parent_section_id,section_order,section_path,source_locator
                FROM document_sections WHERE metadata->>'loader_namespace'='periodic-full-v1'
                  AND metadata->>'section_kind'='narrative' ORDER BY section_id LIMIT 1
            """).fetchone()
            embedding_id = connection.execute("""
                INSERT INTO section_embedding_vectors(
                  content_hash,embedding_model,embedding_version,embedding_dimension,embedding)
                VALUES (%s,%s,%s,4,%s::vector) RETURNING embedding_id
            """, ("f" * 64, provider.spec.model, provider.spec.version, literal)).fetchone()[0]
            chunk_id = connection.execute("""
                INSERT INTO section_vector_chunks(
                  section_id,receipt_no,file_id,parent_section_id,section_order,section_path,
                  source_locator,chunk_order,chunk_text,content_hash,chunk_policy,loader_namespace)
                VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s,'pytest-vector-v1','pytest-vector-v1')
                RETURNING vector_chunk_id
            """, (*source, query, "f" * 64)).fetchone()[0]
            connection.execute("""
                INSERT INTO section_chunk_embeddings(vector_chunk_id,embedding_id,embedding_model,embedding_version)
                VALUES (%s,%s,%s,%s)
            """, (chunk_id, embedding_id, provider.spec.model, provider.spec.version))
            found = search_vector_sections(
                connection, query, provider, corpus_namespace="pytest-vector-v1", top_k=1,
            )
            assert found[0]["section_id"] == source[0]
            assert found[0]["score"] == pytest.approx(1.0)
            raise RollbackFixture

    with connect(os.environ["DATABASE_URL"]) as connection:
        assert connection.execute("SELECT count(*) FROM section_vector_chunks WHERE loader_namespace='pytest-vector-v1'").fetchone()[0] == 0
