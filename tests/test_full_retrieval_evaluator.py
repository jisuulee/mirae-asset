from __future__ import annotations

import copy

import pytest

from eval.evaluate_full_retrieval import deduplicate_receipts, evaluate, validate_benchmark
from src.retrieval.tables import search_tables


def row(**overrides):
    item = {
        "question_id": "q1", "benchmark_version": "periodic-full-retrieval-v1",
        "category": "narrative_description", "question": "사업의 특징을 설명한 곳은?",
        "difficulty": "hard", "retrieval_target": "section",
        "company_filter_required": False, "company": "테스트",
        "year_filter_required": False, "year": None, "date_from": None, "date_to": None,
        "report_type_filter": None, "gold_receipt_nos": ["r1", "r2"],
        "gold_section_ids": [11, 12], "gold_table_ids": [],
        "gold_source_locators": ["xpath:/a", "xpath:/b"], "gold_type": "section",
        "gold_provenance": [
            {"receipt_no": "r1", "file_id": 1, "source_sha256": "a" * 64,
             "source_locator": "xpath:/a", "section_order": 1, "section_key": "r1:1", "section_path": ["A"]},
            {"receipt_no": "r2", "file_id": 2, "source_sha256": "b" * 64,
             "source_locator": "xpath:/b", "section_order": 2, "section_key": "r2:2", "section_path": ["B"]},
        ],
        "human_review_status": "reviewed", "review_notes": "",
        "loader_namespace": "periodic-full-v1", "corpus_snapshot": "snapshot", "split": "development",
        "source_type": "xml",
    }
    item.update(overrides)
    return item


def test_benchmark_schema_and_stable_provenance():
    validate_benchmark([row()])
    broken = row()
    del broken["gold_provenance"][0]["section_key"]
    with pytest.raises(ValueError, match="stable provenance"):
        validate_benchmark([broken])


def test_multi_gold_receipt_dedup_and_metrics():
    prediction = {"question_id": "q1", "latency_ms": 10, "section_results": [
        {"section_id": 99, "receipt_no": "noise"},
        {"section_id": 11, "receipt_no": "r1"},
        {"section_id": 98, "receipt_no": "r1"},
        {"section_id": 12, "receipt_no": "r2"},
    ]}
    report = evaluate([row()], [prediction])
    assert deduplicate_receipts(prediction["section_results"]) == ["noise", "r1", "r2"]
    assert report["overall"]["section"]["recall_at_3"] == 1.0
    assert report["overall"]["section"]["relevant_set_coverage_at_10"] == 1.0
    assert report["overall"]["receipt"]["recall_at_3"] == 1.0
    assert report["overall"]["section"]["map_at_10"] > 0
    assert report["overall"]["section"]["ndcg_at_10"] > 0


def test_category_difficulty_review_and_missing_prediction_aggregation():
    second = row(question_id="q2", category="exact_phrase", difficulty="easy",
                 human_review_status="ambiguous", split="held_out")
    report = evaluate([row(), second], [])
    assert report["prediction_count"] == 0
    assert report["by_category"]["exact_phrase"]["question_count"] == 1
    assert report["by_difficulty"]["hard"]["question_count"] == 1
    assert report["reviewed_only"]["question_count"] == 1
    assert len(report["failures"]) == 2
    assert all(failure["error"] == "missing_prediction" for failure in report["failures"])


def test_table_target_and_empty_result():
    gold = row(retrieval_target="table", gold_type="table", gold_section_ids=[11], gold_table_ids=["t1"])
    hit = evaluate([gold], [{"question_id": "q1", "table_results": [{"table_id": "t1", "receipt_no": "r1"}]}])
    assert hit["overall"]["table"]["top1"] == 1.0
    assert hit["overall"]["section"]["question_count"] == 0
    miss = evaluate([gold], [{"question_id": "q1", "table_results": []}])
    assert miss["overall"]["table"]["top1"] == 0.0
    assert miss["failure_taxonomy"] == {"table_target": 1}


def test_pdf_page_target_policy():
    pdf = row(retrieval_target="pdf_page", gold_type="pdf_page", source_type="pdf",
              gold_source_locators=["page:7"], gold_section_ids=[77],
              gold_provenance=[{"receipt_no": "r1", "source_locator": "page:7",
                                "section_order": 7, "section_key": "r1:page:7"}])
    validate_benchmark([pdf])
    invalid = copy.deepcopy(pdf)
    invalid["gold_source_locators"] = ["xpath:/page"]
    with pytest.raises(ValueError, match="page locators"):
        validate_benchmark([invalid])


class Cursor:
    description = []
    def __init__(self): self.sql = ""; self.params = ()
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def execute(self, sql, params): self.sql = sql; self.params = params
    def fetchall(self): return []


class Connection:
    def __init__(self): self.last = None
    def cursor(self): self.last = Cursor(); return self.last


def test_table_namespace_and_runtime_modes():
    for mode in ("fts", "trigram", "combined"):
        connection = Connection()
        assert search_tables(connection, "매출 현황", mode=mode, corpus_namespace="periodic-full-v1") == []
        assert "t.metadata->>'loader_namespace'=%s" in connection.last.sql
        assert "periodic-full-v1" in connection.last.params
        if mode != "fts":
            assert " %% %s" in connection.last.sql
    with pytest.raises(ValueError, match="mode"):
        search_tables(Connection(), "query", mode="vector")