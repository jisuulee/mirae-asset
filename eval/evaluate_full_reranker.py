"""Evaluate deterministic reranking without changing the Hybrid candidate set."""

from __future__ import annotations

import argparse,json,math,statistics
from collections import Counter
from pathlib import Path

from eval.evaluate_full_retrieval import evaluate,read_jsonl


def pct(v,p):return sorted(v)[max(0,math.ceil(len(v)*p)-1)] if v else None
def latency(v):return {"count":len(v),"average_ms":statistics.fmean(v) if v else None,"p50_ms":pct(v,.5),"p95_ms":pct(v,.95),"max_ms":max(v) if v else None}
def rank(results,gold):return next((i for i,x in enumerate(results,1) if int(x["section_id"]) in gold),None)


def build(gold,hybrid,reranked):
    primary=hybrid["experiment"]["primary"];source={x["question_id"]:x for x in hybrid["rows"]};pred={x["question_id"]:x for x in reranked["rows"]}
    rerank_predictions=[]
    for item in gold:
        row=pred[item["question_id"]]
        rerank_predictions.append({"question_id":item["question_id"],"section_results":row.get("section_results",[])[:10],"table_results":[],"latency_ms":row.get("reranker_latency_ms"),"error":"excluded_table_only_target" if row.get("excluded") else row.get("error")})
    rerank_eval=evaluate(gold,rerank_predictions)
    stage={mode:hybrid["evaluations"][mode] for mode in ("fts","vector",primary)};stage["reranked"]=rerank_eval
    changes=Counter();promotions=Counter();groups=Counter();details=[];failure_rows=[]
    for item in gold:
        if item["retrieval_target"]=="table":continue
        qid=item["question_id"];gold_ids=set(map(int,item["gold_section_ids"]));before=source[qid]["variants"][primary]["section_results"][:10];after=pred[qid]["section_results"][:10];br=rank(before,gold_ids);ar=rank(after,gold_ids)
        if br is None:changes["gold_not_in_candidates"]+=1
        elif ar<br:changes["improved"]+=1
        elif ar==br:changes["unchanged"]+=1
        else:changes["worsened"]+=1
        if br is not None:
            if br>5 and ar<=5:promotions["top10_to_top5"]+=1
            if br>3 and ar<=3:promotions["top10_to_top3"]+=1
            if br>1 and ar==1:promotions["top10_to_top1"]+=1
            if 1<br<=5 and ar==1:promotions["top5_to_top1"]+=1
            if 3<br<=5 and ar<=3:promotions["top5_to_top3"]+=1
            if 1<br<=3 and ar==1:promotions["top3_to_top1"]+=1
            if br==1 and ar>1:promotions["top1_harmed"]+=1
            if br<=3 and ar>3:promotions["top3_dropped_out"]+=1
        diagnostic=next(x for x in hybrid["question_diagnostics"] if x["question_id"]==qid)
        if diagnostic["candidate_group"] in {"fts_only","vector_only"} and br is not None and ar>br:groups[f"{diagnostic['candidate_group']}_worsened"]+=1
        primary_failure="gold_not_in_candidates" if br is None else "reranker_misrank" if ar!=1 else None
        subtype=None
        if primary_failure:
            if item["human_review_status"]=="ambiguous":subtype="gold_ambiguity"
            elif item["retrieval_target"]=="pdf_page":subtype="pdf"
            elif item["category"]=="ambiguous_similar_sections":subtype="similar_sections"
            elif any(int(x["section_id"]) in gold_ids and len(str(x.get("rerank_text") or ""))>=2000 for x in after):subtype="long_section"
            else:subtype="insufficient_chunk_context" if br is not None else "candidate_generation"
            failure_rows.append({"question_id":qid,"hybrid_gold_rank":br,"reranked_gold_rank":ar,"failure_category":primary_failure,"failure_subtype":subtype,"automatic_taxonomy":True,"top_results":after[:3]})
        details.append({"question_id":qid,"category":item["category"],"difficulty":item["difficulty"],"candidate_group":diagnostic["candidate_group"],"hybrid_gold_rank":br,"reranked_gold_rank":ar})
    rerank_lat=[float(x["reranker_latency_ms"]) for x in reranked["rows"] if x.get("reranker_latency_ms") is not None];hybrid_total=hybrid["latency"]["total"]
    total_samples=[]
    for row in reranked["rows"]:
        if row.get("excluded"):continue
        total_samples.append(float(source[row["question_id"]]["variants"][primary]["total_ms"])+float(row["reranker_latency_ms"]))
    return {"status":"internal_candidate_baseline","reranker":reranked["reranker"],"cache":reranked["cache"],"api_usage":{"requests":0,"input_tokens":0,"output_tokens":0,"retries":0,"failures":0,"estimated_cost":0},"stage_evaluations":stage,"rank_changes":dict(changes),"promotions":dict(promotions),"candidate_group_damage":dict(groups),"failure_taxonomy":{"primary":dict(Counter(x["failure_category"] for x in failure_rows)),"subtype":dict(Counter(x["failure_subtype"] for x in failure_rows))},"latency":{"reranker":latency(rerank_lat),"hybrid_before_reranker":hybrid_total,"end_to_end":latency(total_samples)},"question_diagnostics":details,"failures":failure_rows}


