from __future__ import annotations

from src.retrieval.sections import KeywordWeights, search_sections
from src.retrieval.text_normalization import canonical_report_type, normalize_company_query, normalize_search_text


class FakeCursor:
    description = [("section_id",), ("receipt_no",), ("score",)]

    def __init__(self):
        self.sql = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.sql, self.params = sql, params

    def fetchall(self):
        return [(1, "12345678901234", 0.8)]


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def cursor(self):
        return self.cursor_instance


def test_nfkc_zero_width_whitespace_and_casefold():
    assert normalize_search_text("Ａ  B\u200b\tC\u3000") == "a b c"
    assert normalize_company_query("  A\u00a0 B ") == "ab"


def test_empty_query_returns_no_rows_and_weights_are_configurable():
    connection = FakeConnection()
    assert search_sections(connection, "  ") == []
    assert KeywordWeights(fts=0.8, trigram=0.1, title_bonus=0.1).fts == 0.8


def test_combined_sql_has_fts_trigram_and_provenance_projection():
    connection = FakeConnection()
    rows = search_sections(connection, "Revenue", top_k=3, mode="combined")
    assert rows[0]["section_id"] == 1
    assert "search_tsv" in connection.cursor_instance.sql
    assert "similarity" in connection.cursor_instance.sql
    assert "provenance" in connection.cursor_instance.sql
    assert connection.cursor_instance.params[-1] == 3


def test_top_k_validation():
    try:
        search_sections(FakeConnection(), "x", top_k=0)
    except ValueError as exc:
        assert "top_k" in str(exc)
    else:
        raise AssertionError("expected top_k validation")


def test_report_type_canonicalization():
    assert canonical_report_type("\uc0ac\uc5c5\ubcf4\uace0\uc11c") == "annual"
    assert canonical_report_type("\ubc18\uae30\ubcf4\uace0\uc11c") == "half"
    assert canonical_report_type("\ubd84\uae30\ubcf4\uace0\uc11c") == "quarter"
