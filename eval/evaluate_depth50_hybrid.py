"""Evaluate bounded pruning and fixed reranking over the Depth-50 Hybrid pool."""

from __future__ import annotations

import argparse,json,math,statistics,os
from collections import Counter
from pathlib import Path

from eval.evaluate_full_retrieval import evaluate,read_jsonl
from src.db.load_postgres import connect

DEPTHS=(10,20,30)
def _ids(rows,k=None):return [int(x["section_id"]) for x in (rows if k is None else rows[:k])]
def _hit(rows,gold,k=None):return bool(set(_ids(rows,k))&gold)
def _pct(v,p):return sorted(v)[max(0,math.ceil(len(v)*p)-1)] if v else None
def _lat(v):return {"count":len(v),"average_ms":statistics.fmean(v) if v else None,"p50_ms":_pct(v,.5),"p95_ms":_pct(v,.95),"max_ms":max(v) if v else None}


def _prediction(row,depth):
    if row.get("excluded"):return {"question_id":row["question_id"],"error":"excluded_table_only_target"}
    value=row["variants"][str(depth)];return {"question_id":row["question_id"],"section_results":value["final_results"],"table_results":[],"latency_ms":value["total_ms"],"error":None}


def section_shapes(connection,gold):
    ids=sorted({int(x) for item in gold for x in item["gold_section_ids"]});rows=connection.execute("SELECT s.section_id,length(s.body_text),count(c.vector_chunk_id) FROM document_sections s LEFT JOIN section_vector_chunks c USING(section_id) WHERE s.section_id=ANY(%s) GROUP BY s.section_id",(ids,)).fetchall();return {int(i):{"long":int(n or 0)>8000,"split":int(c)>1} for i,n,c in rows}


