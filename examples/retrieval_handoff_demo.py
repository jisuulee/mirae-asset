"""Router/Retrieval handoff demo. This script never calls HyperCLOVA X."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db.load_postgres import connect
from src.retrieval import retrieve, route_question
from src.retrieval.embeddings import ClovaEmbeddingV2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", help="사용자 자연어 질문")
    parser.add_argument(
        "--retrieve",
        action="store_true",
        help="PostgreSQL 및 필요한 경우 CLOVA query embedding을 사용해 retrieval까지 실행",
    )
    args = parser.parse_args()

    if not args.retrieve:
        print(json.dumps(route_question(args.question).to_dict(), ensure_ascii=False, indent=2))
        return

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("--retrieve requires DATABASE_URL")
    api_key = os.getenv("CLOVASTUDIO_API_KEY")
    provider = ClovaEmbeddingV2(api_key) if api_key else None

    with connect(database_url) as connection:
        result = retrieve(args.question, connection, provider=provider)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
