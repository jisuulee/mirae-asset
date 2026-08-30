"""Diagnose candidate recall ceilings without changing retrieval or benchmark gold."""

from __future__ import annotations

import argparse,json,os,re
from collections import Counter,defaultdict
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import read_jsonl
from src.db.load_postgres import connect
from src.retrieval.text_normalization import normalize_search_text

DEPTHS=(10,20,50,100);TOKEN=re.compile(r"[0-9A-Za-z가-힣]{2,}")
def tokens(v):return set(TOKEN.findall(normalize_search_text(str(v or ""))))
def hits(results,gold,depth):return [i for i,x in enumerate(results[:depth],1) if int(x["section_id"]) in gold]
def best_rank(results,gold):return next((i for i,x in enumerate(results,1) if int(x["section_id"]) in gold),None)


def depth_summary(gold,rows,selector=lambda item:True):
    out={}
    for depth in DEPTHS:
        c=Counter()
        for item in gold:
            if item["retrieval_target"]=="table" or not selector(item):continue
            row=rows[item["question_id"]];expected=set(map(int,item["gold_section_ids"]));f=bool(hits(row["searches"]["normal"]["fts"],expected,depth));v=bool(hits(row["searches"]["normal"]["vector"],expected,depth))
            c["eligible"]+=1;c["fts"]+=f;c["vector"]+=v;c["union"]+=f or v;c["both" if f and v else "fts_only" if f else "vector_only" if v else "neither"]+=1
        out[str(depth)]={**dict(c),"fts_recall":c["fts"]/c["eligible"] if c["eligible"] else None,"vector_recall":c["vector"]/c["eligible"] if c["eligible"] else None,"union_recall":c["union"]/c["eligible"] if c["eligible"] else None}
    return out


def load_section_info(connection,ids):
    rows=connection.execute("""SELECT s.section_id,s.receipt_no,s.title_normalized,s.section_path,length(s.body_text),s.source_locator,
      s.metadata->>'source_type',f.file_id,f.relative_path,f.sha256,count(c.vector_chunk_id)
      FROM document_sections s JOIN disclosure_files f USING(file_id) LEFT JOIN section_vector_chunks c USING(section_id)
      WHERE s.section_id=ANY(%s) GROUP BY s.section_id,f.file_id,f.relative_path,f.sha256""",(sorted(ids),)).fetchall()
    names=("section_id","receipt_no","title","section_path","body_length","source_locator","source_type","file_id","file_path","source_sha256","chunk_count")
    return {int(r[0]):dict(zip(names,r)) for r in rows}


