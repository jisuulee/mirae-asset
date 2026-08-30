# Retrieval V1 freeze summary

Frozen on 2026-08-31. The 120-question set remains internal/candidate; the reviewed 36 is stronger regression evidence.

## Default

`FTS20 + Exact Vector20 + Title/Path20 → unweighted RRF k=60 → Top10 → deterministic reranker`

## Corpus

- namespace: periodic-full-v1
- disclosures/sections/tables: 1,054 / 82,862 / 1,401,752
- chunks/unique embeddings: 38,971 / 34,084
- embedding: CLOVA Studio bge-m3, embedding-v2, 1,024d, exact cosine

## Metrics

| set | Top1 | R@3 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|---:|
| candidate 120 (113 section eligible) | 14.16% | 34.51% | 38.05% | 53.10% | 0.2587 |
| reviewed 36 | 25.00% | 55.56% | 58.33% | 77.78% | 0.4212 |

Production-interface latency avg/P50/P95/max: 179.03/154.19/257.49/1877.97 ms (warm local DB; query embeddings reused from cache).

## Freeze and limitations

Frozen components and rejected experiments are documented in `docs/retrieval_v1_freeze.md`. Remaining limitations include 19 no-candidate questions, semantic mismatch, incomplete independent review for 84 questions, a deterministic non-neural reranker, and provider latency outliers.
