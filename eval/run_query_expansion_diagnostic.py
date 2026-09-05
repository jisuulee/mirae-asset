"""Run deterministic corpus-grounded query expansion over unchanged retrievers."""

from __future__ import annotations

import argparse, hashlib, json, os, sqlite3, time
from pathlib import Path

from eval.run_full_hybrid_benchmark import filters
from eval.evaluate_full_retrieval import read_jsonl, validate_benchmark
from src.db.load_postgres import connect
from src.retrieval.embeddings import ClovaEmbeddingV2, EmbeddingResult
from src.retrieval.hybrid_sections import reciprocal_rank_fusion
from src.retrieval.query_expansion import CorpusQueryExpander, fuse_multi_query_results
from src.retrieval.reranker import RerankCache, rerank
from src.retrieval.sections import search_sections
from src.retrieval.text_normalization import normalize_search_text
from src.retrieval.vector_sections import search_vector_sections_from_embedding

VARIANTS=("original","spacing","title_path","combined")


class QueryVectorCache:
    def __init__(self,path:Path):
        path.parent.mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS vectors(cache_key TEXT PRIMARY KEY,vector_json TEXT NOT NULL,input_tokens INTEGER)")
        self.hits=0;self.misses=0
    def key(self,text,spec):return hashlib.sha256("\0".join((normalize_search_text(text),spec.model,spec.version)).encode()).hexdigest()
    def get(self,text,spec):
        row=self.db.execute("SELECT vector_json,input_tokens FROM vectors WHERE cache_key=?",(self.key(text,spec),)).fetchone()
        if row:self.hits+=1;return EmbeddingResult(tuple(json.loads(row[0])),row[1])
        self.misses+=1;return None
    def put(self,text,spec,result):self.db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?)",(self.key(text,spec),json.dumps(result.vector),result.input_tokens))
    def close(self):self.db.commit();self.db.close()


def vocabulary(connection,namespace):
    rows=connection.execute("""SELECT DISTINCT title_normalized,section_path FROM document_sections
      WHERE metadata->>'loader_namespace'=%s AND metadata->>'section_kind'='narrative'""",(namespace,)).fetchall()
    values=[]
    for title,path in rows:
        if title:values.append(str(title))
        if path:values.extend(x.strip() for x in str(path).split("/") if x.strip())
    return values


def add_context(connection,candidates):
    ids=sorted({int(x["section_id"]) for x in candidates})
    texts=dict(connection.execute("SELECT section_id,left(body_text,2000) FROM document_sections WHERE section_id=ANY(%s)",(ids,)).fetchall()) if ids else {}
    return [{**x,"rerank_text":texts.get(int(x["section_id"])) or x.get("body_snippet","")} for x in candidates]


def embedded_queries(provider,cache,queries):
    results={};missing=[]
    for query in queries:
        cached=cache.get(query,provider.spec)
        if cached is None:missing.append(query)
        else:results[query]=cached
    if missing:
        generated=provider.embed(missing)
        for query,result in zip(missing,generated):cache.put(query,provider.spec,result);results[query]=result
    return results


def run(args):
    gold=read_jsonl(args.gold);validate_benchmark(gold);provider=ClovaEmbeddingV2(args.api_key,concurrency=args.concurrency)
    vector_cache=QueryVectorCache(args.embedding_cache);rerank_cache=RerankCache(args.reranker_cache);rows=[]
    with connect(args.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        expander=CorpusQueryExpander(vocabulary(connection,args.namespace),max_terms=args.max_expansions)
        for item in gold:
            if item["retrieval_target"]=="table":rows.append({"question_id":item["question_id"],"excluded":"table_only_target"});continue
            started=time.perf_counter();expansions={key:expander.expand(item["question"],key) for key in VARIANTS};expansion_ms=(time.perf_counter()-started)*1000
            query_lists={key:[normalize_search_text(item["question"]),*[x.query for x in expansions[key]]] for key in VARIANTS}
            unique=list(dict.fromkeys(query for values in query_lists.values() for query in values));started=time.perf_counter();vectors=embedded_queries(provider,vector_cache,unique);embedding_ms=(time.perf_counter()-started)*1000
            fts_sets={};vector_sets={};fts_ms=0.;vector_ms=0.;f=filters(item)
            for query in unique:
                started=time.perf_counter();fts_sets[query]=search_sections(connection,query,top_k=100,mode="fts",**f);fts_ms+=(time.perf_counter()-started)*1000
                started=time.perf_counter();vector_sets[query]=search_vector_sections_from_embedding(connection,vectors[query],provider.spec,top_k=100,**f);vector_ms+=(time.perf_counter()-started)*1000
            variants={}
            for key,queries in query_lists.items():
                started=time.perf_counter();fts=fuse_multi_query_results([fts_sets[x] for x in queries],top_k=100);vector=fuse_multi_query_results([vector_sets[x] for x in queries],top_k=100);multi_fusion_ms=(time.perf_counter()-started)*1000
                started=time.perf_counter();hybrid=reciprocal_rank_fusion(fts[:20],vector[:20],top_k=10,rrf_k=60);rrf_ms=(time.perf_counter()-started)*1000
                contextual=add_context(connection,hybrid);started=time.perf_counter();final=rerank(item["question"],contextual,cache=rerank_cache,top_k=10);rerank_ms=(time.perf_counter()-started)*1000
                variants[key]={"queries":queries,"expansions":[vars(x) for x in expansions[key]],"fts_results":fts,"vector_results":vector,"hybrid_results":hybrid,"final_results":final,"timing":{"multi_query_fusion_ms":multi_fusion_ms,"rrf_ms":rrf_ms,"reranker_ms":rerank_ms}}
            rows.append({"question_id":item["question_id"],"variants":variants,"timing":{"expansion_ms":expansion_ms,"embedding_ms":embedding_ms,"fts_all_queries_ms":fts_ms,"vector_all_queries_ms":vector_ms},"error":None})
            args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps({"rows":rows},ensure_ascii=False,default=str)+"\n",encoding="utf-8")
    cache_stats={"embedding":{"hits":vector_cache.hits,"misses":vector_cache.misses},"reranker":{"hits":rerank_cache.hits,"misses":rerank_cache.misses}};vector_cache.close();rerank_cache.close()
    payload={"policy":{"external_corpus":False,"external_llm":False,"gold_used":False,"max_expansions":args.max_expansions,"rrf_k":60,"retriever_depth":100,"hybrid_depth":20,"rerank_depth":10},"provider_stats":vars(provider.stats),"cache":cache_stats,"rows":rows}
    args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");return {"rows":len(rows),"provider":vars(provider.stats),"cache":cache_stats}


def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--output",type=Path,default=Path("artifacts/query_expansion_diagnostic.json"));p.add_argument("--embedding-cache",type=Path,default=Path("artifacts/query_expansion_embeddings.sqlite"));p.add_argument("--reranker-cache",type=Path,default=Path("artifacts/reranker_cache.sqlite"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));p.add_argument("--api-key",default=os.getenv("CLOVASTUDIO_API_KEY"));p.add_argument("--namespace",default="periodic-full-v1");p.add_argument("--max-expansions",type=int,default=5);p.add_argument("--concurrency",type=int,default=4);a=p.parse_args()
    if not a.database_url or not a.api_key:p.error("DATABASE_URL and CLOVASTUDIO_API_KEY required")
    print(json.dumps(run(a),indent=2))
if __name__=="__main__":main()
