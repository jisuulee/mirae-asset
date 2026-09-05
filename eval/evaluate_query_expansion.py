"""Evaluate query-expansion candidate recall, drift, final ranking, and failures."""

from __future__ import annotations

import json, math, statistics
from collections import Counter, defaultdict
from pathlib import Path

from eval.evaluate_full_retrieval import evaluate, read_jsonl
from src.retrieval.hybrid_sections import reciprocal_rank_fusion
from src.retrieval.query_expansion import track_rank_change

VARIANTS=("original","spacing","title_path","combined")


def rank(rows,gold):return next((i for i,x in enumerate(rows,1) if int(x["section_id"]) in gold),None)
def metrics(items,results):
    ranks=[rank(results[x["question_id"]],set(map(int,x["gold_section_ids"]))) for x in items];n=len(ranks)
    output={"questions":n,"top1":sum(x==1 for x in ranks)/n,"mrr":sum(1/x if x else 0 for x in ranks)/n}
    for k in (3,5,10,20,50,100):output[f"recall_at_{k}"]=sum(bool(x and x<=k) for x in ranks)/n
    return output
def pct(values,p):return sorted(values)[max(0,math.ceil(len(values)*p)-1)] if values else None
def latency(values):return {"average_ms":statistics.fmean(values),"p50_ms":pct(values,.5),"p95_ms":pct(values,.95),"max_ms":max(values)} if values else {}
def predictions(gold,rows,key):
    output=[]
    for item in gold:
        row=rows[item["question_id"]]
        output.append({"question_id":item["question_id"],"section_results":[] if row.get("excluded") else row["variants"][key]["final_results"],"table_results":[],"error":"excluded_table_only_target" if row.get("excluded") else None})
    return output


def build(gold,raw,ceiling,parent):
    rows={x["question_id"]:x for x in raw["rows"]};eligible=[x for x in gold if x["retrieval_target"]!="table"]
    stages={};final={};expansion_counts={}
    for key in VARIANTS:
        fts={x["question_id"]:rows[x["question_id"]]["variants"][key]["fts_results"] for x in eligible}
        vector={x["question_id"]:rows[x["question_id"]]["variants"][key]["vector_results"] for x in eligible}
        union={x["question_id"]:reciprocal_rank_fusion(fts[x["question_id"]],vector[x["question_id"]],top_k=100,rrf_k=60) for x in eligible}
        final[key]={x["question_id"]:rows[x["question_id"]]["variants"][key]["final_results"] for x in eligible}
        stages[key]={"fts":metrics(eligible,fts),"vector":metrics(eligible,vector),"union":metrics(eligible,union),"final":evaluate(gold,predictions(gold,rows,key)),"results":{"fts":fts,"vector":vector,"union":union}}
        expansion_counts[key]=[len(rows[x["question_id"]]["variants"][key]["expansions"]) for x in eligible]
    diagnostics={x["question_id"]:x for x in ceiling["question_diagnostics"]};neither={qid for qid,x in diagnostics.items() if x.get("ceiling_bucket")=="not_found_top100"}
    semantic={qid for qid,x in diagnostics.items() if "semantic_mismatch" in x.get("diagnostic_tags",[])};lexical={qid for qid,x in diagnostics.items() if "lexical_mismatch" in x.get("diagnostic_tags",[])}
    no_chunk={x["question_id"] for x in parent["split_question_analysis"] if x.get("failure_category")=="no_gold_chunk_retrieved"};split={x["question_id"] for x in parent["split_question_analysis"]}
    subsets={"top100_neither":neither,"semantic_mismatch":semantic,"lexical_mismatch":lexical,"no_gold_chunk_retrieved":no_chunk,"split_long":split,"ambiguous_similar_sections":{x["question_id"] for x in eligible if x["category"]=="ambiguous_similar_sections"}}
    subset_results={}
    for name,ids in subsets.items():
        items=[x for x in eligible if x["question_id"] in ids];recovered=[]
        for item in items:
            q=item["question_id"];g=set(map(int,item["gold_section_ids"]));original=rank(stages["original"]["results"]["union"][q],g);expanded=rank(stages["combined"]["results"]["union"][q],g);final_rank=rank(final["combined"][q],g)
            if not original and expanded:recovered.append({"question_id":q,"candidate_rank":expanded,"final_rank":final_rank})
        subset_results[name]={"questions":len(items),"candidate_recovered":len(recovered),"final_top10":sum(bool(x["final_rank"]) for x in recovered),"final_top5":sum(bool(x["final_rank"] and x["final_rank"]<=5) for x in recovered),"final_top3":sum(bool(x["final_rank"] and x["final_rank"]<=3) for x in recovered),"final_top1":sum(x["final_rank"]==1 for x in recovered),"recovered":recovered}
    movement=Counter();top10_lost=0;question_rows=[];failures=[]
    for item in eligible:
        q=item["question_id"];g=set(map(int,item["gold_section_ids"]));before=rank(stages["original"]["results"]["union"][q],g);after=rank(stages["combined"]["results"]["union"][q],g);b=before or 101;a=after or 101
        move=track_rank_change(before,after);movement[move]+=1;top10_lost+=bool(before and before<=10 and (not after or after>10))
        final_rank=rank(final["combined"][q],g);hybrid_rank=rank(rows[q]["variants"]["combined"]["hybrid_results"],g)
        if not after:failure="ambiguous_gold" if item["human_review_status"]=="ambiguous" else "still_no_candidate"
        elif not hybrid_rank:failure="candidate_found_but_low_rank"
        elif not final_rank:failure="reranker_misrank"
        elif move=="worsened":failure="expansion_drift"
        else:failure=None
        record={"question_id":q,"query":item["question"],"category":item["category"],"difficulty":item["difficulty"],"expansions":rows[q]["variants"]["combined"]["expansions"],"original_union_gold_rank":before,"expanded_union_gold_rank":after,"rank_movement":move,"expanded_hybrid_gold_rank":hybrid_rank,"expanded_final_gold_rank":final_rank,"failure_category":failure}
        question_rows.append(record)
        if failure:failures.append(record)
    group={}
    for field in ("category","difficulty"):
        group[field]={}
        for value in sorted({x[field] for x in eligible}):
            items=[x for x in eligible if x[field]==value];group[field][value]={key:{"original":metrics(items,final["original"])[key],"combined":metrics(items,final["combined"])[key]} for key in ("top1","recall_at_5","recall_at_10","mrr")}
    timing_names=("expansion_ms","embedding_ms","fts_all_queries_ms","vector_all_queries_ms")
    timings={name:latency([rows[x["question_id"]]["timing"][name] for x in eligible]) for name in timing_names}
    for name in ("multi_query_fusion_ms","rrf_ms","reranker_ms"):timings[name]=latency([rows[x["question_id"]]["variants"]["combined"]["timing"][name] for x in eligible])
    totals=[sum(rows[x["question_id"]]["timing"].values())+sum(rows[x["question_id"]]["variants"]["combined"]["timing"].values()) for x in eligible];timings["diagnostic_total_ms"]=latency(totals)
    public_stages={key:{name:({k:v for k,v in value.items() if k!="failures"} if name=="final" else value) for name,value in data.items() if name!="results"} for key,data in stages.items()}
    provider=dict(raw["provider_stats"]);provider["estimated_cost_krw_vat_excluded_at_0_0002_per_token"]=provider.get("input_tokens",0)*0.0002
    return {"status":"internal_candidate_diagnostic","policy":raw["policy"],"provider_stats":provider,"cache":raw["cache"],"expansion_counts":{key:{"average":statistics.fmean(value),"max":max(value),"zero":sum(x==0 for x in value)} for key,value in expansion_counts.items()},"stages":public_stages,"subsets":subset_results,"rank_movement":dict(movement),"original_top10_lost":top10_lost,"by_group":group,"failure_taxonomy":dict(Counter(x["failure_category"] for x in failures)),"question_diagnostics":question_rows,"failures":failures,"latency":timings,"decision":{"default":"do_not_adopt","safe_partial":"spacing-only remains diagnostic; it does not improve the Top-100 ceiling","title_path":"candidate recall improves but final ranking and reviewed metrics drift","next":"title/path dedicated retrieval before a rule-gated HCX rewrite experiment","retrieval_freeze":False}}


