"""Frozen Retrieval V1 natural-language section pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .embeddings import EmbeddingProvider
from .reranker import RerankCache, rerank
from .sections import search_sections
from .title_paths import fuse_named_channels, search_title_paths
from .vector_sections import search_vector_sections


@dataclass(frozen=True)
class RetrievalV1Config:
    fts_depth: int = 20
    vector_depth: int = 20
    title_path_depth: int = 20
    rrf_k: int = 60
    fusion_depth: int = 10
    reranker_enabled: bool = True


DEFAULT_RETRIEVAL_V1 = RetrievalV1Config()


def _context(connection:Any,candidates:list[dict[str,Any]]) -> list[dict[str,Any]]:
    ids=sorted({int(row["section_id"]) for row in candidates})
    texts=dict(connection.execute("SELECT section_id,left(body_text,2000) FROM document_sections WHERE section_id=ANY(%s)",(ids,)).fetchall()) if ids else {}
    return [{**row,"rerank_text":texts.get(int(row["section_id"])) or row.get("body_snippet","")} for row in candidates]


def search_default_sections(connection:Any,query:str,provider:EmbeddingProvider,*,top_k:int=10,company:str|None=None,date_from:date|None=None,date_to:date|None=None,report_type:str|None=None,receipt_no:str|None=None,corpus_namespace:str="periodic-full-v1",config:RetrievalV1Config=DEFAULT_RETRIEVAL_V1,rerank_cache:RerankCache|None=None) -> list[dict[str,Any]]:
    """Return evidence candidates from frozen FTS+Exact Vector+Title/Path V1."""
    if not 1<=top_k<=config.fusion_depth:raise ValueError("top_k must be within the frozen fusion depth")
    filters={"company":company,"date_from":date_from,"date_to":date_to,"report_type":report_type,"receipt_no":receipt_no,"corpus_namespace":corpus_namespace}
    fts=search_sections(connection,query,top_k=config.fts_depth,mode="fts",**filters)
    vector=search_vector_sections(connection,query,provider,top_k=config.vector_depth,**filters)
    title=search_title_paths(connection,query,top_k=config.title_path_depth,mode="combined",**filters)
    fused=fuse_named_channels({"fts":fts,"vector":vector,"title":title},top_k=config.fusion_depth,rrf_k=config.rrf_k)
    ranked=rerank(query,_context(connection,fused),cache=rerank_cache,top_k=config.fusion_depth) if config.reranker_enabled else fused
    output=[]
    for position,row in enumerate(ranked[:top_k],1):
        output.append({**row,"rank":position,"company":row.get("company_name"),"evidence_text":row.get("rerank_text") or row.get("body_snippet",""),"rrf_score":row.get("rrf_score"),"score":row.get("reranker_score",row.get("rrf_score")),"retrieval_config":"retrieval-v1-frozen"})
    return output
