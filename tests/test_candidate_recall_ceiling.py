import json
from pathlib import Path

from eval.evaluate_candidate_recall_ceiling import depth_summary,hits
from eval.run_candidate_recall_diagnosis import profile_filters,profiles


def item(qid="q1",gold=(1,2)):
    return {"question_id":qid,"retrieval_target":"section","gold_section_ids":list(gold),
            "category":"concept","difficulty":"hard","company_filter_required":False,
            "year_filter_required":False,"loader_namespace":"periodic-full-v1"}


def result(section_id):return {"section_id":section_id,"receipt_no":"R","source_locator":f"x:{section_id}","provenance":{"file_id":1}}


def test_depth_multi_gold_union_and_missing_results():
    gold=[item()];rows={"q1":{"searches":{"normal":{"fts":[result(9),result(2)],"vector":[]}}}}
    summary=depth_summary(gold,rows)
    assert summary["10"]["fts"]==summary["10"]["union"]==1
    assert summary["10"]["vector"]==0 and summary["10"]["fts_only"]==1
    assert hits([], {1,2}, 10)==[]


def test_filter_ablation_contract():
    value=item();value.update(company_filter_required=True,company="ACME",year_filter_required=True,
                              date_from="2025-01-01",date_to="2025-12-31",report_type_filter="annual",receipt_no_filter="R1")
    assert profiles(value)==["normal","company_only","receipt_only","year_report_only","namespace_only"]
    assert profile_filters(value,"company_only")["date_from"] is None
    assert profile_filters(value,"receipt_only")["receipt_no"]=="R1"
    assert profile_filters(value,"namespace_only")["company"] is None


def test_report_has_split_groups_and_stable_provenance():
    report=json.loads(Path("reports/candidate_recall_ceiling.json").read_text(encoding="utf-8"))
    assert set(report["split_unsplit"])=={"split","unsplit"}
    assert report["failure_set_size"]==61
    diagnostic=report["question_diagnostics"][0]
    assert diagnostic["gold_sections"]
    assert {"file_id","file_path","source_sha256","source_locator"} <= diagnostic["gold_sections"][0].keys()


def test_union_is_monotonic_and_neither_complements_union():
    report=json.loads(Path("reports/candidate_recall_ceiling.json").read_text(encoding="utf-8"))
    values=[report["depth_ceiling"][str(k)]["union"] for k in (10,20,50,100)]
    assert values==sorted(values)
    assert report["depth_ceiling"]["100"]["union"]+report["depth_ceiling"]["100"]["neither"]==113