def metric(label,r):
    s=r["section"];q=r["receipt"];return f"| {label} | {s['top1']:.2%} | {s['recall_at_3']:.2%} | {s['recall_at_5']:.2%} | {s['recall_at_10']:.2%} | {s['mrr']:.4f} | {q['top1']:.2%} | {q['recall_at_5']:.2%} | {q['recall_at_10']:.2%} |"
def md(r):
    p="d20_k60";lines=["# Deterministic Hybrid reranker baseline","","> Reviewed 36 is regression; full 120 remains internal/candidate. Gold and candidate generation are unchanged.","",f"Model: `{r['reranker']['model']}` / `{r['reranker']['version']}`; rerank depth 10; API calls 0.",""]
    for title,key in (("Reviewed regression 36","reviewed_only"),("Full candidate 120","overall")):
        lines += [f"## {title}","","| stage | Top-1 | R@3 | R@5 | R@10 | MRR | Receipt Top-1 | R@5 | R@10 |","|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for mode in ("fts","vector",p,"reranked"):lines.append(metric(mode,r["stage_evaluations"][mode][key]))
        lines.append("")
    lines += ["## Rank changes","",*(f"- {k}: {v}" for k,v in r["rank_changes"].items()),"", "## Latency","",f"Reranker avg/P50/P95/max: {r['latency']['reranker']['average_ms']:.3f}/{r['latency']['reranker']['p50_ms']:.3f}/{r['latency']['reranker']['p95_ms']:.3f}/{r['latency']['reranker']['max_ms']:.3f} ms.",f"End-to-end avg/P50/P95/max: {r['latency']['end_to_end']['average_ms']:.2f}/{r['latency']['end_to_end']['p50_ms']:.2f}/{r['latency']['end_to_end']['p95_ms']:.2f}/{r['latency']['end_to_end']['max_ms']:.2f} ms.",""]
    return "\n".join(lines)
def comparison(r):
    p="d20_k60";lines=["# Retrieval stage comparison","","> Full-120 figures are internal/candidate results.",""]
    for grouping,title in (("by_category","Category"),("by_difficulty","Difficulty")):
        lines += [f"## {title}","","| group | FTS | Vector | Hybrid | Reranked |","|---|---:|---:|---:|---:|"]
        for group in r["stage_evaluations"]["reranked"][grouping]:
            vals=[]
            for mode in ("fts","vector",p,"reranked"):
                s=r["stage_evaluations"][mode][grouping][group]["section"];vals.append("n/a" if s["top1"] is None else f"{s['top1']:.1%}/{s['recall_at_5']:.1%}/{s['mrr']:.3f}")
            lines.append(f"| {group} | {' | '.join(vals)} |")
        lines.append("")
    return "\n".join(lines)
def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--hybrid-report",type=Path,default=Path("reports/periodic_full_hybrid_baseline.json"));p.add_argument("--hybrid-artifact",type=Path,default=Path("artifacts/periodic_full_hybrid_experiment.json"));p.add_argument("--predictions",type=Path,default=Path("artifacts/periodic_full_reranker_predictions.json"));a=p.parse_args()
    hr=json.loads(a.hybrid_report.read_text(encoding="utf-8"));ha=json.loads(a.hybrid_artifact.read_text(encoding="utf-8"));ha["evaluations"]=hr["evaluations"];ha["question_diagnostics"]=hr["question_diagnostics"];ha["latency"]=hr["latency"]
    r=build(read_jsonl(a.gold),ha,json.loads(a.predictions.read_text(encoding="utf-8")));Path("reports/periodic_full_reranker_baseline.json").write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");Path("reports/periodic_full_reranker_baseline.md").write_text(md(r),encoding="utf-8");Path("reports/retrieval_stage_comparison.md").write_text(comparison(r),encoding="utf-8");Path("reports/periodic_full_reranker_failures.json").write_text(json.dumps(r["failures"],ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");print(json.dumps({"rank_changes":r["rank_changes"],"failures":r["failure_taxonomy"]},indent=2))
if __name__=="__main__":main()
