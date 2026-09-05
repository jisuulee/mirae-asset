"""Collect depth and filter-ablation candidates from unchanged FTS/Exact Vector retrieval."""

from __future__ import annotations

import argparse,json,os
from datetime import date
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import read_jsonl,validate_benchmark
from src.db.load_postgres import connect
from src.retrieval.embeddings import ClovaEmbeddingV2
from src.retrieval.sections import search_sections
from src.retrieval.text_normalization import canonical_report_type,normalize_company_query,normalize_search_text
from src.retrieval.vector_sections import _vector_literal,search_vector_sections_from_embedding


def _date(v):return date.fromisoformat(v) if v else None
def profile_filters(item: dict[str,Any],profile: str):
    company=item.get("company") if item.get("company_filter_required") else None;date_from=_date(item.get("date_from"));date_to=_date(item.get("date_to"));report=item.get("report_type_filter");receipt=item.get("receipt_no_filter")
    values={"company":company,"date_from":date_from,"date_to":date_to,"report_type":report,"receipt_no":receipt,"corpus_namespace":item["loader_namespace"]}
    if profile=="normal":return values
    if profile=="company_only":values.update(date_from=None,date_to=None,report_type=None,receipt_no=None)
    elif profile=="receipt_only":values.update(company=None,date_from=None,date_to=None,report_type=None)
    elif profile=="year_report_only":values.update(company=None,receipt_no=None)
    elif profile=="namespace_only":values.update(company=None,date_from=None,date_to=None,report_type=None,receipt_no=None)
    else:raise ValueError(profile)
    return values


def profiles(item):
    result=["normal"]
    if item.get("company_filter_required"):result.append("company_only")
    if item.get("receipt_no_filter"):result.append("receipt_only")
    if item.get("year_filter_required") or item.get("report_type_filter"):result.append("year_report_only")
    result.append("namespace_only")
    return list(dict.fromkeys(result))


def raw_chunk_top100(connection,embedded,spec,filters):
    clauses=["c.loader_namespace=%s","e.embedding_model=%s","e.embedding_version=%s","e.embedding_dimension=%s"];params=[filters["corpus_namespace"],spec.model,spec.version,spec.dimension]
    if filters.get("company"):clauses.append("co.company_name_normalized=%s");params.append(normalize_company_query(filters["company"]))
    if filters.get("date_from"):clauses.append("d.receipt_date>=%s");params.append(filters["date_from"])
    if filters.get("date_to"):clauses.append("d.receipt_date<=%s");params.append(filters["date_to"])
    if filters.get("report_type"):clauses.append("d.report_type=%s");params.append(canonical_report_type(filters["report_type"]))
    if filters.get("receipt_no"):clauses.append("c.receipt_no=%s");params.append(filters["receipt_no"])
    sql=f"""SELECT c.vector_chunk_id,c.section_id,c.chunk_order,1-(e.embedding<=>%s::vector) score
      FROM section_vector_chunks c JOIN section_chunk_embeddings ce USING(vector_chunk_id)
      JOIN section_embedding_vectors e USING(embedding_id) JOIN disclosures d ON d.receipt_no=c.receipt_no
      JOIN companies co ON co.company_id=d.company_id WHERE {' AND '.join(clauses)}
      ORDER BY e.embedding<=>%s::vector,c.vector_chunk_id LIMIT 100"""
    literal=_vector_literal(embedded.vector)
    with connection.cursor() as cur:cur.execute(sql,(literal,*params,literal));return [{"vector_chunk_id":x[0],"section_id":x[1],"chunk_order":x[2],"score":x[3]} for x in cur.fetchall()]


def run(a):
    gold=read_jsonl(a.gold);validate_benchmark(gold);provider=ClovaEmbeddingV2(a.api_key);rows=[]
    with connect(a.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        for item in gold:
            if item["retrieval_target"]=="table":rows.append({"question_id":item["question_id"],"excluded":"table_only_target"});continue
            embedded=provider.embed([normalize_search_text(item["question"])])[0];searches={}
            for profile in profiles(item):
                f=profile_filters(item,profile)
                searches[profile]={"fts":search_sections(connection,item["question"],top_k=100,mode="fts",**f),"vector":search_vector_sections_from_embedding(connection,embedded,provider.spec,top_k=100,**f)}
            searches["normal"]["vector_chunks"]=raw_chunk_top100(connection,embedded,provider.spec,profile_filters(item,"normal"))
            rows.append({"question_id":item["question_id"],"searches":searches})
            a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps({"rows":rows},ensure_ascii=False,default=str)+"\n",encoding="utf-8")
    payload={"policy":{"retrievers":"unchanged FTS + Exact Vector","max_depth":100,"gold_used_for_retrieval":False,"hnsw":False},"provider_stats":vars(provider.stats),"rows":rows};a.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");return {"questions":len(gold),"evaluated":sum(bool(x.get("searches")) for x in rows),"provider":vars(provider.stats)}


def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--output",type=Path,default=Path("artifacts/candidate_recall_diagnosis.json"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));p.add_argument("--api-key",default=os.getenv("CLOVASTUDIO_API_KEY"));a=p.parse_args()
    if not a.database_url or not a.api_key:p.error("DATABASE_URL and CLOVASTUDIO_API_KEY required")
    print(json.dumps(run(a),indent=2))
if __name__=="__main__":main()
