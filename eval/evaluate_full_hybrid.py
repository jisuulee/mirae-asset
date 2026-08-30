"""Evaluate FTS, Exact Vector, and RRF Hybrid from one shared experiment."""

from __future__ import annotations

import argparse, json, math, statistics
from collections import Counter
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import evaluate, read_jsonl


def _pct(values: list[float], p: float): return sorted(values)[max(0,math.ceil(len(values)*p)-1)] if values else None
def _lat(values):
    values=[float(x) for x in values]
    return {"count":len(values),"average_ms":statistics.fmean(values) if values else None,"p50_ms":_pct(values,.5),"p95_ms":_pct(values,.95),"max_ms":max(values) if values else None}
def _ids(results, k): return [int(x["section_id"]) for x in results[:k]]


def prediction(row: dict[str,Any], mode: str) -> dict[str,Any]:
    if row.get("excluded"): return {"question_id":row["question_id"],"error":"excluded_table_only_target"}
    if mode == "fts": results=row["fts_results"];lat=row["timings"]["fts_db_ms"]
    elif mode == "vector": results=row["vector_results"];lat=row["timings"]["vector_db_ms"]
    else: results=row["variants"][mode]["section_results"];lat=row["variants"][mode]["total_ms"]
    return {"question_id":row["question_id"],"section_results":results[:10],"table_results":[],"latency_ms":lat,"error":row.get("error")}


