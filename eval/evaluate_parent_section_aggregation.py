"""Evaluate parent aggregation, split behavior, bias, Hybrid impact, and failures."""

from __future__ import annotations

import argparse,json,math,os,statistics
from collections import Counter
from pathlib import Path

from eval.evaluate_full_retrieval import evaluate,read_jsonl
from src.db.load_postgres import connect

CHOSEN="raw50_support2";BASE="raw50_best"
def rank(rows,gold):return next((i for i,x in enumerate(rows,1) if int(x["section_id"]) in gold),None)
def pct(v,p):return sorted(v)[max(0,math.ceil(len(v)*p)-1)] if v else None
def lat(v):return {"count":len(v),"average_ms":statistics.fmean(v) if v else None,"p50_ms":pct(v,.5),"p95_ms":pct(v,.95),"max_ms":max(v) if v else None}
def metrics(items,results):
    ranks=[rank(results[x["question_id"]],set(map(int,x["gold_section_ids"]))) for x in items];n=len(ranks)
    return {"question_count":n,"top1":sum(r==1 for r in ranks)/n,"recall_at_3":sum(bool(r and r<=3) for r in ranks)/n,"recall_at_5":sum(bool(r and r<=5) for r in ranks)/n,"recall_at_10":sum(bool(r and r<=10) for r in ranks)/n,"recall_at_20":sum(bool(r and r<=20) for r in ranks)/n,"mrr":sum(1/r if r else 0 for r in ranks)/n}
def prediction(item,row,key):
    if row.get("excluded"):return {"question_id":item["question_id"],"error":"excluded_table_only_target"}
    return {"question_id":item["question_id"],"section_results":row["variants"][key]["final_results"],"table_results":[],"error":None}
def shapes(connection,gold):
    ids=sorted({int(i) for x in gold for i in x["gold_section_ids"]});rows=connection.execute("SELECT s.section_id,length(s.body_text),count(c.vector_chunk_id) FROM document_sections s LEFT JOIN section_vector_chunks c USING(section_id) WHERE s.section_id=ANY(%s) GROUP BY s.section_id",(ids,)).fetchall();return {int(i):{"body_length":int(n or 0),"chunk_count":int(c),"split":int(c)>1,"long":int(n or 0)>8000} for i,n,c in rows}
def corr(xs,ys):
    if len(xs)<2 or statistics.pstdev(xs)==0 or statistics.pstdev(ys)==0:return 0.0
    mx,my=statistics.fmean(xs),statistics.fmean(ys);return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/math.sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))


