# Exact vs HNSW Vector experiment

> Policy: reviewed 36 is the reviewed regression set; full 120 remains an internal/candidate development benchmark.
> Exact top-10 is ANN reference truth. Benchmark gold, embeddings, chunks, and ranking were unchanged.

- HNSW: cosine, m=16, ef_construction=64; build 20.724s
- HNSW index: 281,051,136 bytes; projection including indexes/TOAST: 728,875,008 bytes
- ef_search: 100, 200, 400; candidate_k: 100; no cache flush; DB latency excludes query embedding

## Reviewed regression (36)

| mode | Section Top-1 | R@5 | R@10 | MRR | Receipt Top-1 | R@5 | R@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| exact | 2.78% | 8.33% | 19.44% | 0.0634 | 8.33% | 22.22% | 41.67% |
| 100 | 2.78% | 8.33% | 19.44% | 0.0558 | 13.89% | 25.00% | 38.89% |
| 200 | 2.78% | 8.33% | 19.44% | 0.0554 | 8.33% | 25.00% | 38.89% |
| 400 | 2.78% | 8.33% | 16.67% | 0.0508 | 8.33% | 25.00% | 36.11% |

## Full candidate (120; 113 section-vector eligible)

| mode | Section Top-1 | R@5 | R@10 | MRR | Receipt Top-1 | R@5 | R@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| exact | 7.08% | 22.12% | 31.86% | 0.1369 | 14.16% | 37.17% | 58.41% |
| 100 | 7.08% | 22.12% | 30.97% | 0.1334 | 17.70% | 42.48% | 60.18% |
| 200 | 7.08% | 22.12% | 31.86% | 0.1345 | 14.16% | 38.05% | 58.41% |
| 400 | 7.08% | 23.89% | 33.63% | 0.1418 | 14.16% | 39.82% | 61.06% |

## ANN fidelity and DB latency

| ef_search | ANN Recall@10 vs Exact | exact order match | avg ms | P50 | P95 | max |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 68.94% | 46.90% | 126.09 | 128.62 | 188.36 | 234.21 |
| 200 | 75.75% | 53.98% | 116.34 | 115.44 | 176.93 | 184.72 |
| 400 | 84.07% | 66.37% | 114.51 | 108.61 | 178.84 | 203.69 |

Exact overall DB latency: avg 81.91ms, P50 80.50ms, P95 109.24ms, max 137.12ms.

Filtered HNSW timings intentionally force the HNSW KNN path. PostgreSQL otherwise chooses metadata-index + exact sort for selective filters; that native exact choice can be faster.

## Recommendation

Among tested settings, ef_search=400 preserves the most Exact Top-10 results, but overall ANN Recall@10 is only 84.07%.
Namespace-only probes improve from 825.28ms Exact to 67.13ms HNSW, with 88.00% ANN Recall@10.
Do not replace Exact globally with this index configuration. Keep Exact for selective metadata filters; ef_search=400 is only the least-lossy experimental namespace-only option. A higher-quality index build must beat Exact Recall@10 before production adoption.
