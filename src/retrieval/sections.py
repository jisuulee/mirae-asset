"""PostgreSQL FTS, trigram, and explainable combined section search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from .text_normalization import canonical_report_type, normalize_company_query, normalize_search_text


@dataclass(frozen=True)
class KeywordWeights:
    fts: float = 0.60
    trigram: float = 0.30
    title_bonus: float = 0.10


def _rows(cursor: Any) -> list[dict[str, Any]]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _where(
    query: str,
    *,
    company: str | None,
    date_from: date | None,
    date_to: date | None,
    report_type: str | None,
    section_title: str | None,
    receipt_no: str | None,
    corpus_namespace: str,
) -> tuple[list[str], list[Any]]:
    clauses = ["s.metadata->>'loader_namespace' = %s"]
    params: list[Any] = [corpus_namespace]
    if company:
        clauses.append("co.company_name_normalized = %s")
        params.append(normalize_company_query(company))
    if date_from:
        clauses.append("d.receipt_date >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("d.receipt_date <= %s")
        params.append(date_to)
    if report_type:
        clauses.append("d.report_type = %s")
        params.append(canonical_report_type(report_type))
    if section_title:
        clauses.append("s.title_normalized ILIKE '%%' || %s || '%%'")
        params.append(normalize_search_text(section_title))
    if receipt_no:
        clauses.append("s.receipt_no = %s")
        params.append(receipt_no)
    return clauses, params


def search_sections(
    connection: Any,
    query: str,
    *,
    company: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    report_type: str | None = None,
    section_title: str | None = None,
    receipt_no: str | None = None,
    corpus_namespace: str = "periodic-pilot-v1",
    top_k: int = 5,
    mode: Literal["fts", "trigram", "combined"] = "combined",
    weights: KeywordWeights = KeywordWeights(),
) -> list[dict[str, Any]]:
    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be 1..100")
    query_norm = normalize_search_text(query)
    if not query_norm:
        return []
    if mode not in {"fts", "trigram", "combined"}:
        raise ValueError("mode must be fts, trigram, or combined")
    clauses, params = _where(query_norm, company=company, date_from=date_from, date_to=date_to, report_type=report_type, section_title=section_title, receipt_no=receipt_no, corpus_namespace=corpus_namespace)
    # websearch_to_tsquery is parameterized; trigram score remains useful for
    # Korean partial strings and spacing variations.
    score = {
        "fts": "ts_rank_cd(s.search_tsv, websearch_to_tsquery('simple', %s))",
        "trigram": "greatest(similarity(s.search_text_normalized, %s), similarity(s.search_text_nospace, %s))",
        "combined": "(%s * ts_rank_cd(s.search_tsv, websearch_to_tsquery('simple', %s)) + %s * greatest(similarity(s.search_text_normalized, %s), similarity(s.search_text_nospace, %s)) + %s * similarity(lower(coalesce(s.title_normalized,'')), %s))",
    }[mode]
    if mode == "fts":
        score_params = [query_norm]
        clauses.append("s.search_tsv @@ websearch_to_tsquery('simple', %s)")
        params.append(query_norm)
    elif mode == "trigram":
        score_params = [query_norm, normalize_search_text(query_norm, remove_spaces=True)]
        clauses.append("(s.title_normalized %% %s OR s.search_text_normalized %% %s OR s.search_text_nospace %% %s)")
        params.extend((query_norm, query_norm, normalize_search_text(query_norm, remove_spaces=True)))
    else:
        score_params = [weights.fts, query_norm, weights.trigram, query_norm, normalize_search_text(query_norm, remove_spaces=True), weights.title_bonus, query_norm]
        clauses.append("(s.search_tsv @@ websearch_to_tsquery('simple', %s) OR s.title_normalized %% %s OR s.search_text_normalized %% %s OR s.search_text_nospace %% %s)")
        params.extend((query_norm, query_norm, query_norm, normalize_search_text(query_norm, remove_spaces=True)))
    sql = f"""
      SELECT s.section_id,s.receipt_no,co.company_name,d.report_name,d.receipt_date,
             s.title_normalized AS title,s.section_path,
             left(s.body_text,500) AS body_snippet,{score} AS score,
             %s AS retrieval_method,f.relative_path AS source_file,
             s.source_locator,s.metadata->'provenance' AS provenance,
             f.file_role
      FROM document_sections s
      JOIN disclosures d USING(receipt_no)
      JOIN companies co ON co.company_id=d.company_id
      JOIN disclosure_files f ON f.file_id=s.file_id
      WHERE {' AND '.join(clauses)}
      ORDER BY score DESC, (f.file_role='main') DESC, s.section_id
      LIMIT %s
    """
    # score expressions precede WHERE parameters in PostgreSQL positional binds.
    final_params = score_params + [mode] + params + [top_k]
    with connection.cursor() as cursor:
        cursor.execute(sql, tuple(final_params))
        return _rows(cursor)
