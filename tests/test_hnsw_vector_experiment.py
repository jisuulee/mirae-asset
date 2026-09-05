import json
from pathlib import Path

from eval.evaluate_hnsw_vector_experiment import build, latency
from eval.evaluate_full_retrieval import read_jsonl


def test_hnsw_migration_is_fixed_and_non_destructive():
    sql = Path("src/db/migrations/006_section_embedding_hnsw.sql").read_text(encoding="utf-8")
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert "WITH (m = 16, ef_construction = 64)" in sql
    assert "section_vector_hnsw_projection" in sql
    assert "UPDATE document_sections" not in sql
    assert "DELETE FROM section_embedding_vectors" not in sql


def test_experiment_artifact_and_exact_reference_metrics():
    raw = json.loads(Path("artifacts/periodic_full_hnsw_experiment.json").read_text(encoding="utf-8"))
    assert raw["experiment"]["ef_search"] == [100, 200, 400]
    assert len(raw["rows"]) == 120
    assert sum(row.get("excluded") == "table_only_target" for row in raw["rows"]) == 7
    report = build(read_jsonl(Path("eval/periodic_full_benchmark.jsonl")), raw)
    assert report["evaluations"]["exact"]["overall"]["section"]["question_count"] == 113
    assert all(0 <= value["ann_recall_at_10_vs_exact"] <= 1 for value in report["ann"].values())
    assert report["benchmark_policy"]["gold_changed"] is False


def test_latency_percentiles_are_nearest_rank():
    result = latency([1, 2, 3, 100])
    assert result == {"count": 4, "average_ms": 26.5, "p50_ms": 2, "p95_ms": 100, "max_ms": 100}