def markdown(r):
    def row(label,m):return f"| {label} | {m['top1']:.2%} | {m['recall_at_5']:.2%} | {m['recall_at_10']:.2%} | {m['recall_at_100']:.2%} | {m['mrr']:.4f} |"
    lines=["# Query expansion diagnostic","","> Deterministic, corpus-grounded internal/candidate experiment. Benchmark gold was not used or modified.","","## Candidate retrieval","","| path | Top1 | R@5 | R@10 | R@100 | MRR |","|---|---:|---:|---:|---:|---:|"]
    for source in ("fts","vector","union"):lines += [row(f"Original {source}",r["stages"]["original"][source]),row(f"Combined {source}",r["stages"]["combined"][source])]
    lines += ["","## Final Hybrid + deterministic reranker","","| variant | Top1 | R@5 | R@10 | MRR |","|---|---:|---:|---:|---:|"]
    for key in VARIANTS:
        s=r["stages"][key]["final"]["overall"]["section"];lines.append(f"| {key} | {s['top1']:.2%} | {s['recall_at_5']:.2%} | {s['recall_at_10']:.2%} | {s['mrr']:.4f} |")
    lines += ["","## Recovery subsets",""]
    for key,value in r["subsets"].items():lines.append(f"- {key}: {value['candidate_recovered']} candidate recoveries; final Top10 {value['final_top10']} / Top5 {value['final_top5']} / Top1 {value['final_top1']}")
    p=r["provider_stats"];lines += ["",f"Rank movement: {r['rank_movement']}; original Top10 lost: {r['original_top10_lost']}.","",f"Failure taxonomy: {r['failure_taxonomy']}.","","## Cost and decision","",f"- Query embeddings generated: {p['successes']}; attempts: {p['requests']}; retries: {p['retries']}",f"- Input tokens: {p['input_tokens']}; estimated VAT-exclusive cost: {p['estimated_cost_krw_vat_excluded_at_0_0002_per_token']:.4f} KRW",f"- Decision: {r['decision']['default']}",f"- Next: {r['decision']['next']}",""]
    return "\n".join(lines)


def main():
    gold=read_jsonl(Path("eval/periodic_full_benchmark.jsonl"));raw=json.load(open("artifacts/query_expansion_diagnostic.json",encoding="utf-8"));ceiling=json.load(open("reports/candidate_recall_ceiling.json",encoding="utf-8"));parent=json.load(open("reports/parent_section_aggregation.json",encoding="utf-8"));r=build(gold,raw,ceiling,parent)
    Path("reports/query_expansion_diagnostic.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");Path("reports/query_expansion_diagnostic.md").write_text(markdown(r),encoding="utf-8");Path("reports/query_expansion_failures.json").write_text(json.dumps({"taxonomy":r["failure_taxonomy"],"failures":r["failures"]},ensure_ascii=False,indent=2)+"\n",encoding="utf-8");Path("reports/query_expansion_stage_comparison.md").write_text(markdown(r),encoding="utf-8");print(json.dumps({"movement":r["rank_movement"],"subsets":r["subsets"],"failures":r["failure_taxonomy"]},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
