"""Leakage-free deterministic relevance reranking over an existing candidate set."""

from __future__ import annotations

import hashlib, json, re, sqlite3
from pathlib import Path
from typing import Any

from .text_normalization import normalize_search_text

MODEL = "deterministic-feature-reranker-v1"
SCORING_VERSION = "rank-lexical-v1"
TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def _tokens(value: str) -> set[str]:
    return set(TOKEN.findall(normalize_search_text(value)))


def _coverage(query: set[str], text: str) -> float:
    return len(query & _tokens(text)) / len(query) if query else 0.0


def candidate_content_hash(candidate: dict[str, Any]) -> str:
    source="\n".join(str(candidate.get(key) or "") for key in ("title","section_path","rerank_text","source_locator"))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def cache_key(query: str, candidate: dict[str, Any]) -> str:
    payload="\0".join((normalize_search_text(query),candidate_content_hash(candidate),MODEL,SCORING_VERSION,
                        str(candidate.get("fts_rank") or ""),str(candidate.get("vector_rank") or "")))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RerankCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True,exist_ok=True);self.connection=sqlite3.connect(path)
        self.connection.execute("CREATE TABLE IF NOT EXISTS scores(cache_key TEXT PRIMARY KEY,score REAL NOT NULL,features_json TEXT NOT NULL)")
        self.hits=0;self.misses=0

    def get(self,key: str):
        row=self.connection.execute("SELECT score,features_json FROM scores WHERE cache_key=?",(key,)).fetchone()
        if row:self.hits+=1;return float(row[0]),json.loads(row[1])
        self.misses+=1;return None

    def put(self,key: str,score: float,features: dict[str,float]):
        self.connection.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?)",(key,score,json.dumps(features,sort_keys=True)))

    def close(self):self.connection.commit();self.connection.close()


def relevance_features(query: str, candidate: dict[str, Any]) -> dict[str,float]:
    query_tokens=_tokens(query);fts_rank=candidate.get("fts_rank");vector_rank=candidate.get("vector_rank")
    return {"fts_rank_signal":1/float(fts_rank) if fts_rank else 0.0,
            "vector_rank_signal":1/float(vector_rank) if vector_rank else 0.0,
            "text_coverage":_coverage(query_tokens,str(candidate.get("rerank_text") or "")),
            "title_path_coverage":_coverage(query_tokens," ".join(str(candidate.get(k) or "") for k in ("title","section_path"))),
            "consensus":1.0 if fts_rank and vector_rank else 0.0}


def relevance_score(features: dict[str,float]) -> float:
    return (.30*features["fts_rank_signal"]+.30*features["vector_rank_signal"]+
            .20*features["text_coverage"]+.15*features["title_path_coverage"]+.05*features["consensus"])


def rerank(query: str,candidates: list[dict[str,Any]],*,cache: RerankCache|None=None,top_k: int=10):
    unique={}
    for candidate in candidates:
        unique.setdefault(int(candidate["section_id"]),candidate)
    scored=[]
    for candidate in unique.values():
        key=cache_key(query,candidate);cached=cache.get(key) if cache else None
        if cached is None:
            features=relevance_features(query,candidate);score=relevance_score(features)
            if cache:cache.put(key,score,features)
        else:score,features=cached
        scored.append({**candidate,"original_hybrid_rank":candidate.get("hybrid_rank"),
                       "reranker_score":score,"reranker_features":features,
                       "reranker_model":MODEL,"reranker_version":SCORING_VERSION})
    scored.sort(key=lambda x:(-x["reranker_score"],x["original_hybrid_rank"] or 10**9,int(x["section_id"])))
    for rank,row in enumerate(scored[:top_k],1):row["final_reranked_rank"]=rank
    return scored[:top_k]
