"""Independent exact/HNSW search over already populated section embeddings."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .embeddings import EmbeddingSpec, EmbeddingResult, validate_embedding
from .sections import _rows
from .text_normalization import canonical_report_type, normalize_company_query
from .vector_sections import _vector_literal


FIXED_SPEC = EmbeddingSpec("clova-studio", "clova-studio/bge-m3", "embedding-v2", 1024, "cosine", 8192)
HNSW_INDEX = "section_vector_hnsw_cosine_idx"


def _filters(*, company: str | None, date_from: date | None, date_to: date | None,
             report_type: str | None, receipt_no: str | None, corpus_namespace: str) -> tuple[list[str], list[Any]]:
    clauses = ["c.loader_namespace=%s", "e.embedding_model=%s", "e.embedding_version=%s",
               "e.embedding_dimension=%s"]
    params: list[Any] = [corpus_namespace, FIXED_SPEC.model, FIXED_SPEC.version, FIXED_SPEC.dimension]
    if company:
        clauses.append("co.company_name_normalized=%s")
        params.append(normalize_company_query(company))
    if date_from:
        clauses.append("d.receipt_date >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("d.receipt_date <= %s")
        params.append(date_to)
    if report_type:
        clauses.append("d.report_type=%s")
        params.append(canonical_report_type(report_type))
    if receipt_no:
        clauses.append("c.receipt_no=%s")
        params.append(receipt_no)
    return clauses, params


def search_sections_by_vector(
    connection: Any,
    query_vector: Iterable[float],
    *,
    mode: str,
    top_k: int = 10,
    ef_search: int = 40,
    candidate_k: int = 100,
    company: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    report_type: str | None = None,
    receipt_no: str | None = None,
    corpus_namespace: str = "periodic-full-v1",
) -> list[dict[str, Any]]:
    if mode not in {"exact", "hnsw"}:
        raise ValueError("mode must be exact or hnsw")
    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be 1..100")
    if ef_search < 1 or candidate_k < top_k:
        raise ValueError("invalid HNSW search parameters")
    values = tuple(float(value) for value in query_vector)
    validate_embedding(EmbeddingResult(values), FIXED_SPEC)
    literal = _vector_literal(values)
    clauses, params = _filters(company=company, date_from=date_from, date_to=date_to,
                               report_type=report_type, receipt_no=receipt_no,
                               corpus_namespace=corpus_namespace)
    common_select = """
        SELECT c.section_id,c.vector_chunk_id,c.chunk_order,c.receipt_no,co.company_name,
               d.report_name,d.receipt_date,s.title_normalized AS title,c.section_path,
               left(c.chunk_text,500) AS body_snippet,
               (e.embedding::vector(1024)) <=> %s::vector(1024) AS distance,
               f.relative_path AS source_file,c.source_locator,
               jsonb_build_object('chunk',c.metadata,'section',s.metadata->'provenance') AS provenance,
               f.file_role
        FROM section_embedding_vectors e
        JOIN section_chunk_embeddings ce USING(embedding_id)
        JOIN section_vector_chunks c USING(vector_chunk_id)
        JOIN document_sections s USING(section_id)
        JOIN disclosures d ON d.receipt_no=c.receipt_no
        JOIN companies co ON co.company_id=d.company_id
        JOIN disclosure_files f ON f.file_id=c.file_id
    """
    if mode == "hnsw":
        connection.execute(f"SET LOCAL hnsw.ef_search={int(ef_search)}")
        connection.execute("SET LOCAL hnsw.iterative_scan='strict_order'")
        # With selective metadata predicates PostgreSQL prefers a btree scan plus
        # exact sort.  Run the KNN stage separately and disable Sort only for that
        # statement so this mode measures the HNSW access path consistently.
        connection.execute("SET LOCAL enable_sort=off")
        hnsw_clauses = ["h.loader_namespace=%s", "h.embedding_model=%s", "h.embedding_version=%s"]
        hnsw_params: list[Any] = [corpus_namespace, FIXED_SPEC.model, FIXED_SPEC.version]
        if company:
            hnsw_clauses.append("h.company_name_normalized=%s")
            hnsw_params.append(normalize_company_query(company))
        if date_from:
            hnsw_clauses.append("h.receipt_date >= %s")
            hnsw_params.append(date_from)
        if date_to:
            hnsw_clauses.append("h.receipt_date <= %s")
            hnsw_params.append(date_to)
        if report_type:
            hnsw_clauses.append("h.report_type=%s")
            hnsw_params.append(canonical_report_type(report_type))
        if receipt_no:
            hnsw_clauses.append("h.receipt_no=%s")
            hnsw_params.append(receipt_no)
        ann_sql = f"""
            SELECT h.vector_chunk_id,h.embedding <=> %s::vector(1024) AS distance
            FROM section_vector_hnsw_projection h
            WHERE {' AND '.join(hnsw_clauses)}
            ORDER BY h.embedding <=> %s::vector(1024)
            LIMIT %s
        """
        try:
            with connection.cursor() as cursor:
                cursor.execute(ann_sql, (literal, *hnsw_params, literal, candidate_k))
                ann_rows = cursor.fetchall()
        finally:
            connection.execute("SET LOCAL enable_sort=on")
        if not ann_rows:
            return []
        values_sql = ",".join(["(%s::bigint,%s::double precision)"] * len(ann_rows))
        candidate_cte = f"""
          ann_ids(vector_chunk_id,distance) AS (VALUES {values_sql}),
          ann_chunks AS MATERIALIZED (
            SELECT c.section_id,c.vector_chunk_id,c.chunk_order,c.receipt_no,co.company_name,
                   d.report_name,d.receipt_date,s.title_normalized AS title,c.section_path,
                   left(c.chunk_text,500) AS body_snippet,a.distance,
                   f.relative_path AS source_file,c.source_locator,
                   jsonb_build_object('chunk',c.metadata,'section',s.metadata->'provenance') AS provenance,
                   f.file_role
            FROM ann_ids a JOIN section_vector_chunks c USING(vector_chunk_id)
            JOIN document_sections s USING(section_id)
            JOIN disclosures d ON d.receipt_no=c.receipt_no
            JOIN companies co ON co.company_id=d.company_id
            JOIN disclosure_files f ON f.file_id=c.file_id
          )
        """
        sql_params = [value for row in ann_rows for value in row]
        source = "ann_chunks"
    else:
        candidate_cte = f"""chunk_candidates AS ({common_select} WHERE {' AND '.join(clauses)})"""
        sql_params = [literal, *params]
        source = "chunk_candidates"
    sql = f"""
      WITH {candidate_cte}, section_best AS (
        SELECT DISTINCT ON (section_id) * FROM {source}
        ORDER BY section_id,distance,chunk_order
      )
      SELECT section_id,vector_chunk_id,chunk_order,receipt_no,company_name,
             report_name,receipt_date,title,section_path,body_snippet,
             1-distance AS score,%s AS retrieval_method,source_file,
             source_locator,provenance,file_role
      FROM section_best ORDER BY distance,section_id LIMIT %s
    """
    sql_params.extend([mode, top_k])
    with connection.cursor() as cursor:
        cursor.execute(sql, tuple(sql_params))
        return _rows(cursor)
