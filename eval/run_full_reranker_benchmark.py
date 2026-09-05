"""Rerank the immutable primary Hybrid Top-10 without new retrieval or API calls."""

from __future__ import annotations

import argparse,json,os,time
from pathlib import Path

from eval.evaluate_full_retrieval import read_jsonl
from src.db.load_postgres import connect
from src.retrieval.reranker import MODEL,SCORING_VERSION,RerankCache,rerank


def run(args):
    gold={x["question_id"]:x for x in read_jsonl(args.gold)};raw=json.loads(args.hybrid_input.read_text(encoding="utf-8"));primary=raw["experiment"]["primary"]
    candidate_rows=[(row,candidate) for row in raw["rows"] if row.get("variants") for candidate in row["variants"][primary]["section_results"]]
    section_ids=sorted({int(c["section_id"]) for _,c in candidate_rows});chunk_ids=sorted({int(c["vector_chunk_id"]) for _,c in candidate_rows if c.get("vector_chunk_id")})
    with connect(args.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        chunks=dict(connection.execute("SELECT vector_chunk_id,left(chunk_text,2000) FROM section_vector_chunks WHERE vector_chunk_id=ANY(%s)",(chunk_ids,)).fetchall()) if chunk_ids else {}
        sections=dict(connection.execute("SELECT section_id,left(body_text,2000) FROM document_sections WHERE section_id=ANY(%s)",(section_ids,)).fetchall())
    cache=RerankCache(args.cache);rows=[];latencies=[]
    for source in raw["rows"]:
        if source.get("excluded"):
            rows.append({"question_id":source["question_id"],"excluded":"table_only_target"});continue
        candidates=[]
        for candidate in source["variants"][primary]["section_results"]:
            value=dict(candidate);value["rerank_text"]=chunks.get(value.get("vector_chunk_id")) or sections.get(int(value["section_id"])) or value.get("body_snippet","")
            candidates.append(value)
        started=time.perf_counter();results=rerank(gold[source["question_id"]]["question"],candidates,cache=cache,top_k=10);elapsed=(time.perf_counter()-started)*1000;latencies.append(elapsed)
        rows.append({"question_id":source["question_id"],"section_results":results,"reranker_latency_ms":elapsed,"error":None})
    first={"hits":cache.hits,"misses":cache.misses};before=cache.hits
    for source in raw["rows"]:
        if not source.get("variants"):continue
        candidates=[]
        for candidate in source["variants"][primary]["section_results"]:
            value=dict(candidate);value["rerank_text"]=chunks.get(value.get("vector_chunk_id")) or sections.get(int(value["section_id"])) or value.get("body_snippet","");candidates.append(value)
        rerank(gold[source["question_id"]]["question"],candidates,cache=cache,top_k=10)
    validation_hits=cache.hits-before;cache.close()
    payload={"policy":{"candidate_source":primary,"candidate_set_changed":False,"gold_used_as_input":False,"api_calls":0},"reranker":{"model":MODEL,"version":SCORING_VERSION,"depth":10,"text_limit_chars":2000},"cache":{"first_pass":first,"validation_reuse_hits":validation_hits},"rows":rows}
    args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");return {"rows":len(rows),"evaluated":len(latencies),"cache":payload["cache"]}


def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--hybrid-input",type=Path,default=Path("artifacts/periodic_full_hybrid_experiment.json"));p.add_argument("--output",type=Path,default=Path("artifacts/periodic_full_reranker_predictions.json"));p.add_argument("--cache",type=Path,default=Path("artifacts/reranker_cache.sqlite"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));a=p.parse_args()
    if not a.database_url:p.error("DATABASE_URL is required")
    print(json.dumps(run(a),indent=2))


if __name__=="__main__":main()