def build(gold,raw,baseline,shapes):
    rows={x["question_id"]:x for x in raw["rows"]};eligible=[x for x in gold if x["retrieval_target"]!="table"]
    evaluations={str(d):evaluate(gold,[_prediction(rows[x["question_id"]],d) for x in gold]) for d in DEPTHS}
    counts={"fts50_total":sum(len(rows[x["question_id"]]["fts_results"]) for x in eligible),"vector50_total":sum(len(rows[x["question_id"]]["vector_results"]) for x in eligible),"union_total":sum(rows[x["question_id"]]["union_candidate_count"] for x in eligible),"questions":len(eligible)}
    stages=Counter();top20_fail=[];tracking={str(d):Counter() for d in DEPTHS}
    for item in eligible:
        row=rows[item["question_id"]];g=set(map(int,item["gold_section_ids"]));f20=_hit(row["fts_results"],g,20);v20=_hit(row["vector_results"],g,20);union50=_hit(row["fts_results"],g,50) or _hit(row["vector_results"],g,50)
        stages["union50_gold"]+=union50
        if not (f20 or v20):top20_fail.append(item["question_id"])
        for d in DEPTHS:
            rrf=_hit(row["rrf_candidates"],g,d);final=row["variants"][str(d)]["final_results"]
            stages[f"rrf{d}_gold"]+=rrf
            if item["question_id"] in top20_fail:
                tracking[str(d)]["top20_failures"]+=1;tracking[str(d)]["recovered_in_union50"]+=union50;tracking[str(d)]["retained_after_pruning"]+=rrf;tracking[str(d)]["final_top10"]+=_hit(final,g,10);tracking[str(d)]["final_top5"]+=_hit(final,g,5);tracking[str(d)]["final_top3"]+=_hit(final,g,3);tracking[str(d)]["final_top1"]+=_hit(final,g,1)
    shape_eval={}
    for group,predicate in (("split",lambda s:s["split"]),("unsplit",lambda s:not s["split"]),("long",lambda s:s["long"])):
        subset=[x for x in gold if x["retrieval_target"]!="table" and any(predicate(shapes.get(int(i),{"split":False,"long":False})) for i in x["gold_section_ids"])]
        shape_eval[group]={str(d):evaluate(subset,[_prediction(rows[x["question_id"]],d) for x in subset])["overall"] for d in DEPTHS}
    latency={"fts50_db":_lat([r["timing"]["fts_db_ms"] for r in rows.values() if r.get("timing")]),"query_embedding":_lat([r["timing"]["query_embedding_ms"] for r in rows.values() if r.get("timing")]),"vector50_db":_lat([r["timing"]["vector_db_ms"] for r in rows.values() if r.get("timing")]),"rrf":_lat([r["timing"]["rrf_ms"] for r in rows.values() if r.get("timing")]),"context_fetch":_lat([r["timing"]["context_fetch_ms"] for r in rows.values() if r.get("timing")])}
    cache={}
    for d in DEPTHS:
        latency[f"pruning_{d}"]=_lat([r["variants"][str(d)]["pruning_ms"] for r in rows.values() if r.get("variants")]);latency[f"reranker_{d}"]=_lat([r["variants"][str(d)]["reranker_ms"] for r in rows.values() if r.get("variants")]);latency[f"total_{d}"]=_lat([r["variants"][str(d)]["total_ms"] for r in rows.values() if r.get("variants")]);cache[str(d)]={"hits":sum(r["variants"][str(d)]["cache_hits"] for r in rows.values() if r.get("variants")),"misses":sum(r["variants"][str(d)]["cache_misses"] for r in rows.values() if r.get("variants"))}
    failures={}
    for d in DEPTHS:
        c=Counter();details=[]
        for item in eligible:
            row=rows[item["question_id"]];g=set(map(int,item["gold_section_ids"]));union=_hit(row["fts_results"],g,50) or _hit(row["vector_results"],g,50);rrf=_hit(row["rrf_candidates"],g,d);final=row["variants"][str(d)]["final_results"]
            if not union:kind="not_in_top50_union"
            elif not rrf:kind="lost_in_rrf_pruning"
            elif not _hit(final,g,10):kind="reranker_misrank"
            else:continue
            shape=[shapes.get(int(i),{}) for i in item["gold_section_ids"]];subtype="ambiguous_gold" if item["human_review_status"]=="ambiguous" else "split_or_parent_issue" if any(x.get("split") for x in shape) else "long_section" if any(x.get("long") for x in shape) else "same_company_similar_section" if item["category"]=="ambiguous_similar_sections" else "insufficient_chunk_context"
            c[kind]+=1;c[subtype]+=1;details.append({"question_id":item["question_id"],"failure_category":kind,"failure_subtype":subtype})
        failures[str(d)]={"taxonomy":dict(c),"questions":details}
    return {"status":"internal_candidate_experiment","policy":raw["policy"],"provider_stats":raw["provider_stats"],"candidate_counts":{**counts,"fts50_average":counts["fts50_total"]/counts["questions"],"vector50_average":counts["vector50_total"]/counts["questions"],"union_average":counts["union_total"]/counts["questions"]},"stage_gold_counts":dict(stages),"top20_failure_tracking":{k:dict(v) for k,v in tracking.items()},"baseline":baseline,"evaluations":evaluations,"shape_subsets":shape_eval,"latency":latency,"cache":cache,"failure_analysis":failures}


def metric(label,r):
    s=r["section"];q=r["receipt"];return f"| {label} | {s['top1']:.2%} | {s['recall_at_3']:.2%} | {s['recall_at_5']:.2%} | {s['recall_at_10']:.2%} | {s['mrr']:.4f} | {q['top1']:.2%} | {q['recall_at_5']:.2%} | {q['recall_at_10']:.2%} |"