def build(gold, raw, keyword_failures):
    by_gold={x["question_id"]:x for x in gold}; by_row={x["question_id"]:x for x in raw["rows"]}
    variants=sorted(next(x for x in raw["rows"] if x.get("variants"))["variants"])
    modes=["fts","vector",*variants]
    evaluations={m:evaluate(gold,[prediction(r,m) for r in raw["rows"]]) for m in modes}
    complement={}
    primary=raw["experiment"]["primary"]
    diagnostics=[]
    for k in (5,10):
        counts=Counter();preserve=Counter();both_promoted=0
        for item in gold:
            if item["retrieval_target"]=="table":continue
            row=by_row[item["question_id"]];expected=set(map(int,item["gold_section_ids"]))
            fh=bool(expected & set(_ids(row["fts_results"],k)));vh=bool(expected & set(_ids(row["vector_results"],k)))
            hh=bool(expected & set(_ids(row["variants"][primary]["section_results"],k)))
            group="both" if fh and vh else "fts_only" if fh else "vector_only" if vh else "neither";counts[group]+=1
            if hh: preserve[group]+=1
            def rank(results): return next((i for i,x in enumerate(results[:k],1) if int(x["section_id"]) in expected),None)
            fr,vr,hr=rank(row["fts_results"]),rank(row["vector_results"]),rank(row["variants"][primary]["section_results"])
            if group=="both" and hr is not None and hr < min(fr,vr):both_promoted+=1
            if k==10: diagnostics.append({"question_id":item["question_id"],"category":item["category"],"difficulty":item["difficulty"],"candidate_group":group,"fts_gold_rank":fr,"vector_gold_rank":vr,"hybrid_gold_rank":hr,"hybrid_hit":hh})
        complement[str(k)]={"candidate_gold":dict(counts),"hybrid_preserved":dict(preserve),"both_gold_promoted":both_promoted}
    recover={}
    for taxonomy in ("semantic_paraphrase","lexical_mismatch","korean_spacing","tokenizer","ranking"):
        qids={x["question_id"] for x in keyword_failures if x["failure_category"]==taxonomy}
        recover[taxonomy]={"fts_failures":len(qids)}
        for mode in ("vector",primary):
            scored={x["question_id"]:x for x in evaluations[mode]["failures"]}
            recover[taxonomy]["vector_recovered" if mode=="vector" else "hybrid_recovered"]=len(qids-set(scored))
        vector_failed={x["question_id"] for x in evaluations["vector"]["failures"]}
        hybrid_failed={x["question_id"] for x in evaluations[primary]["failures"]}
        vector_recovered=qids-vector_failed
        recover[taxonomy]["hybrid_lost_vector_recoveries"]=len(vector_recovered & hybrid_failed)
    for label,qids in {
        "hard": {x["question_id"] for x in keyword_failures if by_gold[x["question_id"]]["difficulty"]=="hard"},
        "pdf": {x["question_id"] for x in keyword_failures if by_gold[x["question_id"]]["retrieval_target"]=="pdf_page"},
    }.items():
        vector_failed={x["question_id"] for x in evaluations["vector"]["failures"]};hybrid_failed={x["question_id"] for x in evaluations[primary]["failures"]}
        recover[label]={"fts_failures":len(qids),"vector_recovered":len(qids-vector_failed),"hybrid_recovered":len(qids-hybrid_failed),"hybrid_lost_vector_recoveries":len((qids-vector_failed)&hybrid_failed)}
    failures=[]
    scored_primary={x["question_id"]:x for x in evaluations[primary]["failures"]}
    for qid,scored in scored_primary.items():
        item=by_gold[qid];row=by_row[qid];gold_ids=set(map(int,item["gold_section_ids"]));f=bool(gold_ids&set(_ids(row["fts_results"],20)));v=bool(gold_ids&set(_ids(row["vector_results"],20)))
        if item["human_review_status"]=="ambiguous":kind="gold_ambiguity"
        elif item["retrieval_target"]=="pdf_page":kind="pdf"
        elif not f and not v:kind="neither_retriever_found_gold"
        elif f and v:kind="fusion_demoted_gold"
        elif f:kind="vector_dominated"
        else:kind="fts_dominated"
        failures.append({**scored,"hybrid_failure_category":kind,"fts_candidate_has_gold":f,"vector_candidate_has_gold":v,"automatic_taxonomy":True})
    timings={name:_lat([r["timings"][key] for r in raw["rows"] if r.get("timings")]) for name,key in (("fts_db","fts_db_ms"),("query_embedding","query_embedding_ms"),("exact_vector_db","vector_db_ms"))}
    timings["fusion"]=_lat([r["variants"][primary]["fusion_ms"] for r in raw["rows"] if r.get("variants")]);timings["total"]=_lat([r["variants"][primary]["total_ms"] for r in raw["rows"] if r.get("variants")])
    timing_profiles={}
    for item in gold:
        row=by_row[item["question_id"]]
        if not row.get("timings"):continue
        profile=("receipt" if item.get("receipt_no_filter") else "year_report" if item.get("year_filter_required") or item.get("report_type_filter") else "company" if item.get("company_filter_required") else "namespace")
        target=timing_profiles.setdefault(profile,{"fts_db":[],"query_embedding":[],"exact_vector_db":[],"fusion":[],"total":[]})
        target["fts_db"].append(row["timings"]["fts_db_ms"]);target["query_embedding"].append(row["timings"]["query_embedding_ms"]);target["exact_vector_db"].append(row["timings"]["vector_db_ms"]);target["fusion"].append(row["variants"][primary]["fusion_ms"]);target["total"].append(row["variants"][primary]["total_ms"])
    timing_profiles={profile:{name:_lat(values) for name,values in parts.items()} for profile,parts in timing_profiles.items()}
    return {"status":"internal_candidate_baseline","policy":raw["policy"],"experiment":raw["experiment"],"provider_stats":raw["provider_stats"],"evaluations":evaluations,"complementarity":complement,"failure_recovery":recover,"hybrid_failure_taxonomy":dict(Counter(x["hybrid_failure_category"] for x in failures)),"latency":timings,"latency_by_filter":timing_profiles,"question_diagnostics":diagnostics,"failures":failures}


def metric_row(label,result):
    s=result["section"];r=result["receipt"]
    return f"| {label} | {s['top1']:.2%} | {s['recall_at_5']:.2%} | {s['recall_at_10']:.2%} | {s['mrr']:.4f} | {r['top1']:.2%} | {r['recall_at_5']:.2%} | {r['recall_at_10']:.2%} |"


