"""Regression-run the production Retrieval V1 interface on the frozen benchmark."""

from __future__ import annotations

import argparse,json,os,time
from pathlib import Path

from eval.evaluate_full_retrieval import read_jsonl,validate_benchmark
from eval.run_full_hybrid_benchmark import filters
from eval.run_query_expansion_diagnostic import QueryVectorCache
from src.db.load_postgres import connect
from src.retrieval.default_sections import search_default_sections
from src.retrieval.embeddings import ClovaEmbeddingV2
from src.retrieval.reranker import RerankCache


class CachedProvider:
    def __init__(self,delegate,cache):self.delegate=delegate;self.cache=cache;self.spec=delegate.spec
    def embed(self,texts):
        output=[]
        for text in texts:
            value=self.cache.get(text,self.spec)
            if value is None:value=self.delegate.embed([text])[0];self.cache.put(text,self.spec,value)
            output.append(value)
        return output


def run(a):
    gold=read_jsonl(a.gold);validate_benchmark(gold);delegate=ClovaEmbeddingV2(a.api_key);vector_cache=QueryVectorCache(a.embedding_cache);provider=CachedProvider(delegate,vector_cache);rerank_cache=RerankCache(a.reranker_cache);rows=[];latencies=[]
    with connect(a.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for item in gold:
            if item["retrieval_target"]=="table":rows.append({"question_id":item["question_id"],"excluded":"table_only_target","section_results":[],"table_results":[],"error":"excluded_table_only_target"});continue
            started=time.perf_counter();results=search_default_sections(connection,item["question"],provider,rerank_cache=rerank_cache,**filters(item));elapsed=(time.perf_counter()-started)*1000;latencies.append(elapsed);rows.append({"question_id":item["question_id"],"section_results":results,"table_results":[],"error":None,"latency_ms":elapsed})
    cache={"embedding":{"hits":vector_cache.hits,"misses":vector_cache.misses},"reranker":{"hits":rerank_cache.hits,"misses":rerank_cache.misses}};vector_cache.close();rerank_cache.close();payload={"pipeline":"retrieval-v1-frozen","provider_stats":vars(delegate.stats),"cache":cache,"latencies_ms":latencies,"rows":rows};a.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");return {"evaluated":len(latencies),"provider":vars(delegate.stats),"cache":cache}


def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--output",type=Path,default=Path("artifacts/retrieval_v1_freeze_predictions.json"));p.add_argument("--embedding-cache",type=Path,default=Path("artifacts/query_expansion_embeddings.sqlite"));p.add_argument("--reranker-cache",type=Path,default=Path("artifacts/reranker_cache.sqlite"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));p.add_argument("--api-key",default=os.getenv("CLOVASTUDIO_API_KEY"));a=p.parse_args()
    if not a.database_url or not a.api_key:p.error("DATABASE_URL and CLOVASTUDIO_API_KEY required")
    print(json.dumps(run(a),indent=2))
if __name__=="__main__":main()
