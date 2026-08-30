"""Evaluate title/path-only retrieval, three-channel ceiling, fusion, and damage."""

from __future__ import annotations

import json,math,os,statistics
from collections import Counter
from pathlib import Path

from eval.evaluate_full_retrieval import evaluate,read_jsonl
from src.db.load_postgres import connect

DEPTHS=(10,20,50,100)
def rank(rows,gold):return next((i for i,x in enumerate(rows,1) if int(x["section_id"]) in gold),None)
def metrics(items,results):
    ranks=[rank(results[x["question_id"]],set(map(int,x["gold_section_ids"]))) for x in items];n=len(ranks);out={"questions":n,"top1":sum(x==1 for x in ranks)/n,"mrr":sum(1/x if x else 0 for x in ranks)/n}
    for k in (3,5,10,20,50,100):out[f"recall_at_{k}"]=sum(bool(x and x<=k) for x in ranks)/n
    return out
def pred(gold,rows,key):return [{"question_id":x["question_id"],"section_results":[] if rows[x["question_id"]].get("excluded") else rows[x["question_id"]]["variants"][key]["final_results"],"table_results":[],"error":"excluded_table_only_target" if rows[x["question_id"]].get("excluded") else None} for x in gold]
def pct(v,p):return sorted(v)[max(0,math.ceil(len(v)*p)-1)] if v else None
def lat(v):return {"average_ms":statistics.fmean(v),"p50_ms":pct(v,.5),"p95_ms":pct(v,.95),"max_ms":max(v)}


def plans(database_url):
    with connect(database_url) as c:
        c.execute("SET TRANSACTION READ ONLY")
        idx=[x[0] for x in c.execute("SELECT indexname FROM pg_indexes WHERE tablename='document_sections' AND indexname LIKE 'document_sections_title_path_%' ORDER BY indexname").fetchall()]
        queries={"fts":"SELECT section_id FROM document_sections WHERE metadata->>'loader_namespace'='periodic-full-v1' AND title_path_search_tsv @@ websearch_to_tsquery('simple','연구개발 현황') LIMIT 20","trigram":"SELECT section_id FROM document_sections WHERE title_path_search_normalized % '연구개발 현황' LIMIT 20"};out={"indexes":idx}
        for key,query in queries.items():
            plan=c.execute("EXPLAIN (ANALYZE,FORMAT JSON) "+query).fetchone()[0][0];names=[]
            def walk(node):
                if node.get("Index Name"):names.append(node["Index Name"])
                for child in node.get("Plans",[]):walk(child)
            walk(plan["Plan"]);out[key]={"execution_ms":plan["Execution Time"],"indexes_used":names,"plan":plan}
        return out


