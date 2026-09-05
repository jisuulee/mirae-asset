"""Small real CLOVA Embedding v2 validation before the full load."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.db.load_postgres import connect
from src.retrieval.embeddings import ClovaEmbeddingV2
from src.retrieval.vector_sections import load_section_embeddings, prepare_chunks, search_vector_sections

MIGRATION = Path("src/db/migrations/005_pgvector.sql")


def representative(chunks, size: int):
    unique = {chunk.content_hash: chunk for chunk in chunks}
    ordered = sorted(unique.values(), key=lambda chunk: (len(chunk.text), chunk.section_id, chunk.chunk_order))
    picks = []
    for index in range(size):
        position = round(index * (len(ordered) - 1) / max(1, size - 1))
        picks.append(ordered[position])
    pdf = next((chunk for chunk in ordered if chunk.metadata.get("source_type") == "pdf"), None)
    if pdf and pdf.content_hash not in {chunk.content_hash for chunk in picks}:
        picks[-1] = pdf
    return list({chunk.content_hash: chunk for chunk in picks}.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--api-key", default=os.getenv("CLOVASTUDIO_API_KEY"))
    parser.add_argument("--sample-size", type=int, default=24)
    parser.add_argument("--output", type=Path, default=Path("reports/periodic_full_vector_live_validation.json"))
    args = parser.parse_args()
    if not args.database_url or not args.api_key:
        parser.error("DATABASE_URL and CLOVASTUDIO_API_KEY are required")
    if not 10 <= args.sample_size <= 50:
        parser.error("sample size must be 10..50")

    with connect(args.database_url) as connection:
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        all_chunks = prepare_chunks(connection, "periodic-full-v1")
        sample = representative(all_chunks, args.sample_size)
        first_provider = ClovaEmbeddingV2(args.api_key)
        first = load_section_embeddings(
            connection, first_provider, loader_namespace="periodic-full-v1", chunks=sample,
        )
        second_provider = ClovaEmbeddingV2(args.api_key)
        second = load_section_embeddings(
            connection, second_provider, loader_namespace="periodic-full-v1", chunks=sample,
        )
        integrity = connection.execute("""
            SELECT count(*),count(*) FILTER (WHERE vector_dims(embedding)=1024),
                   count(*) FILTER (WHERE embedding IS NULL),
                   count(*) FILTER (WHERE embedding::text ~ 'NaN|Infinity')
            FROM section_embedding_vectors
            WHERE embedding_model='clova-studio/bge-m3' AND embedding_version='embedding-v2'
        """).fetchone()
        chosen = max(sample, key=lambda chunk: len(chunk.text))
        company = connection.execute("""
            SELECT co.company_name FROM disclosures d JOIN companies co USING(company_id)
            WHERE d.receipt_no=%s
        """, (chosen.receipt_no,)).fetchone()[0]
        results = search_vector_sections(
            connection, chosen.text, first_provider, top_k=5, company=company,
            receipt_no=chosen.receipt_no, corpus_namespace="periodic-full-v1",
        )
        if not results:
            raise RuntimeError("exact vector smoke search returned no rows")
        top = results[0]
        required = {"section_id", "vector_chunk_id", "receipt_no", "title", "section_path",
                    "source_locator", "source_file", "provenance", "score"}
        if not required.issubset(top):
            raise RuntimeError("vector result provenance projection is incomplete")
        report = {
            "sample_size": len(sample),
            "length_min": min(len(chunk.text) for chunk in sample),
            "length_max": max(len(chunk.text) for chunk in sample),
            "pdf_chunks": sum(chunk.metadata.get("source_type") == "pdf" for chunk in sample),
            "first_load": first,
            "second_load": second,
            "first_provider_stats": vars(first_provider.stats),
            "second_provider_stats": vars(second_provider.stats),
            "rate_limit_header_names": sorted(first_provider.rate_limit_headers),
            "integrity": {"vectors": integrity[0], "dimension_1024": integrity[1],
                          "null_vectors": integrity[2], "non_finite_vectors": integrity[3]},
            "smoke_search": {"result_count": len(results), "top_section_id": top["section_id"],
                             "expected_section_id": chosen.section_id, "top_score": top["score"],
                             "metadata_filters": ["company", "receipt_no", "loader_namespace"],
                             "provenance_complete": True},
        }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
