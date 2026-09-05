"""Run FTS + Exact Vector candidate retrieval and deterministic RRF variants."""

from __future__ import annotations

import argparse, json, os, time
from datetime import date
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import read_jsonl, validate_benchmark
from src.db.load_postgres import connect
from src.retrieval.embeddings import ClovaEmbeddingV2
from src.retrieval.hybrid_sections import reciprocal_rank_fusion
from src.retrieval.sections import search_sections
from src.retrieval.text_normalization import normalize_search_text
from src.retrieval.vector_sections import search_vector_sections_from_embedding


def _date(value: str | None) -> date | None: return date.fromisoformat(value) if value else None


def filters(item: dict[str, Any]) -> dict[str, Any]:
    return {"company": item.get("company") if item.get("company_filter_required") else None,
            "date_from": _date(item.get("date_from")), "date_to": _date(item.get("date_to")),
            "report_type": item.get("report_type_filter"), "receipt_no": item.get("receipt_no_filter"),
            "corpus_namespace": item["loader_namespace"]}


def run(args: argparse.Namespace) -> dict[str, Any]:
    gold=read_jsonl(args.gold);validate_benchmark(gold);provider=ClovaEmbeddingV2(args.api_key)
    variants=[(depth,k) for depth in args.depth for k in args.rrf_k];rows=[]
    with connect(args.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for item in gold:
            if item["retrieval_target"] == "table":
                rows.append({"question_id":item["question_id"],"excluded":"table_only_target"});continue
            error=None; fts=[]; vector=[]; fused={}; timings={}
            try:
                started=time.perf_counter();fts=search_sections(connection,item["question"],top_k=max(args.depth),mode="fts",**filters(item));timings["fts_db_ms"]=(time.perf_counter()-started)*1000
                started=time.perf_counter();embedded=provider.embed([normalize_search_text(item["question"])])[0];timings["query_embedding_ms"]=(time.perf_counter()-started)*1000
                started=time.perf_counter();vector=search_vector_sections_from_embedding(connection,embedded,provider.spec,top_k=max(args.depth),**filters(item));timings["vector_db_ms"]=(time.perf_counter()-started)*1000
                for depth,k in variants:
                    started=time.perf_counter();result=reciprocal_rank_fusion(fts[:depth],vector[:depth],top_k=10,rrf_k=k);fusion_ms=(time.perf_counter()-started)*1000
                    fused[f"d{depth}_k{k}"]={"section_results":result,"fusion_ms":fusion_ms,
                                             "total_ms":timings["fts_db_ms"]+timings["query_embedding_ms"]+timings["vector_db_ms"]+fusion_ms}
            except Exception as exc: error=f"{type(exc).__name__}: {exc}"
            rows.append({"question_id":item["question_id"],"fts_results":fts,"vector_results":vector,
                         "timings":timings,"variants":fused,"error":error})
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps({"rows":rows},ensure_ascii=False,default=str)+"\n",encoding="utf-8")
    payload={"policy":{"benchmark":"reviewed 36 + internal/candidate 120","vector_backend":"exact cosine","hnsw":False},
             "experiment":{"depth":args.depth,"rrf_k":args.rrf_k,"primary":"d20_k60","weights":{"fts":1.0,"vector":1.0},
                           "condition":"single process; cache not flushed; total includes query embedding"},
             "provider_stats":vars(provider.stats),"rows":rows}
    args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    return {"questions":len(gold),"evaluated":sum(bool(row.get("variants")) for row in rows),
            "excluded_table":sum(bool(row.get("excluded")) for row in rows),"provider":vars(provider.stats)}


def main() -> None:
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--output",type=Path,default=Path("artifacts/periodic_full_hybrid_experiment.json"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));p.add_argument("--api-key",default=os.getenv("CLOVASTUDIO_API_KEY"));p.add_argument("--depth",type=int,nargs="+",default=[10,20]);p.add_argument("--rrf-k",type=int,nargs="+",default=[20,60,100]);a=p.parse_args()
    if not a.database_url or not a.api_key:p.error("DATABASE_URL and CLOVASTUDIO_API_KEY are required")
    if not set(a.depth).issubset(range(1,101)) or min(a.rrf_k)<1:p.error("invalid depth or RRF k")
    print(json.dumps(run(a),indent=2))


if __name__ == "__main__":main()