def build(gold,raw,reranker_failures,section_info):
    rows={x["question_id"]:x for x in raw["rows"] if x.get("searches")};current_fail={x["question_id"] for x in reranker_failures if x["failure_category"]=="gold_not_in_candidates"}
    overall=depth_summary(gold,rows);by_category={c:depth_summary(gold,rows,lambda x,c=c:x["category"]==c) for c in sorted({x["category"] for x in gold if x["retrieval_target"]!="table"})};by_difficulty={d:depth_summary(gold,rows,lambda x,d=d:x["difficulty"]==d) for d in ("easy","medium","hard")}
    diagnostics=[];ceiling=Counter();tags=Counter();ablation=Counter();split_groups=defaultdict(lambda:Counter())
    for item in gold:
        if item["retrieval_target"]=="table":continue
        row=rows[item["question_id"]];expected=set(map(int,item["gold_section_ids"]));normal=row["searches"]["normal"];fr=best_rank(normal["fts"],expected);vr=best_rank(normal["vector"],expected);union_rank=min(x for x in (fr,vr) if x is not None) if fr is not None or vr is not None else None
        infos=[section_info[x] for x in expected if x in section_info];is_split=any(int(x["chunk_count"])>1 for x in infos);is_long=any(int(x["body_length"] or 0)>8000 for x in infos);group="split" if is_split else "unsplit"
        for depth in DEPTHS:
            split_groups[group][f"eligible_{depth}"]+=1;split_groups[group][f"vector_{depth}"]+=bool(vr and vr<=depth);split_groups[group][f"union_{depth}"]+=bool(union_rank and union_rank<=depth)
        relaxed={};metadata_issue=False
        for profile,value in row["searches"].items():
            if profile=="normal":continue
            fh=best_rank(value["fts"],expected);vh=best_rank(value["vector"],expected);relaxed[profile]={"fts_gold_rank":fh,"vector_gold_rank":vh}
            if union_rank is None and (fh is not None or vh is not None):metadata_issue=True;ablation[profile]+=1
        qtokens=tokens(item["question"]);title_overlap=max((len(qtokens & tokens(f"{x['title']} {x['section_path']}"))/len(qtokens) if qtokens else 0 for x in infos),default=0)
        chunk_hits=[{"rank":i,"vector_chunk_id":x["vector_chunk_id"],"section_id":x["section_id"],"chunk_order":x["chunk_order"]} for i,x in enumerate(normal["vector_chunks"],1) if int(x["section_id"]) in expected]
        diag_tags=[]
        if fr is None or fr>50:diag_tags.append("lexical_mismatch")
        if vr is None or vr>50:diag_tags.append("semantic_mismatch")
        if fr is None and vr is None:diag_tags.append("both_retrievers_miss")
        if metadata_issue:diag_tags.append("metadata_filter_issue")
        if title_overlap>=.5 and (fr is None or fr>20):diag_tags.append("title_path_mismatch")
        if is_split and (vr is None or vr>20):diag_tags.append("chunking_or_parent_issue")
        if is_long:diag_tags.append("long_section")
        if item["retrieval_target"]=="pdf_page":diag_tags.append("pdf_target")
        if item["human_review_status"]=="ambiguous":diag_tags.append("gold_ambiguity")
        gold_receipts=set(item["gold_receipt_nos"]);top5=normal["fts"][:5]+normal["vector"][:5]
        if item.get("year_filter_required") and sum(str(x.get("receipt_no")) not in gold_receipts for x in top5)>=5:diag_tags.append("cross_year_or_report_confusion")
        gold_titles={normalize_search_text(x["title"] or "") for x in infos};similar=sum(any(t and (t in normalize_search_text(x.get("title") or "") or normalize_search_text(x.get("title") or "") in t) for t in gold_titles) for x in top5)
        if similar>=3:diag_tags.append("same_company_similar_section")
        if item["question_id"] in current_fail:
            if union_rank is not None and union_rank<=10:bucket="fusion_demoted_within_top10"
            elif union_rank is not None and union_rank<=20:bucket="recovered_by_top20"
            elif union_rank is not None and union_rank<=50:bucket="recovered_by_top50"
            elif union_rank is not None and union_rank<=100:bucket="recovered_by_top100"
            else:bucket="not_found_top100"
            ceiling[bucket]+=1
            for tag in diag_tags:tags[tag]+=1
            diagnostics.append({"question_id":item["question_id"],"question":item["question"],"category":item["category"],"difficulty":item["difficulty"],"review_status":item["human_review_status"],"retrieval_target":item["retrieval_target"],"filters":{k:item.get(k) for k in ("company","date_from","date_to","report_type_filter","receipt_no_filter")},"fts_gold_rank":fr,"vector_gold_rank":vr,"all_fts_gold_hits":hits(normal["fts"],expected,100),"all_vector_gold_hits":hits(normal["vector"],expected,100),"vector_gold_chunk_hits":chunk_hits,"ceiling_bucket":bucket,"diagnostic_tags":diag_tags,"title_path_query_overlap":title_overlap,"gold_sections":infos,"best_fts_candidate":normal["fts"][0] if normal["fts"] else None,"best_vector_candidate":normal["vector"][0] if normal["vector"] else None,"filter_ablation":relaxed})
    split_report={}
    for group,c in split_groups.items():
        split_report[group]={str(d):{"questions":c[f"eligible_{d}"],"vector_recall":c[f"vector_{d}"]/c[f"eligible_{d}"],"union_recall":c[f"union_{d}"]/c[f"eligible_{d}"]} for d in DEPTHS}
    return {"status":"diagnostic_only_candidate_benchmark","depth_ceiling":overall,"failure_set_size":len(current_fail),"failure_ceiling_buckets":dict(ceiling),"failure_diagnostic_tags":dict(tags),"filter_ablation_recoveries":dict(ablation),"split_unsplit":split_report,"by_category":by_category,"by_difficulty":by_difficulty,"question_diagnostics":diagnostics}


