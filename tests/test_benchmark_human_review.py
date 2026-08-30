from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eval.apply_benchmark_review import proposed_changes, validate_accepted_gold, validate_review_export
from eval.build_benchmark_human_review import REVIEW_DECISIONS, REVIEW_FIELDS
from eval.evaluate_full_retrieval import read_jsonl
from eval.validate_full_benchmark import validate_database


BENCHMARK = Path("eval/periodic_full_benchmark.jsonl")
TEMPLATE = Path("eval/periodic_full_benchmark_review.jsonl")
DATA = Path("reports/human_review/review_data.json")
HTML = Path("reports/human_review/index.html")


def benchmark_rows():
    return read_jsonl(BENCHMARK)


def review_rows():
    return read_jsonl(TEMPLATE)


def test_exactly_84_unique_nonreviewed_review_rows():
    rows = review_rows()
    assert len(rows) == 84
    assert len({row["question_id"] for row in rows}) == 84
    assert {row["human_review_status"] for row in rows} == {"needs_review", "ambiguous"}
    assert sum(row["human_review_status"] == "needs_review" for row in rows) == 74
    assert sum(row["human_review_status"] == "ambiguous" for row in rows) == 10


def test_reviewed_36_are_excluded_and_original_fields_are_unchanged():
    originals = {row["question_id"]: row for row in benchmark_rows()}
    reviews = review_rows()
    assert not ({row["question_id"] for row in reviews}
                & {qid for qid, row in originals.items() if row["human_review_status"] == "reviewed"})
    for review in reviews:
        original = originals[review["question_id"]]
        for key, value in original.items():
            assert review[key] == value
    data = json.loads(DATA.read_text(encoding="utf-8"))
    assert data["benchmark_sha256"] == hashlib.sha256(BENCHMARK.read_bytes()).hexdigest()


def test_review_decision_enum_and_blank_export_schema():
    assert set(REVIEW_DECISIONS) == {
        "ACCEPT", "ACCEPT_MULTI_GOLD", "CHANGE_GOLD", "REWRITE_QUESTION",
        "CHANGE_TARGET", "AMBIGUOUS_KEEP", "DROP",
    }
    for row in review_rows():
        for field, default in REVIEW_FIELDS.items():
            assert row[field] == default
    result = validate_review_export(review_rows(), benchmark_rows())
    assert result["rows"] == result["unique"] == result["pending"] == 84


def test_unknown_duplicate_and_invalid_gold_are_rejected():
    benchmark = benchmark_rows()
    base = {"question_id": review_rows()[0]["question_id"], "review_decision": "ACCEPT",
            "accepted_gold_receipts": [], "accepted_gold_sections": [],
            "accepted_gold_tables": [], "accepted_pdf_pages": []}
    with pytest.raises(ValueError, match="unknown"):
        validate_review_export([{**base, "question_id": "does-not-exist"}], benchmark)
    with pytest.raises(ValueError, match="duplicate"):
        validate_review_export([base, dict(base)], benchmark)
    with pytest.raises(ValueError, match="non-negative integers"):
        validate_review_export([{**base, "accepted_gold_sections": ["not-an-id"]}], benchmark)
    with pytest.raises(ValueError, match="accepted gold is required"):
        validate_review_export([{**base, "review_decision": "CHANGE_GOLD"}], benchmark)


def test_apply_tool_produces_dry_run_changes_without_mutating_source():
    benchmark = benchmark_rows()
    original = BENCHMARK.read_bytes()
    qid = review_rows()[0]["question_id"]
    reviews = [{"question_id": qid, "review_decision": "REWRITE_QUESTION",
                "proposed_question": "검수자가 제안한 질문", "accepted_gold_receipts": [],
                "accepted_gold_sections": [], "accepted_gold_tables": [], "accepted_pdf_pages": []}]
    validate_review_export(reviews, benchmark)
    changes = proposed_changes(reviews, benchmark)
    assert changes[0]["question"]["to"] == "검수자가 제안한 질문"
    assert BENCHMARK.read_bytes() == original
    source = Path("eval/apply_benchmark_review.py").read_text(encoding="utf-8")
    assert 'parser.add_argument("--apply", action="store_true")' in source
    assert "the source benchmark is never overwritten implicitly" in source


def test_html_contains_all_cards_controls_and_leakage_warning():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    assert len(data["cards"]) == 84
    assert all(card["gold_evidence"]["sections"] or card["gold_evidence"]["tables"] for card in data["cards"])
    assert all("retrieval_diagnostic" in card and "alternative_evidence" in card for card in data["cards"])
    html = HTML.read_text(encoding="utf-8")
    for marker in ("Previous", "Next", "All categories", "All difficulties", "question_id search",
                   "localStorage", "Export review JSON", "DO NOT USE RETRIEVAL RESULT AS GOLD TRUTH"):
        assert marker in html


@pytest.mark.skipif(
    not shutil.which("node") or not Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists(),
    reason="Node and Chrome required for executed-DOM test",
)
@pytest.mark.parametrize("transport", ["file", "http"])
def test_review_ui_executes_and_all_interactions_work_in_chrome(transport):
    script = Path("tests/browser_review_ui_check.mjs").resolve()
    html = HTML.resolve()
    server = thread = None
    if transport == "file":
        url = html.as_uri()
    else:
        handler = partial(SimpleHTTPRequestHandler, directory=str(Path.cwd()))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/reports/human_review/index.html"
    try:
        result = subprocess.run(["node", str(script), url], check=True, capture_output=True,
                                text=True, encoding="utf-8", timeout=30)
        payload = json.loads(result.stdout)
        assert payload["initial"]["cards"] == 84
        assert payload["exportCount"] == 84
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=2)


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL required")
def test_84_stable_provenance_is_current_in_live_database():
    from src.db.load_postgres import connect

    with connect(os.environ["DATABASE_URL"]) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        result = validate_database(connection, [row for row in benchmark_rows()
                                                if row["human_review_status"] in {"needs_review", "ambiguous"}])
    assert result["questions"] == 84
    assert result["sections_checked"] == 113
    assert result["tables_checked"] == 7
    assert result["mismatch_count"] == 0


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL required")
def test_nonexistent_accepted_gold_is_rejected_by_live_database():
    from src.db.load_postgres import connect

    row = review_rows()[0]
    invalid = {"question_id": row["question_id"], "accepted_gold_sections": [9_999_999_999],
               "accepted_gold_receipts": [], "accepted_gold_tables": [], "accepted_pdf_pages": []}
    with connect(os.environ["DATABASE_URL"]) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        with pytest.raises(ValueError, match="does not exist"):
            validate_accepted_gold(connection, [invalid], benchmark_rows())
