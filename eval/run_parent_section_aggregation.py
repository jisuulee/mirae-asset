"""Run parent aggregation and unchanged Hybrid/reranker paths on real Exact vectors."""

from __future__ import annotations

import argparse,json,os,time
from pathlib import Path

from eval.evaluate_full_retrieval import read_jsonl,validate_benchmark
from eval.run_candidate_recall_diagnosis import raw_chunk_top100
from eval.run_full_hybrid_benchmark import filters
from src.db.load_postgres import connect
from src.retrieval.embeddings import ClovaEmbeddingV2
from src.retrieval.hybrid_sections import reciprocal_rank_fusion
from src.retrieval.parent_sections import METHOD_LIMITS,aggregate_parent_chunks
from src.retrieval.reranker import MODEL,SCORING_VERSION,RerankCache,rerank
from src.retrieval.sections import search_sections
from src.retrieval.text_normalization import normalize_search_text
from src.retrieval.vector_sections import search_vector_sections_from_embedding

RAW_DEPTHS=(50,100)


def enrich_chunks(connection,chunks):
    ids=[int(x["vector_chunk_id"]) for x in chunks]
    if not ids:return []
    rows=connection.execute("""SELECT c.vector_chunk_id,c.section_id,c.chunk_order,c.receipt_no,c.section_path,c.source_locator,
      left(c.chunk_text,2000),s.title_normalized,co.company_name,d.report_name,d.receipt_date,f.relative_path,f.file_role,
      jsonb_build_object('chunk',c.metadata,'section',s.metadata->'provenance'),
      (SELECT count(*) FROM section_vector_chunks pc WHERE pc.section_id=c.section_id),length(s.body_text)
      FROM section_vector_chunks c JOIN document_sections s USING(section_id) JOIN disclosures d ON d.receipt_no=c.receipt_no
      JOIN companies co ON co.company_id=d.company_id JOIN disclosure_files f ON f.file_id=c.file_id
      WHERE c.vector_chunk_id=ANY(%s)""",(ids,)).fetchall()
    names=("vector_chunk_id","section_id","chunk_order","receipt_no","section_path","source_locator","rerank_text","title","company_name","report_name","receipt_date","source_file","file_role","provenance","parent_chunk_count","body_length")
    meta={int(r[0]):dict(zip(names,r)) for r in rows};output=[]
    for rank,source in enumerate(chunks,1):
        value={**meta[int(source["vector_chunk_id"])],**source,"raw_chunk_rank":rank,"body_snippet":meta[int(source["vector_chunk_id"])]["rerank_text"][:500]};output.append(value)
    return output


def enrich_fts_context(connection,results):
    ids=sorted({int(x["section_id"]) for x in results})
    texts=dict(connection.execute("SELECT section_id,left(body_text,2000) FROM document_sections WHERE section_id=ANY(%s)",(ids,)).fetchall()) if ids else {}
    return [{**x,"rerank_text":texts.get(int(x["section_id"])) or x.get("body_snippet","")} for x in results]


def run(a):
    gold=read_jsonl(a.gold);validate_benchmark(gold);provider=ClovaEmbeddingV2(a.api_key);cache=RerankCache(a.cache);rows=[]
    with connect(a.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for item in gold:
            if item["retrieval_target"]=="table":rows.append({"question_id":item["question_id"],"excluded":"table_only_target"});continue
            timing={};started=time.perf_counter();fts=search_sections(connection,item["question"],top_k=20,mode="fts",**filters(item));timing["fts20_db_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();fts=enrich_fts_context(connection,fts);timing["fts_context_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();embedded=provider.embed([normalize_search_text(item["question"])])[0];timing["query_embedding_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();current=search_vector_sections_from_embedding(connection,embedded,provider.spec,top_k=20,**filters(item));timing["current_vector_db_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();raw=enrich_chunks(connection,raw_chunk_top100(connection,embedded,provider.spec,filters(item)));timing["raw_vector100_db_and_context_ms"]=(time.perf_counter()-started)*1000
            variants={}
            for depth in RAW_DEPTHS:
                for method in METHOD_LIMITS:
                    key=f"raw{depth}_{method}";started=time.perf_counter();parents=aggregate_parent_chunks(raw[:depth],method=method);aggregation_ms=(time.perf_counter()-started)*1000
                    started=time.perf_counter();hybrid=reciprocal_rank_fusion(fts,parents[:20],top_k=10,rrf_k=60);rrf_ms=(time.perf_counter()-started)*1000
                    before=(cache.hits,cache.misses);started=time.perf_counter();ranked=rerank(item["question"],hybrid,cache=cache,top_k=10);rerank_ms=(time.perf_counter()-started)*1000
                    variants[key]={"parent_candidates":parents[:20],"hybrid_candidates":hybrid,"final_results":ranked,
                                   "aggregation_ms":aggregation_ms,"rrf_ms":rrf_ms,"reranker_ms":rerank_ms,
                                   "cache_hits":cache.hits-before[0],"cache_misses":cache.misses-before[1],
                                   "total_ms":sum(timing.values())+aggregation_ms+rrf_ms+rerank_ms}
            rows.append({"question_id":item["question_id"],"fts_results":fts,"current_vector_results":current,"raw_chunks":raw,"timing":timing,"variants":variants})
            a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps({"rows":rows},ensure_ascii=False,default=str)+"\n",encoding="utf-8")
    cache_stats={"hits":cache.hits,"misses":cache.misses};cache.close();payload={"policy":{"fts_depth":20,"vector_parent_depth":20,"raw_depths":RAW_DEPTHS,"rrf_k":60,"hybrid_pruning_depth":10,"reranker_model":MODEL,"reranker_version":SCORING_VERSION,"hnsw":False,"gold_used_as_input":False},"aggregation":{"methods":list(METHOD_LIMITS),"max_support_bonus":0.02},"provider_stats":vars(provider.stats),"cache":cache_stats,"rows":rows};a.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");return {"questions":len(gold),"evaluated":sum(bool(x.get("variants")) for x in rows),"provider":vars(provider.stats),"cache":cache_stats}


def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--output",type=Path,default=Path("artifacts/parent_section_aggregation.json"));p.add_argument("--cache",type=Path,default=Path("artifacts/reranker_cache.sqlite"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));p.add_argument("--api-key",default=os.getenv("CLOVASTUDIO_API_KEY"));a=p.parse_args()
    if not a.database_url or not a.api_key:p.error("DATABASE_URL and CLOVASTUDIO_API_KEY required")
    print(json.dumps(run(a),indent=2))
if __name__=="__main__":main()
