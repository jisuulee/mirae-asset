from eval.evaluate_full_retrieval import deduplicate_receipts
from eval.run_full_hybrid_benchmark import filters
from src.retrieval.hybrid_sections import reciprocal_rank_fusion


def row(section_id, score=1.0, receipt="r", provenance=None):
    return {"section_id":section_id,"score":score,"receipt_no":receipt,
            "source_locator":f"xpath:{section_id}","provenance":provenance or {"stable":True}}


def test_rrf_is_deterministic_and_deduplicates_sections():
    fts=[row(1),row(1,.9),row(2)];vector=[row(2),row(3)]
    first=reciprocal_rank_fusion(fts,vector,top_k=3,rrf_k=60)
    second=reciprocal_rank_fusion(fts,vector,top_k=3,rrf_k=60)
    assert [x["section_id"] for x in first]==[2,1,3]==[x["section_id"] for x in second]
    assert first[0]["fts_rank"]==3 and first[0]["vector_rank"]==1


def test_rrf_handles_one_or_both_retrievers_and_empty_results():
    assert [x["section_id"] for x in reciprocal_rank_fusion([row(1)],[],top_k=10)]==[1]
    assert [x["section_id"] for x in reciprocal_rank_fusion([],[row(2)],top_k=10)]==[2]
    both=reciprocal_rank_fusion([row(1)],[row(1)],top_k=10)
    assert both[0]["fts_rank"]==both[0]["vector_rank"]==1
    assert reciprocal_rank_fusion([],[],top_k=10)==[]


def test_rrf_preserves_provenance_and_raw_scores():
    result=reciprocal_rank_fusion([row(7,.7,provenance={"file_id":3})],[row(7,.8)],top_k=1)[0]
    assert result["provenance"]=={"file_id":3}
    assert result["fts_score"]==.7 and result["vector_score"]==.8


def test_metadata_filter_contract_and_receipt_dedup():
    item={"company_filter_required":True,"company":" ACME ","date_from":"2025-01-01","date_to":"2025-12-31","report_type_filter":"annual","receipt_no_filter":"R1","loader_namespace":"periodic-full-v1"}
    value=filters(item)
    assert str(value["date_from"])=="2025-01-01" and value["receipt_no"]=="R1"
    assert value["corpus_namespace"]=="periodic-full-v1"
    assert deduplicate_receipts([row(1,receipt="R1"),row(2,receipt="R1"),row(3,receipt="R2")])==["R1","R2"]


def test_multi_gold_candidate_can_recover_either_section():
    fused=reciprocal_rank_fusion([row(4)],[row(9)],top_k=2)
    assert {4,9}.intersection(x["section_id"] for x in fused)=={4,9}
