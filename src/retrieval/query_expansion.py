"""Leakage-free deterministic query expansion grounded in corpus title/path vocabulary."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from .text_normalization import normalize_search_text

TOKEN = re.compile(r"[0-9a-zA-Z가-힣]{2,}")


def _grams(value: str, size: int = 2) -> set[str]:
    compact = normalize_search_text(value, remove_spaces=True)
    return {compact[i:i + size] for i in range(max(0, len(compact) - size + 1))}


@dataclass(frozen=True)
class Expansion:
    query: str
    source: str


class CorpusQueryExpander:
    """Build expansions without question IDs, categories, gold, or answer text."""

    def __init__(self, phrases: Iterable[str], *, max_terms: int = 5):
        if max_terms < 1:
            raise ValueError("max_terms must be positive")
        counts = Counter(normalize_search_text(value) for value in phrases if normalize_search_text(value))
        self.phrases = tuple(sorted(counts, key=lambda value: (-counts[value], value)))
        self.max_terms = max_terms
        vocabulary = set()
        for phrase in self.phrases:
            vocabulary.update(TOKEN.findall(phrase))
        self.words = frozenset(value for value in vocabulary if 2 <= len(value) <= 20)

    def _decompose(self, token: str) -> str | None:
        if len(token) < 4:
            return None
        best: list[str] | None = None
        for cut in range(2, len(token) - 1):
            left, right = token[:cut], token[cut:]
            if left in self.words and right in self.words:
                candidate = [left, right]
                if best is None or min(map(len, candidate)) > min(map(len, best)):
                    best = candidate
        return " ".join(best) if best else None

    def spacing(self, query: str) -> list[Expansion]:
        normalized = normalize_search_text(query)
        values: list[Expansion] = []
        compact = normalize_search_text(query, remove_spaces=True)
        if compact and compact != normalized:
            values.append(Expansion(compact, "no_space"))
        for token in TOKEN.findall(normalized):
            decomposed = self._decompose(token)
            if decomposed and decomposed != token:
                values.append(Expansion(decomposed, "corpus_word_decomposition"))
        return _unique(values, self.max_terms)

    def title_path(self, query: str) -> list[Expansion]:
        normalized = normalize_search_text(query)
        query_grams = _grams(normalized)
        query_tokens = set(TOKEN.findall(normalized))
        scored = []
        for phrase in self.phrases:
            if phrase == normalized or len(phrase) > 80:
                continue
            grams = _grams(phrase)
            if not grams or not query_grams:
                continue
            overlap = len(query_grams & grams) / len(query_grams | grams)
            token_overlap = len(query_tokens & set(TOKEN.findall(phrase))) / max(1, len(query_tokens))
            score = .7 * overlap + .3 * token_overlap
            if score >= .18:
                scored.append((score, phrase))
        scored.sort(key=lambda value: (-value[0], len(value[1]), value[1]))
        return [Expansion(value, "corpus_title_path_ngram") for _, value in scored[:self.max_terms]]

    def expand(self, query: str, variant: str) -> list[Expansion]:
        if variant == "original":
            return []
        if variant == "spacing":
            return self.spacing(query)
        if variant == "title_path":
            return self.title_path(query)
        if variant == "combined":
            return _unique([*self.spacing(query), *self.title_path(query)], self.max_terms)
        raise ValueError(f"unsupported expansion variant: {variant}")


def _unique(values: Iterable[Expansion], limit: int) -> list[Expansion]:
    output=[];seen=set()
    for value in values:
        normalized=normalize_search_text(value.query)
        if normalized and normalized not in seen:
            output.append(Expansion(normalized,value.source));seen.add(normalized)
        if len(output)>=limit:
            break
    return output


def fuse_multi_query_results(result_sets: Iterable[Iterable[dict[str, Any]]], *, top_k: int = 100, rrf_k: int = 60) -> list[dict[str, Any]]:
    if not 1 <= top_k <= 100 or rrf_k < 1:
        raise ValueError("invalid fusion parameters")
    merged: dict[int, dict[str, Any]] = {}
    for query_index, results in enumerate(result_sets):
        seen=set()
        for rank,result in enumerate(results,1):
            section_id=int(result["section_id"])
            if section_id in seen:
                continue
            seen.add(section_id)
            row=merged.setdefault(section_id,{**result,"section_id":section_id,"multi_query_score":0.0,"query_hits":[]})
            row["multi_query_score"] += 1.0/(rrf_k+rank)
            row["query_hits"].append({"query_index":query_index,"rank":rank,"score":result.get("score")})
    ranked=sorted(merged.values(),key=lambda row:(-row["multi_query_score"],min(x["rank"] for x in row["query_hits"]),row["section_id"]))
    for rank,row in enumerate(ranked[:top_k],1):
        row["score"]=row["multi_query_score"];row["multi_query_rank"]=rank
    return ranked[:top_k]


def track_rank_change(original_rank: int | None, expanded_rank: int | None, *, missing_rank: int = 101) -> str:
    """Classify drift without interpreting missing predictions as gold evidence."""
    before=original_rank or missing_rank;after=expanded_rank or missing_rank
    return "improved" if after < before else "unchanged" if after == before else "worsened"
