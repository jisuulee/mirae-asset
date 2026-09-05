"""Deterministic parent-section aggregation over ranked raw Vector chunks."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

METHOD_LIMITS = {"best": 1, "support2": 2, "support3": 3}
MAX_SUPPORT_BONUS = 0.02


def aggregate_parent_chunks(
    chunks: Iterable[dict[str, Any]], *, method: str
) -> list[dict[str, Any]]:
    """Return one candidate per section; additional-chunk support is capped."""
    if method not in METHOD_LIMITS:
        raise ValueError(f"unsupported parent aggregation method: {method}")
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        grouped[int(chunk["section_id"])].append(dict(chunk))
    parents=[];limit=METHOD_LIMITS[method]
    for section_id,values in grouped.items():
        values.sort(key=lambda x:(-float(x["score"]),int(x.get("raw_chunk_rank") or 10**9),int(x["vector_chunk_id"])))
        contributing=values[:limit];best=values[0];support=[max(0.0,min(1.0,float(x["score"]))) for x in contributing[1:]]
        bonus=min(MAX_SUPPORT_BONUS,MAX_SUPPORT_BONUS*(sum(support)/len(support))) if support else 0.0
        parents.append({**best,"section_id":section_id,"score":float(best["score"])+bonus,
                        "aggregated_score":float(best["score"])+bonus,"best_chunk_score":float(best["score"]),
                        "support_bonus":bonus,"aggregation_method":method,
                        "contributing_chunk_ids":[int(x["vector_chunk_id"]) for x in contributing],
                        "contributing_chunk_ranks":[int(x["raw_chunk_rank"]) for x in contributing],
                        "contributing_chunk_scores":[float(x["score"]) for x in contributing],
                        "contributing_chunk_count":len(contributing),"retrieval_method":f"vector_parent_{method}"})
    parents.sort(key=lambda x:(-x["aggregated_score"],int(x["contributing_chunk_ranks"][0]),int(x["section_id"])))
    for rank,parent in enumerate(parents,1):parent["parent_rank"]=rank
    return parents