def build(gold,raw,shape,legacy):
    rows={x["question_id"]:x for x in raw["rows"]};eligible=[x for x in gold if x["retrieval_target"]!="table"]
    current={x["question_id"]:rows[x["question_id"]]["current_vector_results"] for x in eligible}
    vector={key:{x["question_id"]:rows[x["question_id"]]["variants"][key]["parent_candidates"] for x in eligible} for key in next(x for x in raw["rows"] if x.get("variants"))["variants"]}
    variants={key:evaluate(gold,[prediction(x,rows[x["question_id"]],key) for x in gold]) for key in vector}
    split=[x for x in eligible if any(shape[int(i)]["split"] for i in x["gold_section_ids"])];unsplit=[x for x in eligible if x not in split]
    hybrid_results={key:{x["question_id"]:rows[x["question_id"]]["variants"][key]["final_results"] for x in eligible} for key in vector}
    hybrid_shape_metrics={
        key:{"overall":metrics(eligible,value),"split":metrics(split,value),"unsplit":metrics(unsplit,value)}
        for key,value in hybrid_results.items()
    }
    vector_metrics={"current":{"overall":metrics(eligible,current),"split":metrics(split,current),"unsplit":metrics(unsplit,current)}}
    for key,value in vector.items():vector_metrics[key]={"overall":metrics(eligible,value),"split":metrics(split,value),"unsplit":metrics(unsplit,value)}
    changes=Counter();threshold=Counter();multiple=0;split_rows=[];failures=[]
    for item in split:
        row=rows[item["question_id"]];g=set(map(int,item["gold_section_ids"]));before=rank(current[item["question_id"]],g);after=rank(vector[CHOSEN][item["question_id"]],g);b=before or 21;a=after or 21
        changes["improved" if a<b else "unchanged" if a==b else "worsened"]+=1
        for k in (10,5,3,1):threshold[f"new_top{k}"]+=bool(b>k and a<=k)
        gold_chunks=[x for x in row["raw_chunks"][:50] if int(x["section_id"]) in g];multiple+=len(gold_chunks)>=2
        if item["human_review_status"]=="ambiguous":kind="ambiguous_gold"
        elif not gold_chunks:kind="no_gold_chunk_retrieved" if not any(int(x["section_id"]) in g for x in row["raw_chunks"]) else "semantic_mismatch"
        elif after is None or after>10:kind="gold_chunk_retrieved_but_parent_misranked"
        elif len(gold_chunks)<2:kind="insufficient_multiple_chunk_signal"
        else:kind=None
        record={"question_id":item["question_id"],"current_parent_rank":before,"aggregated_parent_rank":after,"rank_change":b-a,"gold_raw_chunk_hits":[{"rank":x["raw_chunk_rank"],"vector_chunk_id":x["vector_chunk_id"],"score":x["score"]} for x in gold_chunks],"gold_shapes":[shape[int(i)] for i in item["gold_section_ids"]],"failure_category":kind}
        split_rows.append(record)
        if kind:failures.append(record)
    promotions=[];chunk_counts=[]
    for item in eligible:
        best={int(x["section_id"]):x for x in vector[BASE][item["question_id"]]};support={int(x["section_id"]):x for x in vector[CHOSEN][item["question_id"]]}
        for section_id in set(best)&set(support):
            promotion=int(best[section_id]["parent_rank"])-int(support[section_id]["parent_rank"]);count=int(support[section_id]["parent_chunk_count"]);promotions.append(promotion);chunk_counts.append(count)
    def top_bias(key):
        values=[x for item in eligible for x in vector[key][item["question_id"]][:10]]
        return {"candidate_count":len(values),"average_parent_chunk_count":statistics.fmean(int(x["parent_chunk_count"]) for x in values),"long_share":sum(int(x["body_length"])>8000 for x in values)/len(values),"multi_contributor_share":sum(int(x["contributing_chunk_count"])>1 for x in values)/len(values)}
    common=["fts20_db_ms","fts_context_ms","query_embedding_ms","raw_vector100_db_and_context_ms"]
    latency={name:lat([r["timing"][name] for r in rows.values() if r.get("timing")]) for name in next(r for r in rows.values() if r.get("timing"))["timing"]}
    for key in vector:
        latency[key]={"aggregation":lat([r["variants"][key]["aggregation_ms"] for r in rows.values() if r.get("variants")]),"rrf":lat([r["variants"][key]["rrf_ms"] for r in rows.values() if r.get("variants")]),"reranker":lat([r["variants"][key]["reranker_ms"] for r in rows.values() if r.get("variants")]),"parent_pipeline_total":lat([sum(r["timing"][n] for n in common)+r["variants"][key]["aggregation_ms"]+r["variants"][key]["rrf_ms"]+r["variants"][key]["reranker_ms"] for r in rows.values() if r.get("variants")])}
    return {"status":"internal_candidate_experiment","chosen_variant":CHOSEN,"policy":raw["policy"],"aggregation":raw["aggregation"],"provider_stats":raw["provider_stats"],"cache":raw["cache"],"vector_metrics":vector_metrics,"hybrid_metrics":{"legacy_reported_baseline":legacy,"same_run_best_chunk":variants[BASE],"parent_aggregated":variants[CHOSEN],"all_variants":variants,"shape_metrics":hybrid_shape_metrics},"split_rank_changes":dict(changes),"split_threshold_entries":dict(threshold),"split_multiple_gold_chunk_hits":multiple,"split_question_analysis":split_rows,"failure_taxonomy":dict(Counter(x["failure_category"] for x in failures)),"failures":failures,"bias":{"rank_promotion_chunk_count_correlation":corr(chunk_counts,promotions),"best_top10":top_bias(BASE),"support2_top10":top_bias(CHOSEN),"max_support_bonus":raw["aggregation"]["max_support_bonus"]},"latency":latency}


