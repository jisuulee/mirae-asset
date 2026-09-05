"""Create and idempotently load section embeddings."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.db.load_postgres import connect
from src.retrieval.embeddings import ClovaEmbeddingV2
from src.retrieval.vector_sections import load_section_embeddings, prepare_chunks

MIGRATION = Path(__file__).with_name("migrations") / "005_pgvector.sql"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--api-key", default=os.getenv("CLOVASTUDIO_API_KEY"))
    parser.add_argument("--loader-namespace", default="periodic-full-v1")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL is required")
    with connect(args.database_url) as connection:
        if args.dry_run:
            connection.execute("SET TRANSACTION READ ONLY")
            chunks = prepare_chunks(connection, args.loader_namespace)
            print(json.dumps({"sections": len({chunk.section_id for chunk in chunks}),
                              "chunks": len(chunks), "unique_texts": len({chunk.content_hash for chunk in chunks}),
                              "characters": sum(len(chunk.text) for chunk in chunks)}, indent=2))
            return
        if not args.api_key:
            parser.error("CLOVASTUDIO_API_KEY or --api-key is required")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        provider = ClovaEmbeddingV2(args.api_key, concurrency=args.concurrency)
        metrics = load_section_embeddings(
            connection, provider,
            loader_namespace=args.loader_namespace, batch_size=args.batch_size,
        )
        metrics["provider_stats"] = vars(provider.stats)
        metrics["rate_limit_header_names"] = sorted(provider.rate_limit_headers)
        print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