def md(r):
    lines=["# Candidate recall ceiling","","> Diagnostic only. Full 120 remains internal/candidate; gold and retrieval algorithms were unchanged.","","## Depth ceiling","","| depth | FTS | Vector | Union | FTS only | Vector only | Both | Neither |","|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for d,v in r["depth_ceiling"].items():lines.append(f"| {d} | {v['fts']}/{v['eligible']} ({v['fts_recall']:.2%}) | {v['vector']}/{v['eligible']} ({v['vector_recall']:.2%}) | {v['union']}/{v['eligible']} ({v['union_recall']:.2%}) | {v.get('fts_only',0)} | {v.get('vector_only',0)} | {v.get('both',0)} | {v.get('neither',0)} |")
    lines += ["","## Current 61 failures","",*(f"- {k}: {v}" for k,v in r["failure_ceiling_buckets"].items()),"","Filter-ablation recoveries: "+json.dumps(r["filter_ablation_recoveries"],ensure_ascii=False),"","Taxonomy labels are automated review aids and never modify gold.",""]
    return "\n".join(lines)


def next_steps(r):
    d=r["depth_ceiling"];return """# Candidate-generation next steps

| priority | option | expected impact | complexity | latency | rule risk |
|---:|---|---|---|---|---|
| 1 | Increase diagnostic candidate depth to 50 with bounded downstream pruning | High: union rises from %d/113 at Top-20 to %d/113 at Top-50 | Low | Medium | Low |
| 2 | Parent-section aggregation diagnosis for split/long sections | High for the structurally weak split subset | Medium | Medium | Low |
| 3 | Query expansion/rewriting as a separate experiment | Potentially high for Top-100 neither misses | Medium/High | Medium | Medium; provider/rule approval required |
| 4 | Dedicated title/path retrieval, evaluated independently | Targeted; only a small failure subset has strong title/path overlap | Medium | Low | Low |
| 5 | PDF/table-specific retrieval | Low for these 61 section failures; table-only remains a separate track | High | Medium | Low |
| 6 | Metadata routing changes | Low: ablation recovered no misses | Medium | Low | Low |

Recommendation: test candidate depth 50 first because it is the lowest-risk change with a directly measured ceiling gain. Keep the current algorithms and evaluate latency plus reranker pruning. Next, diagnose parent-section aggregation on split/long sections. Top-100 still leaves %d/113 misses, so depth alone cannot solve the corpus.
"""%(d["20"]["union"],d["50"]["union"],d["100"]["neither"])


def main():
    p=argparse.ArgumentParser();p.add_argument("--input",type=Path,default=Path("artifacts/candidate_recall_diagnosis.json"));p.add_argument("--gold",type=Path,default=Path("eval/periodic_full_benchmark.jsonl"));p.add_argument("--reranker-failures",type=Path,default=Path("reports/periodic_full_reranker_failures.json"));p.add_argument("--database-url",default=os.getenv("DATABASE_URL"));a=p.parse_args()
    if not a.database_url:p.error("DATABASE_URL required")
    gold=read_jsonl(a.gold);ids={int(x) for row in gold for x in row["gold_section_ids"]}
    with connect(a.database_url) as c:info=load_section_info(c,ids)
    r=build(gold,json.loads(a.input.read_text(encoding="utf-8")),json.loads(a.reranker_failures.read_text(encoding="utf-8")),info)
    Path("reports/candidate_recall_ceiling.json").write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");Path("reports/candidate_recall_ceiling.md").write_text(md(r),encoding="utf-8");Path("reports/candidate_generation_failures.json").write_text(json.dumps(r["question_diagnostics"],ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");Path("reports/candidate_generation_next_steps.md").write_text(next_steps(r),encoding="utf-8");print(json.dumps({"ceiling":r["depth_ceiling"],"failure_buckets":r["failure_ceiling_buckets"]},indent=2))
if __name__=="__main__":main()
