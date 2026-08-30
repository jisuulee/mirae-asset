"""Section chunk preparation, idempotent embedding load, and vector-only search."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from .embeddings import EmbeddingProvider, embedding_content_hash, validate_embedding
from .sections import _rows
from .text_normalization import canonical_report_type, normalize_company_query, normalize_search_text

MIGRATION_VERSION = "section-paragraph-v1"
PARAGRAPH_BREAK = re.compile(r"\n\s*\n+")


@dataclass(frozen=True)
class SectionChunk:
    section_id: int
    receipt_no: str
    file_id: int
    parent_section_id: int | None
    section_order: int
    section_path: str | None
    source_locator: str
    chunk_order: int
    text: str
    content_hash: str
    loader_namespace: str
    metadata: dict[str, Any]


def _window(text: str, size: int, overlap: int) -> Iterable[tuple[int, int, str]]:
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        yield start, end, text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def chunk_section(
    section: dict[str, Any], *, long_threshold: int = 8000, target_chars: int = 6000, overlap_chars: int = 200
) -> list[SectionChunk]:
    if section.get("section_kind") != "narrative":
        return []
    if target_chars <= 0 or not 0 <= overlap_chars < target_chars:
        raise ValueError("invalid chunk size/overlap")
    title = (section.get("title") or "").strip()
    path = (section.get("section_path") or "").strip()
    body = (section.get("body_text") or "").strip()
    prefix = "\n".join(value for value in (path, title) if value)
    source = "\n\n".join(value for value in (prefix, body) if value)
    if not source:
        return []

    pieces: list[tuple[int, int, str]] = []
    if len(source) <= long_threshold:
        pieces = [(0, len(source), source)]
    else:
        paragraphs = [part.strip() for part in PARAGRAPH_BREAK.split(source) if part.strip()]
        buffer = ""
        offset = 0
        for paragraph in paragraphs:
            candidate = paragraph if not buffer else buffer + "\n\n" + paragraph
            if len(candidate) <= target_chars:
                buffer = candidate
                continue
            if buffer:
                pieces.append((offset, offset + len(buffer), buffer))
                offset += max(1, len(buffer) - overlap_chars)
                buffer = ""
            if len(paragraph) > target_chars:
                for start, end, part in _window(paragraph, target_chars, overlap_chars):
                    pieces.append((offset + start, offset + end, part))
                offset += len(paragraph) + 2
            else:
                buffer = paragraph
        if buffer:
            pieces.append((offset, offset + len(buffer), buffer))

    chunks = []
    for order, (start, end, text) in enumerate(pieces):
        chunks.append(SectionChunk(
            section_id=int(section["section_id"]), receipt_no=str(section["receipt_no"]),
            file_id=int(section["file_id"]), parent_section_id=section.get("parent_section_id"),
            section_order=int(section["section_order"]), section_path=section.get("section_path"),
            source_locator=str(section["source_locator"]), chunk_order=order, text=text,
            content_hash=embedding_content_hash(text), loader_namespace=str(section["loader_namespace"]),
            metadata={"char_start": start, "char_end": end, "section_kind": "narrative",
                      "source_type": section.get("source_type"), "title": section.get("title")},
        ))
    return chunks


def fetch_embedding_sections(connection: Any, loader_namespace: str) -> list[dict[str, Any]]:
    rows = connection.execute("""
        SELECT s.section_id,s.receipt_no,s.file_id,s.parent_section_id,s.section_order,
               s.section_path,s.title_normalized,s.body_text,s.source_locator,
               s.metadata->>'loader_namespace',s.metadata->>'section_kind',s.metadata->>'source_type'
        FROM document_sections s
        WHERE s.metadata->>'loader_namespace'=%s AND s.metadata->>'section_kind'='narrative'
        ORDER BY s.section_id
    """, (loader_namespace,)).fetchall()
    names = ("section_id", "receipt_no", "file_id", "parent_section_id", "section_order",
             "section_path", "title", "body_text", "source_locator", "loader_namespace",
             "section_kind", "source_type")
    return [dict(zip(names, row)) for row in rows]


def prepare_chunks(connection: Any, loader_namespace: str) -> list[SectionChunk]:
    return [chunk for section in fetch_embedding_sections(connection, loader_namespace) for chunk in chunk_section(section)]


def _vector_literal(values: Iterable[float]) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"


def _checkpoint(connection: Any) -> None:
    commit = getattr(connection, "commit", None)
    if commit:
        commit()


def load_section_embeddings(
    connection: Any,
    provider: EmbeddingProvider,
    *,
    loader_namespace: str,
    batch_size: int = 16,
    chunks: list[SectionChunk] | None = None,
) -> dict[str, int]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    chunks = chunks if chunks is not None else prepare_chunks(connection, loader_namespace)
    hashes = sorted({chunk.content_hash for chunk in chunks})
    cached_rows = connection.execute("""
        SELECT content_hash,embedding_id,embedding_dimension FROM section_embedding_vectors
        WHERE embedding_model=%s AND embedding_version=%s AND content_hash=ANY(%s)
    """, (provider.spec.model, provider.spec.version, hashes)).fetchall()
    if any(int(dimension) != provider.spec.dimension for _, _, dimension in cached_rows):
        raise ValueError("cached embedding dimension does not match provider spec")
    embedding_ids = {str(content_hash).strip(): int(embedding_id) for content_hash, embedding_id, _ in cached_rows}
    text_by_hash = {chunk.content_hash: chunk.text for chunk in chunks}
    misses = [content_hash for content_hash in hashes if content_hash not in embedding_ids]
    embedded = 0
    input_tokens = 0
    for start in range(0, len(misses), batch_size):
        batch_hashes = misses[start:start + batch_size]
        results = provider.embed([text_by_hash[value] for value in batch_hashes])
        if len(results) != len(batch_hashes):
            raise ValueError("provider returned a different embedding count")
        for content_hash, result in zip(batch_hashes, results):
            validate_embedding(result, provider.spec)
            embedding_id = connection.execute("""
                INSERT INTO section_embedding_vectors(
                  content_hash,embedding_model,embedding_version,embedding_dimension,embedding,input_tokens)
                VALUES (%s,%s,%s,%s,%s::vector,%s)
                ON CONFLICT (content_hash,embedding_model,embedding_version) DO UPDATE
                  SET content_hash=excluded.content_hash
                RETURNING embedding_id
            """, (content_hash, provider.spec.model, provider.spec.version, provider.spec.dimension,
                  _vector_literal(result.vector), result.input_tokens)).fetchone()[0]
            embedding_ids[content_hash] = int(embedding_id)
            embedded += 1
            input_tokens += result.input_tokens or 0
        _checkpoint(connection)

    for chunk_index, chunk in enumerate(chunks, start=1):
        vector_chunk_id = connection.execute("""
            INSERT INTO section_vector_chunks(
              section_id,receipt_no,file_id,parent_section_id,section_order,section_path,
              source_locator,chunk_order,chunk_text,content_hash,chunk_policy,loader_namespace,metadata)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (section_id,chunk_order,chunk_policy,loader_namespace) DO UPDATE SET
              receipt_no=excluded.receipt_no,file_id=excluded.file_id,parent_section_id=excluded.parent_section_id,
              section_order=excluded.section_order,section_path=excluded.section_path,
              source_locator=excluded.source_locator,chunk_text=excluded.chunk_text,
              content_hash=excluded.content_hash,metadata=excluded.metadata,updated_at=now()
            RETURNING vector_chunk_id
        """, (chunk.section_id, chunk.receipt_no, chunk.file_id, chunk.parent_section_id,
              chunk.section_order, chunk.section_path, chunk.source_locator, chunk.chunk_order,
              chunk.text, chunk.content_hash, MIGRATION_VERSION, loader_namespace,
              json.dumps(chunk.metadata, ensure_ascii=False))).fetchone()[0]
        connection.execute("""
            INSERT INTO section_chunk_embeddings(
              vector_chunk_id,embedding_id,embedding_model,embedding_version)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (vector_chunk_id,embedding_model,embedding_version) DO UPDATE
              SET embedding_id=excluded.embedding_id
        """, (vector_chunk_id, embedding_ids[chunk.content_hash], provider.spec.model, provider.spec.version))
        if chunk_index % 500 == 0:
            _checkpoint(connection)
    _checkpoint(connection)
    return {"sections": len({chunk.section_id for chunk in chunks}), "chunks": len(chunks),
            "unique_texts": len(hashes), "embedded": embedded, "reused": len(hashes) - embedded,
            "input_tokens": input_tokens}


def search_vector_sections(
    connection: Any,
    query: str,
    provider: EmbeddingProvider,
    *,
    top_k: int = 5,
    company: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    report_type: str | None = None,
    receipt_no: str | None = None,
    corpus_namespace: str = "periodic-full-v1",
) -> list[dict[str, Any]]:
    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be 1..100")
    normalized = normalize_search_text(query)
    if not normalized:
        return []
    embedded = provider.embed([normalized])[0]
    validate_embedding(embedded, provider.spec)
    clauses = ["c.loader_namespace=%s", "e.embedding_model=%s", "e.embedding_version=%s",
               "e.embedding_dimension=%s"]
    params: list[Any] = [corpus_namespace, provider.spec.model, provider.spec.version, provider.spec.dimension]
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
    query_vector = _vector_literal(embedded.vector)
    sql = f"""
      WITH chunk_candidates AS (
        SELECT c.section_id,c.vector_chunk_id,c.chunk_order,c.receipt_no,co.company_name,
               d.report_name,d.receipt_date,s.title_normalized AS title,c.section_path,
               left(c.chunk_text,500) AS body_snippet,
               e.embedding <=> %s::vector AS distance,
               f.relative_path AS source_file,c.source_locator,
               jsonb_build_object('chunk',c.metadata,'section',s.metadata->'provenance') AS provenance,
               f.file_role
        FROM section_vector_chunks c
        JOIN section_chunk_embeddings ce USING(vector_chunk_id)
        JOIN section_embedding_vectors e USING(embedding_id)
        JOIN document_sections s USING(section_id)
        JOIN disclosures d ON d.receipt_no=c.receipt_no
        JOIN companies co ON co.company_id=d.company_id
        JOIN disclosure_files f ON f.file_id=c.file_id
        WHERE {' AND '.join(clauses)}
      ), section_best AS (
        SELECT DISTINCT ON (section_id) * FROM chunk_candidates
        ORDER BY section_id,distance,chunk_order
      )
      SELECT section_id,vector_chunk_id,chunk_order,receipt_no,company_name,
             report_name,receipt_date,title,section_path,body_snippet,
             1-distance AS score,'vector' AS retrieval_method,source_file,
             source_locator,provenance,file_role
      FROM section_best
      ORDER BY distance,section_id
      LIMIT %s
    """
    final_params = [query_vector] + params + [top_k]
    with connection.cursor() as cursor:
        cursor.execute(sql, tuple(final_params))
        return _rows(cursor)
