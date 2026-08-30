"""Run an original-query-only title/path channel beside unchanged FTS and Exact Vector."""

from __future__ import annotations

import argparse,json,os,time
from pathlib import Path

from eval.evaluate_full_retrieval import read_jsonl,validate_benchmark
from eval.run_full_hybrid_benchmark import filters
from eval.run_query_expansion_diagnostic import QueryVectorCache,add_context
from src.db.load_postgres import connect
from src.retrieval.embeddings import ClovaEmbeddingV2
from src.retrieval.reranker import RerankCache,rerank
from src.retrieval.sections import search_sections
from src.retrieval.text_normalization import normalize_search_text
from src.retrieval.title_paths import fuse_named_channels,search_title_paths
from src.retrieval.vector_sections import search_vector_sections_from_embedding

DEPTHS=(10,20,50,100)


def run(a):
    gold=read_jsonl(a.gold);validate_benchmark(gold);provider=ClovaEmbeddingV2(a.api_key);embedding_cache=QueryVectorCache(a.embedding_cache);rerank_cache=RerankCache(a.reranker_cache);rows=[]
    with connect(a.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for item in gold:
            if item["retrieval_target"]=="table":rows.append({"question_id":item["question_id"],"excluded":"table_only_target"});continue
            query=item["question"];normalized=normalize_search_text(query);f=filters(item);timing={}
            started=time.perf_counter();fts=search_sections(connection,query,top_k=100,mode="fts",**f);timing["fts_ms"]=(time.perf_counter()-started)*1000
            embedded=embedding_cache.get(normalized,provider.spec)
            if embedded is None:
                started=time.perf_counter();embedded=provider.embed([normalized])[0];embedding_cache.put(normalized,provider.spec,embedded);timing["embedding_ms"]=(time.perf_counter()-started)*1000
            else:timing["embedding_ms"]=0.0
            started=time.perf_counter();vector=search_vector_sections_from_embedding(connection,embedded,provider.spec,top_k=100,**f);timing["vector_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();title_fts=search_title_paths(connection,query,top_k=100,mode="fts",**f);timing["title_fts_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();title_trigram=search_title_paths(connection,query,top_k=100,mode="trigram",**f);timing["title_trigram_ms"]=(time.perf_counter()-started)*1000
            started=time.perf_counter();title=fuse_named_channels({"title_fts":title_fts,"title_trigram":title_trigram},top_k=100);timing["title_fusion_ms"]=(time.perf_counter()-started)*1000
            variants={}
            configs={"baseline":{"fts":fts[:20],"vector":vector[:20]},"title_1_0":{"fts":fts[:20],"vector":vector[:20],"title":title[:20]},"title_0_5":{"fts":fts[:20],"vector":vector[:20],"title":title[:20]}}
            for key,channels in configs.items():
                weights={"title":.5} if key=="title_0_5" else None;started=time.perf_counter();hybrid=fuse_named_channels(channels,top_k=10,rrf_k=60,weights=weights);fusion_ms=(time.perf_counter()-started)*1000
                contextual=add_context(connection,hybrid);started=time.perf_counter();final=rerank(query,contextual,cache=rerank_cache,top_k=10);rerank_ms=(time.perf_counter()-started)*1000
                variants[key]={"hybrid_results":hybrid,"final_results":final,"fusion_ms":fusion_ms,"reranker_ms":rerank_ms}
            rows.append({"question_id":item["question_id"],"query":query,"query_unchanged":query==item["question"],"fts_results":fts,"vector_results":vector,"title_fts_results":title_fts,"title_trigram_results":title_trigram,"title_results":title,"variants":variants,"timing":timing})
            a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps({"rows":rows},ensure_ascii=False,default=str)+"\n",encoding="utf-8")
    cache={"embedding":{"hits":embedding_cache.hits,"misses":embedding_cache.misses},"reranker":{"hits":rerank_cache.hits,"misses":rerank_cache.misses}};embedding_cache.close();rerank_cache.close();payload={"policy":{"query_mutated":False,"gold_used":False,"external_model":False,"body_used_by_title_channel":False,"depths":DEPTHS,"fusion_depth":20,"rrf_k":60,"rerank_depth":10},"provider_stats":vars(provider.stats),"cache":cache,"rows":rows};a.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");return {"rows":len(rows),"provider":vars(provider.stats),"cache":cache}


def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--output",type=Path,default=Path("artifacts/title_path_retrieval_experiment.json"));p.add_argument("--embedding-cache",type=Path,default=Path("artifacts/query_expansion_embeddings.sqlite"));p.add_argument("--reranker-cache",type=Path,default=Path("artifacts/reranker_cache.sqlite"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));p.add_argument("--api-key",default=os.getenv("CLOVASTUDIO_API_KEY"));a=p.parse_args()
    if not a.database_url or not a.api_key:p.error("DATABASE_URL and CLOVASTUDIO_API_KEY required")
    print(json.dumps(run(a),indent=2))
if __name__=="__main__":main()
