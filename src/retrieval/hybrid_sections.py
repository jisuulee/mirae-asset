"""Score-scale-independent section fusion for FTS and Exact Vector results."""

from __future__ import annotations

from typing import Any, Iterable


def reciprocal_rank_fusion(
    fts_results: Iterable[dict[str, Any]],
    vector_results: Iterable[dict[str, Any]],
    *,
    top_k: int = 10,
    rrf_k: int = 60,
    fts_weight: float = 1.0,
    vector_weight: float = 1.0,
) -> list[dict[str, Any]]:
    if not 1 <= top_k <= 100 or rrf_k < 1:
        raise ValueError("invalid top_k or rrf_k")
    if fts_weight < 0 or vector_weight < 0 or not (fts_weight or vector_weight):
        raise ValueError("fusion weights must be non-negative and not both zero")
    merged: dict[int, dict[str, Any]] = {}
    for source, results, weight in (("fts", fts_results, fts_weight),
                                    ("vector", vector_results, vector_weight)):
        seen: set[int] = set()
        for rank, result in enumerate(results, 1):
            section_id = int(result["section_id"])
            if section_id in seen:
                continue
            seen.add(section_id)
            row = merged.setdefault(section_id, {
                **result, "section_id": section_id, "fts_rank": None,
                "fts_score": None, "vector_rank": None, "vector_score": None,
                "rrf_score": 0.0, "retrieval_method": "rrf_hybrid",
            })
            row[f"{source}_rank"] = rank
            row[f"{source}_score"] = result.get("score")
            row["rrf_score"] += weight / (rrf_k + rank)
            # Prefer the FTS representation when both carry equivalent provenance.
            if source == "fts":
                for key, value in result.items():
                    if key not in {"score", "retrieval_method"}:
                        row[key] = value
    ranked = sorted(merged.values(), key=lambda row: (
        -row["rrf_score"], min(row["fts_rank"] or 10**9, row["vector_rank"] or 10**9),
        row["section_id"],
    ))
    for rank, row in enumerate(ranked[:top_k], 1):
        row["score"] = row["rrf_score"]
        row["hybrid_rank"] = rank
    return ranked[:top_k]