def markdown(report):
    primary=report["experiment"]["primary"];lines=["# FTS + Exact Vector Semantic Hybrid baseline","","> Reviewed 36 is the reviewed regression set. Full 120 is an internal/candidate development benchmark, not an official human-reviewed set.","",f"Primary: FTS Top-20 + Exact Vector Top-20, unweighted RRF k=60 (`{primary}`). HNSW and reranking are not used.",""]
    for title,key in (("Reviewed regression (36)","reviewed_only"),("Full candidate (120; 113 section eligible)","overall")):
        lines += [f"## {title}","","| mode | Section Top-1 | R@5 | R@10 | MRR | Receipt Top-1 | R@5 | R@10 |","|---|---:|---:|---:|---:|---:|---:|---:|"]
        for mode in ("fts","vector",primary):lines.append(metric_row(mode,report["evaluations"][mode][key]))
        lines.append("")
    c=report["complementarity"]["10"];lines += ["## Top-10 complementarity","",f"- FTS only: {c['candidate_gold'].get('fts_only',0)}; Hybrid preserved: {c['hybrid_preserved'].get('fts_only',0)}",f"- Vector only: {c['candidate_gold'].get('vector_only',0)}; Hybrid preserved: {c['hybrid_preserved'].get('vector_only',0)}",f"- Both: {c['candidate_gold'].get('both',0)}; Hybrid preserved: {c['hybrid_preserved'].get('both',0)}",f"- Neither: {c['candidate_gold'].get('neither',0)}",""]
    lines += ["## Latency","","| component | avg | P50 | P95 | max |","|---|---:|---:|---:|---:|"]
    for name,value in report["latency"].items():lines.append(f"| {name} | {value['average_ms']:.2f}ms | {value['p50_ms']:.2f}ms | {value['p95_ms']:.2f}ms | {value['max_ms']:.2f}ms |")
    lines += ["","All failure taxonomy labels are automatic review aids; benchmark gold was not changed.",""]
    return "\n".join(lines)


def comparison_md(report):
    p=report["experiment"]["primary"];lines=["# FTS vs Exact Vector vs Hybrid","","> Full-120 results are internal/candidate results.",""]
    for grouping,title in (("by_category","Category"),("by_difficulty","Difficulty")):
        lines += [f"## {title}","","| group | FTS Top-1/R@10 | Vector Top-1/R@10 | Hybrid Top-1/R@10 |","|---|---:|---:|---:|"]
        for group in report["evaluations"][p][grouping]:
            vals=[]
            for mode in ("fts","vector",p):
                s=report["evaluations"][mode][grouping][group]["section"]
                vals.append("n/a" if s["top1"] is None else f"{s['top1']:.1%}/{s['recall_at_10']:.1%}")
            lines.append(f"| {group} | {' | '.join(vals)} |")
        lines.append("")
    return "\n".join(lines)


def main():
    p=argparse.ArgumentParser();p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--input",type=Path,default=Path("artifacts/periodic_full_hybrid_experiment.json"));p.add_argument("--keyword-failures",type=Path,default=Path("reports/periodic_full_keyword_failures.json"));a=p.parse_args()
    report=build(read_jsonl(a.gold),json.loads(a.input.read_text(encoding="utf-8")),json.loads(a.keyword_failures.read_text(encoding="utf-8")))
    Path("reports/periodic_full_hybrid_baseline.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    Path("reports/periodic_full_hybrid_baseline.md").write_text(markdown(report),encoding="utf-8")
    Path("reports/fts_vector_hybrid_comparison.md").write_text(comparison_md(report),encoding="utf-8")
    Path("reports/periodic_full_hybrid_failures.json").write_text(json.dumps(report["failures"],ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"primary":report["experiment"]["primary"],"failures":len(report["failures"])},indent=2))


if __name__=="__main__":main()
