"""Separate keyword interface for bounded retrieval table projections."""

from __future__ import annotations

from typing import Any, Literal

from .sections import _rows
from .text_normalization import normalize_search_text


def search_tables(
    connection: Any,
    query: str,
    *,
    top_k: int = 5,
    mode: Literal["fts", "trigram", "combined"] = "combined",
    corpus_namespace: str = "periodic-pilot-v1",
) -> list[dict[str, Any]]:
    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be 1..100")
    normalized = normalize_search_text(query)
    if not normalized:
        return []
    if mode == "fts":
        score = "ts_rank_cd(t.search_tsv, websearch_to_tsquery('simple', %s))"
        score_params = [normalized]
        predicate = "t.search_tsv @@ websearch_to_tsquery('simple', %s)"
        predicate_params = [normalized]
    elif mode == "trigram":
        score = "similarity(lower(t.searchable_text), %s)"
        score_params = [normalized]
        predicate = "lower(t.searchable_text) %% %s"
        predicate_params = [normalized]
    elif mode == "combined":
        score = "(0.7 * ts_rank_cd(t.search_tsv, websearch_to_tsquery('simple', %s)) + 0.3 * similarity(lower(t.searchable_text), %s))"
        score_params = [normalized, normalized]
        predicate = "(t.search_tsv @@ websearch_to_tsquery('simple', %s) OR lower(t.searchable_text) %% %s)"
        predicate_params = [normalized, normalized]
    else:
        raise ValueError("mode must be fts, trigram, or combined")
    sql = f"""
      SELECT t.table_id,t.receipt_no,s.section_id,s.title_normalized AS title,
             left(t.searchable_text,500) AS searchable_snippet,{score} AS score,
             %s AS retrieval_method,t.source_locator,t.provenance,t.archive_path,
             t.archive_identifier,f.relative_path AS source_file,f.file_role
      FROM retrieval_tables t JOIN document_sections s USING(section_id)
      JOIN disclosure_files f ON f.file_id=t.file_id
      WHERE t.metadata->>'loader_namespace'=%s AND {predicate}
      ORDER BY score DESC,(f.file_role='main') DESC,t.retrieval_table_id
      LIMIT %s
    """
    params = score_params + [mode, corpus_namespace] + predicate_params + [top_k]
    with connection.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        return _rows(cursor)
