"""Evaluate FTS50 + Exact Vector50 with bounded RRF pruning and fixed reranking."""

from __future__ import annotations

import argparse,json,os,time
from pathlib import Path

from eval.evaluate_full_retrieval import read_jsonl,validate_benchmark
from eval.run_full_hybrid_benchmark import filters
from src.db.load_postgres import connect
from src.retrieval.embeddings import ClovaEmbeddingV2
from src.retrieval.hybrid_sections import reciprocal_rank_fusion
from src.retrieval.reranker import MODEL,SCORING_VERSION,RerankCache,rerank
from src.retrieval.sections import search_sections
from src.retrieval.text_normalization import normalize_search_text
from src.retrieval.vector_sections import search_vector_sections_from_embedding


DEPTHS=(10,20,30)


def _context(connection,candidates):
    section_ids=sorted({int(x["section_id"]) for x in candidates});chunk_ids=sorted({int(x["vector_chunk_id"]) for x in candidates if x.get("vector_chunk_id")})
    chunks=dict(connection.execute("SELECT vector_chunk_id,left(chunk_text,2000) FROM section_vector_chunks WHERE vector_chunk_id=ANY(%s)",(chunk_ids,)).fetchall()) if chunk_ids else {}
    sections=dict(connection.execute("SELECT section_id,left(body_text,2000) FROM document_sections WHERE section_id=ANY(%s)",(section_ids,)).fetchall())
    output=[]
    for source in candidates:
        value=dict(source);value["rerank_text"]=chunks.get(value.get("vector_chunk_id")) or sections.get(int(value["section_id"])) or value.get("body_snippet","");output.append(value)
    return output


def run(a):
    gold=read_jsonl(a.gold);validate_benchmark(gold);provider=ClovaEmbeddingV2(a.api_key);cache=RerankCache(a.cache);rows=[]
    with connect(a.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for item in gold:
            if item["retrieval_target"]=="table":rows.append({"question_id":item["question_id"],"excluded":"table_only_target"});continue
            timing={};started=time.perf_counter();fts=search_sections(connection,item["question"],top_k=50,mode="fts",**filters(item));timing["fts_db_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();embedded=provider.embed([normalize_search_text(item["question"])])[0];timing["query_embedding_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();vector=search_vector_sections_from_embedding(connection,embedded,provider.spec,top_k=50,**filters(item));timing["vector_db_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();rrf=reciprocal_rank_fusion(fts,vector,top_k=100,rrf_k=60);timing["rrf_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();with_text=_context(connection,rrf[:30]);timing["context_fetch_ms"]=(time.perf_counter()-started)*1000
            variants={}
            for depth in DEPTHS:
                started=time.perf_counter();pruned=with_text[:depth];prune_ms=(time.perf_counter()-started)*1000
                before=(cache.hits,cache.misses);started=time.perf_counter();ranked=rerank(item["question"],pruned,cache=cache,top_k=depth);rerank_ms=(time.perf_counter()-started)*1000
                variants[str(depth)]={"pruned_candidates":pruned,"reranked_candidates":ranked,"final_results":ranked[:10],"pruning_ms":prune_ms,"reranker_ms":rerank_ms,"cache_hits":cache.hits-before[0],"cache_misses":cache.misses-before[1],"total_ms":sum(timing.values())+prune_ms+rerank_ms}
            rows.append({"question_id":item["question_id"],"fts_results":fts,"vector_results":vector,"rrf_candidates":rrf[:30],"union_candidate_count":len({int(x["section_id"]) for x in fts+vector}),"timing":timing,"variants":variants})
            a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps({"rows":rows},ensure_ascii=False,default=str)+"\n",encoding="utf-8")
    cache_summary={"hits":cache.hits,"misses":cache.misses};cache.close();payload={"policy":{"fts_depth":50,"vector_depth":50,"rrf_k":60,"reranker_model":MODEL,"reranker_version":SCORING_VERSION,"hnsw":False,"gold_used_as_input":False},"provider_stats":vars(provider.stats),"cache":cache_summary,"rows":rows};a.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");return {"questions":len(gold),"evaluated":sum(bool(x.get("variants")) for x in rows),"provider":vars(provider.stats),"cache":cache_summary}


def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--output",type=Path,default=Path("artifacts/depth50_hybrid_experiment.json"));p.add_argument("--cache",type=Path,default=Path("artifacts/reranker_cache.sqlite"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));p.add_argument("--api-key",default=os.getenv("CLOVASTUDIO_API_KEY"));a=p.parse_args()
    if not a.database_url or not a.api_key:p.error("DATABASE_URL and CLOVASTUDIO_API_KEY required")
    print(json.dumps(run(a),indent=2))
if __name__=="__main__":main()
