"""Run exact and HNSW section retrieval with one shared query embedding per question."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from datetime import date
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import read_jsonl, validate_benchmark
from src.db.load_postgres import connect
from src.retrieval.embeddings import ClovaEmbeddingV2
from src.retrieval.text_normalization import normalize_search_text
from src.retrieval.vector_hnsw import search_sections_by_vector


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _filters(item: dict[str, Any], *, namespace_only: bool = False) -> dict[str, Any]:
    if namespace_only:
        return {"corpus_namespace": item["loader_namespace"]}
    return {
        "company": item.get("company") if item.get("company_filter_required") else None,
        "date_from": _date(item.get("date_from")), "date_to": _date(item.get("date_to")),
        "report_type": item.get("report_type_filter"), "receipt_no": item.get("receipt_no_filter"),
        "corpus_namespace": item["loader_namespace"],
    }


def _search(connection: Any, vector: tuple[float, ...], item: dict[str, Any], *, mode: str,
            ef_search: int, candidate_k: int, namespace_only: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    results = search_sections_by_vector(
        connection, vector, mode=mode, top_k=10, ef_search=ef_search,
        candidate_k=candidate_k, **_filters(item, namespace_only=namespace_only),
    )
    return {"latency_ms": (time.perf_counter() - started) * 1000, "section_results": results, "error": None}


def run(args: argparse.Namespace) -> dict[str, Any]:
    benchmark = read_jsonl(args.gold)
    validate_benchmark(benchmark)
    provider = ClovaEmbeddingV2(args.api_key)
    ef_values = tuple(args.ef_search)
    rows: list[dict[str, Any]] = []
    query_vectors: list[tuple[dict[str, Any], tuple[float, ...]]] = []
    with connect(args.database_url) as connection:
        for item in benchmark:
            if item["retrieval_target"] == "table":
                rows.append({"question_id": item["question_id"], "excluded": "table_only_target"})
                continue
            query_started = time.perf_counter()
            embedded = provider.embed([normalize_search_text(item["question"])])[0]
            query_ms = (time.perf_counter() - query_started) * 1000
            vector = tuple(embedded.vector)
            query_vectors.append((item, vector))
            exact = _search(connection, vector, item, mode="exact", ef_search=ef_values[0], candidate_k=args.candidate_k)
            hnsw = {str(ef): _search(connection, vector, item, mode="hnsw", ef_search=ef,
                                     candidate_k=args.candidate_k) for ef in ef_values}
            rows.append({"question_id": item["question_id"], "query_embedding_latency_ms": query_ms,
                         "exact": exact, "hnsw": hnsw})
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({"rows": rows}, ensure_ascii=False, default=str) + "\n", encoding="utf-8")

        probes = []
        probe_source = query_vectors[:min(args.namespace_probes, len(query_vectors))]
        for item, vector in probe_source:
            values = {"exact": _search(connection, vector, item, mode="exact", ef_search=ef_values[0],
                                        candidate_k=args.candidate_k, namespace_only=True)}
            values["hnsw"] = {str(ef): _search(connection, vector, item, mode="hnsw", ef_search=ef,
                                                candidate_k=args.candidate_k, namespace_only=True)
                              for ef in ef_values}
            probes.append({"question_id": item["question_id"], **values})
    payload = {
        "experiment": {"ef_search": ef_values, "candidate_k": args.candidate_k,
                       "namespace_probe_count": len(probes),
                       "condition": "DB latency excludes query embedding; single process; PostgreSQL/OS cache not flushed"},
        "provider_stats": vars(provider.stats), "rows": rows, "namespace_probes": probes,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return {"questions": len(benchmark), "evaluated": len(query_vectors), "excluded_table": len(benchmark)-len(query_vectors),
            "query_embedding_requests": provider.stats.requests, "query_embedding_retries": provider.stats.retries,
            "namespace_probes": len(probes)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/periodic_full_hnsw_experiment.json"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--api-key", default=os.getenv("CLOVASTUDIO_API_KEY"))
    parser.add_argument("--ef-search", type=int, nargs="+", default=[20, 50, 100])
    parser.add_argument("--candidate-k", type=int, default=100)
    parser.add_argument("--namespace-probes", type=int, default=10)
    args = parser.parse_args()
    if not args.database_url or not args.api_key:
        parser.error("DATABASE_URL and CLOVASTUDIO_API_KEY are required")
    if len(set(args.ef_search)) < 2 or min(args.ef_search) < 1:
        parser.error("provide at least two positive ef_search values")
    print(json.dumps(run(args), indent=2))


if __name__ == "__main__":
    main()