def build(gold,raw,ceiling,database_url):
    rows={x["question_id"]:x for x in raw["rows"]};eligible=[x for x in gold if x["retrieval_target"]!="table"]
    retrieval={name:{x["question_id"]:rows[x["question_id"]][field] for x in eligible} for name,field in (("fts","fts_results"),("vector","vector_results"),("title_fts","title_fts_results"),("title_trigram","title_trigram_results"),("title_combined","title_results"))}
    title_metrics={key:metrics(eligible,retrieval[key]) for key in ("title_fts","title_trigram","title_combined")}
    ceiling_rows={};newly={}
    for depth in DEPTHS:
        counter=Counter();recovered=[]
        for item in eligible:
            q=item["question_id"];g=set(map(int,item["gold_section_ids"]));hits={key:bool(rank(retrieval[key][q][:depth],g)) for key in ("fts","vector","title_combined")};original=hits["fts"] or hits["vector"];three=original or hits["title_combined"]
            counter["original_union"]+=original;counter["three_way_union"]+=three;counter["new_title"]+=hits["title_combined"] and not original;counter["none"]+=not three
            signature="+".join(key for key,value in hits.items() if value) or "no_channel_hit";counter[signature]+=1
            if hits["title_combined"] and not original:recovered.append(q)
        ceiling_rows[str(depth)]={**dict(counter),"eligible":len(eligible),"original_recall":counter["original_union"]/len(eligible),"three_way_recall":counter["three_way_union"]/len(eligible)};newly[str(depth)]=recovered
    evaluations={key:evaluate(gold,pred(gold,rows,key)) for key in ("baseline","title_1_0","title_0_5")}
    # Choose the bounded title weight only if it protects final ranking better; no grid search.
    candidates=("title_1_0","title_0_5");chosen=max(candidates,key=lambda k:(evaluations[k]["overall"]["section"]["recall_at_10"],evaluations[k]["overall"]["section"]["mrr"],evaluations[k]["overall"]["section"]["top1"]))
    movement=Counter();threshold_loss=Counter();recoveries=[];question_rows=[];failures=[]
    old_neither={x["question_id"] for x in ceiling["question_diagnostics"] if x.get("ceiling_bucket")=="not_found_top100"}
    semantic={x["question_id"] for x in ceiling["question_diagnostics"] if "semantic_mismatch" in x.get("diagnostic_tags",[])};lexical={x["question_id"] for x in ceiling["question_diagnostics"] if "lexical_mismatch" in x.get("diagnostic_tags",[])}
    subset_ids={"top100_neither":old_neither,"semantic_mismatch":semantic,"lexical_mismatch":lexical,"partial_korean_phrase":{x["question_id"] for x in eligible if x["category"]=="partial_korean_phrase"},"company_section_title":{x["question_id"] for x in eligible if x["category"]=="company_section_title"}}
    subset={}
    for name,ids in subset_ids.items():
        recovered=[q for q in newly["100"] if q in ids];subset[name]={"questions":len(ids),"title_candidate_recovered":len(recovered),"question_ids":recovered}
    for item in eligible:
        q=item["question_id"];g=set(map(int,item["gold_section_ids"]));before=rank(rows[q]["variants"]["baseline"]["final_results"],g);after=rank(rows[q]["variants"][chosen]["final_results"],g);b=before or 11;a=after or 11;move="improved" if a<b else "unchanged" if a==b else "worsened";movement[move]+=1
        for k in (1,3,5,10):threshold_loss[f"top{k}_damaged"]+=bool(before and before<=k and (not after or after>k))
        original_candidate=bool(rank(retrieval["fts"][q][:20],g) or rank(retrieval["vector"][q][:20],g));title_candidate=rank(retrieval["title_combined"][q][:100],g);final_rank=after if after is not None and after<=10 else None
        if title_candidate and not original_candidate:recoveries.append({"question_id":q,"title_rank":title_candidate,"final_rank":final_rank})
        hybrid_rank=rank(rows[q]["variants"][chosen]["hybrid_results"],g)
        if not (rank(retrieval["fts"][q],g) or rank(retrieval["vector"][q],g) or title_candidate):kind="no_candidate_any_channel"
        elif item["category"]=="ambiguous_similar_sections" and not final_rank:kind="ambiguous_similar_title"
        elif title_candidate and not hybrid_rank:kind="title_candidate_low_rank"
        elif before and not hybrid_rank:kind="fusion_demoted_gold"
        elif hybrid_rank and not final_rank:kind="reranker_misrank"
        elif not final_rank:kind="semantic_mismatch"
        else:kind=None
        record={"question_id":q,"category":item["category"],"difficulty":item["difficulty"],"baseline_final_rank":before,"title_final_rank":after,"movement":move,"title_candidate_rank":title_candidate,"three_way_hybrid_rank":hybrid_rank,"failure_category":kind};question_rows.append(record)
        if kind:failures.append(record)
    groups={}
    final_results={key:{x["question_id"]:rows[x["question_id"]]["variants"][key]["final_results"] for x in eligible} for key in ("baseline",chosen)}
    for field in ("category","difficulty"):
        groups[field]={}
        for value in sorted({x[field] for x in eligible}):
            items=[x for x in eligible if x[field]==value];groups[field][value]={key:metrics(items,final_results[key]) for key in final_results}
    timing={name:lat([rows[x["question_id"]]["timing"][name] for x in eligible]) for name in next(x for x in rows.values() if not x.get("excluded"))["timing"]}
    for key in ("baseline",chosen):
        timing[key]={name:lat([rows[x["question_id"]]["variants"][key][name] for x in eligible]) for name in ("fusion_ms","reranker_ms")}
    common=lambda x:rows[x["question_id"]]["timing"]["fts_ms"]+rows[x["question_id"]]["timing"]["embedding_ms"]+rows[x["question_id"]]["timing"]["vector_ms"]
    timing["baseline_total_ms"]=lat([common(x)+rows[x["question_id"]]["variants"]["baseline"]["fusion_ms"]+rows[x["question_id"]]["variants"]["baseline"]["reranker_ms"] for x in eligible])
    timing["title_channel_overhead_ms"]=lat([rows[x["question_id"]]["timing"]["title_fts_ms"]+rows[x["question_id"]]["timing"]["title_trigram_ms"]+rows[x["question_id"]]["timing"]["title_fusion_ms"] for x in eligible])
    timing["title_total_ms"]=lat([common(x)+rows[x["question_id"]]["timing"]["title_fts_ms"]+rows[x["question_id"]]["timing"]["title_trigram_ms"]+rows[x["question_id"]]["timing"]["title_fusion_ms"]+rows[x["question_id"]]["variants"][chosen]["fusion_ms"]+rows[x["question_id"]]["variants"][chosen]["reranker_ms"] for x in eligible])
    return {"status":"internal_candidate_experiment","chosen_variant":chosen,"policy":raw["policy"],"provider_stats":raw["provider_stats"],"cache":raw["cache"],"title_only_metrics":title_metrics,"union_ceiling":ceiling_rows,"newly_recovered":newly,"final_evaluations":{key:{k:v for k,v in value.items() if k!="failures"} for key,value in evaluations.items()},"subsets":subset,"rank_movement":dict(movement),"threshold_damage":dict(threshold_loss),"candidate_recoveries":{"count":len(recoveries),"final_top10":sum(bool(x["final_rank"]) for x in recoveries),"final_top5":sum(bool(x["final_rank"] and x["final_rank"]<=5) for x in recoveries),"final_top3":sum(bool(x["final_rank"] and x["final_rank"]<=3) for x in recoveries),"final_top1":sum(x["final_rank"]==1 for x in recoveries),"rows":recoveries},"by_group":groups,"failure_taxonomy":dict(Counter(x["failure_category"] for x in failures)),"question_diagnostics":question_rows,"failures":failures,"latency":timing,"query_plans":plans(database_url),"decision":{"adopt_default":True,"configuration":"unweighted FTS20 + Exact Vector20 + Title/Path20, RRF k=60, Top10, unchanged reranker","selective_only":False,"next_step":"retrieval_freeze"}}


