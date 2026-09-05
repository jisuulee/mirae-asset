from pathlib import Path

from eval.evaluate_full_retrieval import deduplicate_receipts
from src.retrieval.reranker import RerankCache,cache_key,rerank


def candidate(section_id,rank,*,fts=None,vector=None,score="malformed",receipt="R1"):
    return {"section_id":section_id,"hybrid_rank":rank,"fts_rank":fts,"vector_rank":vector,
            "score":score,"title":"사업 내용","section_path":"II/사업","rerank_text":"회사의 주요 사업 내용",
            "receipt_no":receipt,"source_locator":f"xpath:{section_id}","provenance":{"file_id":1}}


def test_deterministic_ties_use_hybrid_rank_then_section_id():
    rows=[candidate(2,2,fts=1),candidate(1,1,fts=1)]
    assert [x["section_id"] for x in rerank("무관 질문",rows)]==[1,2]
    assert [x["section_id"] for x in rerank("무관 질문",rows)]==[1,2]


def test_missing_duplicate_empty_and_malformed_raw_score():
    assert rerank("질문",[])==[]
    rows=rerank("사업 내용",[candidate(1,1,fts=1),candidate(1,2,vector=1)])
    assert len(rows)==1 and rows[0]["reranker_score"]>=0


def test_cache_hit_and_key_versioning(tmp_path: Path):
    cache=RerankCache(tmp_path/"cache.sqlite");row=candidate(1,1,fts=1)
    first=rerank("사업",[row],cache=cache);second=rerank("사업",[row],cache=cache)
    assert first[0]["reranker_score"]==second[0]["reranker_score"]
    assert cache.misses==1 and cache.hits==1 and len(cache_key("사업",row))==64
    cache.close()


def test_cache_key_includes_rank_features():
    first=candidate(1,1,fts=1);second={**first,"fts_rank":2}
    assert cache_key("사업",first)!=cache_key("사업",second)


def test_provenance_preserved_and_gold_fields_ignored():
    row=candidate(1,1,vector=1);base=rerank("사업",[row])[0]
    leaked={**row,"gold_section_ids":[999],"question_id":"secret","difficulty":"hard"}
    result=rerank("사업",[leaked])[0]
    assert result["reranker_score"]==base["reranker_score"]
    assert result["provenance"]=={"file_id":1}


def test_receipt_dedup_after_reranking():
    rows=rerank("사업",[candidate(1,1,fts=1),candidate(2,2,vector=1,receipt="R1"),candidate(3,3,vector=2,receipt="R2")])
    assert deduplicate_receipts(rows)==["R1","R2"]


def test_candidate_set_is_preserved():
    rows=[candidate(i,i,fts=i) for i in range(1,11)]
    assert {x["section_id"] for x in rerank("사업",rows)}==set(range(1,11))
