"""Evaluate deterministic Question Router V1 without retrieval benchmark leakage."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.retrieval.question_router import route_question

DATASET = ROOT / "eval" / "question_router_v1.jsonl"
COMPANIES=("LG에너지솔루션","포스코인터내셔널","두산에너빌리티","포스코퓨처엠","현대자동차","현대모비스","SK하이닉스","삼성전자","삼성물산","현대건설","한화오션","LG화학","네이버","카카오","삼성SDI","셀트리온")
ROUTES=("sql","rag","correction")


def read_rows(path=DATASET):return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_rows(rows):
    diagnostics=[];route_correct=clarify_correct=metadata_total=metadata_correct=0;counts={route:Counter() for route in ROUTES};failures=Counter()
    for item in rows:
        decision=route_question(item["question"],known_companies=COMPANIES);expected_routes=set(item.get("expected_routes") or ([item["expected_route"]] if item["expected_route"]!="composite" else []));actual_routes=set(decision.routes)
        route_hit=decision.route==item["expected_route"] and (not item.get("expected_routes") or actual_routes==expected_routes);route_correct+=route_hit
        clarification_hit=decision.needs_clarification==item["expected_clarification"];clarify_correct+=clarification_hit
        for route in ROUTES:
            expected=route in expected_routes;actual=route in actual_routes
            counts[route]["tp" if expected and actual else "fp" if actual else "fn" if expected else "tn"]+=1
        metadata_errors=[]
        for field in ("company","year","report_type","receipt_no"):
            key=f"expected_{field}"
            if key in item:
                metadata_total+=1
                if getattr(decision,field)==item[key]:metadata_correct+=1
                else:metadata_errors.append({"field":field,"expected":item[key],"actual":getattr(decision,field)})
        failure=None
        if not route_hit:
            expected=item["expected_route"];actual=decision.route
            if expected=="sql" and actual=="rag":failure="sql_as_rag"
            elif expected=="rag" and actual=="sql":failure="rag_as_sql"
            elif expected in {"correction","composite"} and "correction" not in actual_routes:failure="correction_missed"
            elif "correction" in actual_routes and "correction" not in expected_routes:failure="correction_false_positive"
            elif expected=="composite":failure="multi_intent_failure"
            else:failure="route_mismatch"
        elif not clarification_hit:failure="ambiguous_should_clarify"
        elif metadata_errors:failure="metadata_extraction_error"
        if failure:failures[failure]+=1
        diagnostics.append({"question_id":item["question_id"],"question":item["question"],"expected_route":item["expected_route"],"actual":decision.to_dict(),"route_correct":route_hit,"clarification_correct":clarification_hit,"metadata_errors":metadata_errors,"failure_category":failure})
    per_route={}
    for route,value in counts.items():
        tp,fp,fn=value["tp"],value["fp"],value["fn"]
        per_route[route]={"support":tp+fn,"precision":tp/(tp+fp) if tp+fp else 0.0,"recall":tp/(tp+fn) if tp+fn else 0.0,"tp":tp,"fp":fp,"fn":fn}
    n=len(rows)
    return {"router_version":"question-router-v1","dataset_size":n,"route_distribution":dict(Counter(row["expected_route"] for row in rows)),"overall_route_accuracy":route_correct/n,"clarification_accuracy":clarify_correct/n,"metadata_extraction_accuracy":metadata_correct/metadata_total if metadata_total else 1.0,"metadata_assertions":metadata_total,"per_route":per_route,"failure_taxonomy":dict(failures),"failures":[x for x in diagnostics if x["failure_category"]],"diagnostics":diagnostics}


def markdown(result):
    lines=["# Question Router V1 summary","",f"Evaluation set: {result['dataset_size']} hand-authored policy regression questions, maintained separately from the Retrieval benchmark. This is not an external or independent validation set.","",f"- Overall route accuracy: {result['overall_route_accuracy']:.2%}",f"- Clarification accuracy: {result['clarification_accuracy']:.2%}",f"- Metadata extraction accuracy: {result['metadata_extraction_accuracy']:.2%} ({result['metadata_assertions']} assertions)","","| Route | Support | Precision | Recall |","|---|---:|---:|---:|"]
    for route,value in result["per_route"].items():lines.append(f"| {route} | {value['support']} | {value['precision']:.2%} | {value['recall']:.2%} |")
    lines += ["",f"Route distribution: {result['route_distribution']}",f"Failure taxonomy: {result['failure_taxonomy'] or '{}'}",""]
    return "\n".join(lines)


def main():
    result=evaluate_rows(read_rows());Path("reports/question_router_v1_summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");Path("reports/question_router_v1_summary.md").write_text(markdown(result),encoding="utf-8");print(json.dumps({k:v for k,v in result.items() if k not in ("diagnostics","failures")},ensure_ascii=False,indent=2));
    if result["failures"]:print(json.dumps(result["failures"],ensure_ascii=False,indent=2))
if __name__=="__main__":main()