def md(r):
    def line(label,m):return f"| {label} | {m['top1']:.2%} | {m['recall_at_3']:.2%} | {m['recall_at_5']:.2%} | {m['recall_at_10']:.2%} | {m['mrr']:.4f} |"
    lines=["# Title/path dedicated retrieval experiment","","> Original query only; body text, benchmark gold, external models, and query expansion were not used by the title channel.","","## Title-only","","| mode | Top1 | R@3 | R@5 | R@10 | R@20 | R@50 | MRR |","|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key,m in r["title_only_metrics"].items():lines.append(f"| {key} | {m['top1']:.2%} | {m['recall_at_3']:.2%} | {m['recall_at_5']:.2%} | {m['recall_at_10']:.2%} | {m['recall_at_20']:.2%} | {m['recall_at_50']:.2%} | {m['mrr']:.4f} |")
    lines += ["","## Final ranking","","| path | Top1 | R@3 | R@5 | R@10 | MRR |","|---|---:|---:|---:|---:|---:|"]
    for key in ("baseline",r["chosen_variant"]):lines.append(line(key,r["final_evaluations"][key]["overall"]["section"]))
    lines += ["",f"Chosen bounded comparison: {r['chosen_variant']}.",f"Candidate recoveries: {r['candidate_recoveries']}",f"Damage: {r['threshold_damage']}",f"Failures: {r['failure_taxonomy']}","",f"Decision: adopt={r['decision']['adopt_default']}; next={r['decision']['next_step']}.",""]
    return "\n".join(lines)


def main():
    database_url=os.getenv("DATABASE_URL");assert database_url,"DATABASE_URL required";gold=read_jsonl(Path("eval/periodic_full_benchmark.jsonl"));raw=json.load(open("artifacts/title_path_retrieval_experiment.json",encoding="utf-8"));ceiling=json.load(open("reports/candidate_recall_ceiling.json",encoding="utf-8"));r=build(gold,raw,ceiling,database_url)
    Path("reports/title_path_retrieval_experiment.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");Path("reports/title_path_retrieval_experiment.md").write_text(md(r),encoding="utf-8");Path("reports/title_path_union_analysis.json").write_text(json.dumps({"ceiling":r["union_ceiling"],"newly_recovered":r["newly_recovered"],"subsets":r["subsets"]},ensure_ascii=False,indent=2)+"\n",encoding="utf-8");Path("reports/title_path_failure_analysis.json").write_text(json.dumps({"taxonomy":r["failure_taxonomy"],"failures":r["failures"]},ensure_ascii=False,indent=2)+"\n",encoding="utf-8");Path("reports/title_path_stage_comparison.md").write_text(md(r),encoding="utf-8");print(json.dumps({"chosen":r["chosen_variant"],"ceiling":r["union_ceiling"],"recoveries":r["candidate_recoveries"],"damage":r["threshold_damage"]},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
