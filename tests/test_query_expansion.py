from pathlib import Path

from eval.run_query_expansion_diagnostic import QueryVectorCache
from src.retrieval.embeddings import EmbeddingResult, EmbeddingSpec
from src.retrieval.query_expansion import CorpusQueryExpander, fuse_multi_query_results, track_rank_change


def row(section,score=1.0,**extra):
    return {"section_id":section,"score":score,"receipt_no":f"R{section}","provenance":{"file_id":section},**extra}


def test_deterministic_bounded_expansion_and_no_metadata_input():
    expander=CorpusQueryExpander(["연구 개발 현황","사업의 내용","매출 및 수주상황","연구개발"],max_terms=2)
    first=expander.expand("이 회사의 연구개발현황을 찾아줘","combined")
    second=expander.expand("이 회사의 연구개발현황을 찾아줘","combined")
    assert first==second and len(first)<=2
    assert all(hasattr(value,"query") and hasattr(value,"source") for value in first)


def test_normalization_and_spacing_variation():
    expander=CorpusQueryExpander(["연구개발","현황"])
    values=expander.expand("  연구개발\u3000현황  ","spacing")
    assert values[0].query=="연구개발현황"


def test_corpus_word_decomposition():
    expander=CorpusQueryExpander(["연구개발","현황"])
    values=expander.expand("연구개발현황","spacing")
    assert any(value.query=="연구개발 현황" for value in values)


def test_empty_expansion_and_unknown_variant():
    expander=CorpusQueryExpander([])
    assert expander.expand("질문","title_path")==[]
    try:expander.expand("질문","gold_answer")
    except ValueError:pass
    else:raise AssertionError("unknown variants must fail")


def test_multi_query_fusion_deduplicates_preserves_provenance_and_is_deterministic():
    first=[row(1),row(2)];second=[row(2),row(3)]
    fused=fuse_multi_query_results([first,second],top_k=3)
    assert [x["section_id"] for x in fused]==[2,1,3]
    assert fused[0]["provenance"]=={"file_id":2}
    assert len(fused[0]["query_hits"])==2


def test_multi_gold_and_missing_results_are_compatible():
    fused=fuse_multi_query_results([[],[row(2)]],top_k=10)
    assert {x["section_id"] for x in fused}&{1,2}=={2}


def test_query_drift_tracking():
    assert track_rank_change(10,3)=="improved"
    assert track_rank_change(3,3)=="unchanged"
    assert track_rank_change(3,None)=="worsened"


def test_query_embedding_cache_reuses_model_version_and_query(tmp_path: Path):
    spec=EmbeddingSpec("provider","model","v1",2);cache=QueryVectorCache(tmp_path/"query.sqlite")
    assert cache.get("연구 개발",spec) is None
    cache.put("연구 개발",spec,EmbeddingResult((.1,.2),3))
    assert cache.get("  연구\u3000개발 ",spec)==EmbeddingResult((.1,.2),3)
    assert cache.misses==1 and cache.hits==1
    assert cache.get("연구 개발",EmbeddingSpec("provider","model","v2",2)) is None
    cache.close()
