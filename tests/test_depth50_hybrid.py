import json
from pathlib import Path

from eval.evaluate_depth50_hybrid import _hit
from src.retrieval.hybrid_sections import reciprocal_rank_fusion
from src.retrieval.reranker import rerank


def row(section_id,rank=1):
    return {"section_id":section_id,"score":1/rank,"receipt_no":"R","title":"사업",
            "section_path":"II","body_snippet":"사업 내용","source_locator":f"x:{section_id}",
            "provenance":{"file_id":1},"hybrid_rank":rank,"fts_rank":rank,"vector_rank":None,
            "rerank_text":"사업 내용"}


def test_top50_union_dedup_and_rrf_pruning():
    fts=[row(i,i) for i in range(1,51)];vector=[row(i,i) for i in range(26,76)]
    fused=reciprocal_rank_fusion(fts,vector,top_k=100,rrf_k=60)
    assert len(fused)==75 and len({x["section_id"] for x in fused})==75
    assert [len(fused[:d]) for d in (10,20,30)]==[10,20,30]


def test_reranker_depths_preserve_candidates_and_provenance():
    candidates=[row(i,i) for i in range(1,31)]
    for depth in (10,20,30):
        result=rerank("사업",candidates[:depth],top_k=depth)
        assert len(result)==depth
        assert {x["section_id"] for x in result}==set(range(1,depth+1))
        assert all(x["provenance"]=={"file_id":1} for x in result)


def test_multi_gold_and_missing_candidate():
    assert _hit([row(9),row(2)],{1,2},10)
    assert not _hit([], {1,2}, 10)


def test_live_artifact_cache_reuse_and_no_gold_leakage():
    raw=json.loads(Path("artifacts/depth50_hybrid_experiment.json").read_text(encoding="utf-8"))
    assert raw["policy"]["gold_used_as_input"] is False
    assert raw["policy"]["fts_depth"]==raw["policy"]["vector_depth"]==50
    assert raw["cache"]["hits"]>0
    sample=next(x for x in raw["rows"] if x.get("variants"))
    assert set(sample["variants"])=={"10","20","30"}
    assert sample["variants"]["20"]["cache_hits"]>=10


def test_report_tracks_all_pruning_depths():
    report=json.loads(Path("reports/depth50_hybrid_experiment.json").read_text(encoding="utf-8"))
    assert report["stage_gold_counts"]["union50_gold"]==80
    assert [report["stage_gold_counts"][f"rrf{x}_gold"] for x in (10,20,30)]==[48,62,71]