def line(label,m):return f"| {label} | {m['top1']:.2%} | {m['recall_at_3']:.2%} | {m['recall_at_5']:.2%} | {m['recall_at_10']:.2%} | {m.get('recall_at_20',0):.2%} | {m['mrr']:.4f} |"
def md(r):
    v=r["vector_metrics"];h=r["hybrid_metrics"];lines=["# Parent-section aggregation experiment","","> Diagnostic/internal candidate experiment. Gold, embeddings, chunks, FTS, Vector model, RRF, and reranker scoring were unchanged.","","Chosen experimental variant: raw chunk Top-50 + bounded Top-2 support. Score = best similarity + support bonus capped at 0.02.","","## Vector-only","","| path | Top1 | R@3 | R@5 | R@10 | R@20 | MRR |","|---|---:|---:|---:|---:|---:|---:|",line("current exact",v["current"]["overall"]),line("parent support2",v[CHOSEN]["overall"]),"", "## Split/unsplit","","| subset/path | Top1 | R@3 | R@5 | R@10 | R@20 | MRR |","|---|---:|---:|---:|---:|---:|---:|",line("split current",v["current"]["split"]),line("split aggregated",v[CHOSEN]["split"]),line("unsplit current",v["current"]["unsplit"]),line("unsplit aggregated",v[CHOSEN]["unsplit"]),""]
    for title,key in (("Hybrid same-run best chunk","same_run_best_chunk"),("Hybrid parent aggregated","parent_aggregated")):
        s=h[key]["overall"]["section"];lines.append(f"- {title}: Top1 {s['top1']:.2%}, R@5 {s['recall_at_5']:.2%}, R@10 {s['recall_at_10']:.2%}, MRR {s['mrr']:.4f}")
    lines += ["", "## Hybrid split/unsplit", "", "| subset/path | Top1 | R@3 | R@5 | R@10 | R@20 | MRR |", "|---|---:|---:|---:|---:|---:|---:|", line("split best chunk",h["shape_metrics"][BASE]["split"]),line("split aggregated",h["shape_metrics"][CHOSEN]["split"]),line("unsplit best chunk",h["shape_metrics"][BASE]["unsplit"]),line("unsplit aggregated",h["shape_metrics"][CHOSEN]["unsplit"])]
    lines += ["", "## Rank movement and bias", "", f"- Split rank changes: {r['split_rank_changes']}", f"- New threshold entries: {r['split_threshold_entries']}", f"- Split gold parents with at least two raw Top-50 chunk hits: {r['split_multiple_gold_chunk_hits']}", f"- Chunk-count/rank-promotion correlation: {r['bias']['rank_promotion_chunk_count_correlation']:.4f}", f"- Long-section share in Top-10: {r['bias']['best_top10']['long_share']:.2%} -> {r['bias']['support2_top10']['long_share']:.2%}", "", "## Remaining split failures", ""]
    lines.extend(f"- {key}: {value}" for key,value in sorted(r["failure_taxonomy"].items()))
    t=r["latency"][CHOSEN];lines += ["", "## Latency", "", f"- Parent aggregation avg/P95: {t['aggregation']['average_ms']:.3f}/{t['aggregation']['p95_ms']:.3f} ms", f"- RRF avg/P95: {t['rrf']['average_ms']:.3f}/{t['rrf']['p95_ms']:.3f} ms", f"- Reranker avg/P95: {t['reranker']['average_ms']:.3f}/{t['reranker']['p95_ms']:.3f} ms", f"- End-to-end avg/P50/P95/max: {t['parent_pipeline_total']['average_ms']:.2f}/{t['parent_pipeline_total']['p50_ms']:.2f}/{t['parent_pipeline_total']['p95_ms']:.2f}/{t['parent_pipeline_total']['max_ms']:.2f} ms", "", "> The maximum includes a query-embedding API outlier; aggregation itself is sub-millisecond."]
    lines += ["", "## Cache-correctness note", "", "The same-run baseline includes FTS/vector ranks in the deterministic reranker cache key. Historical reports are retained separately because the previous cache key could reuse a score computed under different ranks; the scoring formula did not change."]
    lines += ["","Parent aggregation is not adopted as default: it adds one split Top-10 hit but no split Top-5/Top-1 improvement, slightly lowers overall Vector R@5, and does not materially solve split ranking.",""]
    return "\n".join(lines)
def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--input",type=Path,default=Path("artifacts/parent_section_aggregation.json"));p.add_argument("--legacy",type=Path,default=Path("reports/periodic_full_reranker_baseline.json"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));a=p.parse_args()
    if not a.database_url:p.error("DATABASE_URL required")
    gold=read_jsonl(a.gold);raw=json.loads(a.input.read_text(encoding="utf-8"));legacy=json.loads(a.legacy.read_text(encoding="utf-8"))["stage_evaluations"]["reranked"]
    with connect(a.database_url) as c:shape=shapes(c,gold)
    r=build(gold,raw,shape,legacy);Path("reports/parent_section_aggregation.json").write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");Path("reports/parent_section_aggregation.md").write_text(md(r),encoding="utf-8");Path("reports/parent_section_split_analysis.json").write_text(json.dumps(r["split_question_analysis"],ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");Path("reports/parent_section_failure_analysis.json").write_text(json.dumps({"taxonomy":r["failure_taxonomy"],"failures":r["failures"],"bias":r["bias"]},ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");print(json.dumps({"chosen":r["chosen_variant"],"changes":r["split_rank_changes"],"failures":r["failure_taxonomy"]},indent=2))
if __name__=="__main__":main()