def md(r):
    lines=["# Depth-50 Hybrid candidate expansion","","> Full 120 is internal/candidate. Gold, retrievers, RRF k=60, and deterministic reranker scoring were unchanged.","",f"Average FTS/Vector/union candidates: {r['candidate_counts']['fts50_average']:.2f} / {r['candidate_counts']['vector50_average']:.2f} / {r['candidate_counts']['union_average']:.2f}.","",f"Union50 gold: {r['stage_gold_counts']['union50_gold']}; RRF Top10/20/30 gold: {r['stage_gold_counts']['rrf10_gold']}/{r['stage_gold_counts']['rrf20_gold']}/{r['stage_gold_counts']['rrf30_gold']}.",""]
    for title,key in (("Reviewed regression 36","reviewed_only"),("Full candidate 120","overall")):
        lines += [f"## {title}","","| pipeline | Top1 | R@3 | R@5 | R@10 | MRR | Receipt Top1 | R@5 | R@10 |","|---|---:|---:|---:|---:|---:|---:|---:|---:|",metric("depth20-rerank10",r["baseline"][key])]
        for d in DEPTHS:lines.append(metric(f"depth50-rerank{d}",r["evaluations"][str(d)][key]))
        lines.append("")
    lines += ["## Pruning and decision","",f"Union50 contains {r['stage_gold_counts']['union50_gold']} gold-bearing questions; RRF Top10/20/30 retains {r['stage_gold_counts']['rrf10_gold']}/{r['stage_gold_counts']['rrf20_gold']}/{r['stage_gold_counts']['rrf30_gold']}.","","Expanded-30 has the best measured R@10/MRR with only a small reranker-latency increase, so it is the preferred experimental setting among the four choices. However, none of the 14 questions newly found beyond the old Top-20 union reached final Top-10. Depth-50 retrieval itself is therefore not yet proven as the cause of the metric gain; do not promote it as production default.",""]
    return "\n".join(lines)
def comparison(r):
    lines=["# Depth-50 stage comparison","","> Metrics are internal/candidate results.",""]
    for grouping,title in (("by_difficulty","Difficulty"),("by_category","Category")):
        lines += [f"## {title}","","| group | expanded-10 Top1/R5/R10 | expanded-20 | expanded-30 |","|---|---:|---:|---:|"]
        for group in r["evaluations"]["10"][grouping]:
            vals=[]
            for d in DEPTHS:
                s=r["evaluations"][str(d)][grouping][group]["section"];vals.append("n/a" if s["top1"] is None else f"{s['top1']:.1%}/{s['recall_at_5']:.1%}/{s['recall_at_10']:.1%}")
            lines.append(f"| {group} | {' | '.join(vals)} |")
        lines.append("")
    return "\n".join(lines)
def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--input",type=Path,default=Path("artifacts/depth50_hybrid_experiment.json"));p.add_argument("--baseline",type=Path,default=Path("reports/periodic_full_reranker_baseline.json"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));a=p.parse_args()
    if not a.database_url:p.error("DATABASE_URL required")
    gold=read_jsonl(a.gold);raw=json.loads(a.input.read_text(encoding="utf-8"));base=json.loads(a.baseline.read_text(encoding="utf-8"))["stage_evaluations"]["reranked"]
    with connect(a.database_url) as c:shapes=section_shapes(c,gold)
    r=build(gold,raw,base,shapes);Path("reports/depth50_hybrid_experiment.json").write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");Path("reports/depth50_hybrid_experiment.md").write_text(md(r),encoding="utf-8");Path("reports/depth50_pruning_analysis.json").write_text(json.dumps({"stage_gold_counts":r["stage_gold_counts"],"top20_failure_tracking":r["top20_failure_tracking"],"failure_analysis":r["failure_analysis"],"cache":r["cache"]},ensure_ascii=False,indent=2)+"\n",encoding="utf-8");Path("reports/depth50_stage_comparison.md").write_text(comparison(r),encoding="utf-8");print(json.dumps({"stage":r["stage_gold_counts"],"tracking":r["top20_failure_tracking"]},indent=2))
if __name__=="__main__":main()
