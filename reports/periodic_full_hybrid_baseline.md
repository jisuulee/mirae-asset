# FTS + Exact Vector Semantic Hybrid baseline

> Reviewed 36 is the reviewed regression set. Full 120 is an internal/candidate development benchmark, not an official human-reviewed set.

Primary: FTS Top-20 + Exact Vector Top-20, unweighted RRF k=60 (`d20_k60`). HNSW and reranking are not used.

## Reviewed regression (36)

| mode | Section Top-1 | R@5 | R@10 | MRR | Receipt Top-1 | R@5 | R@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| fts | 50.00% | 55.56% | 58.33% | 0.5216 | 55.56% | 77.78% | 83.33% |
| vector | 2.78% | 8.33% | 19.44% | 0.0634 | 8.33% | 22.22% | 41.67% |
| d20_k60 | 16.67% | 38.89% | 63.89% | 0.2625 | 25.00% | 52.78% | 83.33% |

## Full candidate (120; 113 section eligible)

| mode | Section Top-1 | R@5 | R@10 | MRR | Receipt Top-1 | R@5 | R@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| fts | 15.93% | 17.70% | 18.58% | 0.1662 | 17.70% | 24.78% | 26.55% |
| vector | 7.08% | 22.12% | 31.86% | 0.1369 | 14.16% | 37.17% | 58.41% |
| d20_k60 | 11.50% | 31.86% | 46.02% | 0.2004 | 19.47% | 46.90% | 71.68% |

## Top-10 complementarity

- FTS only: 18; Hybrid preserved: 17
- Vector only: 33; Hybrid preserved: 32
- Both: 3; Hybrid preserved: 3
- Neither: 59

## Latency

| component | avg | P50 | P95 | max |
|---|---:|---:|---:|---:|
| fts_db | 20.97ms | 5.41ms | 117.20ms | 582.23ms |
| query_embedding | 497.09ms | 125.35ms | 185.08ms | 41467.92ms |
| exact_vector_db | 95.88ms | 93.22ms | 156.84ms | 425.68ms |
| fusion | 0.05ms | 0.03ms | 0.10ms | 0.39ms |
| total | 613.99ms | 228.47ms | 419.63ms | 41605.42ms |

All failure taxonomy labels are automatic review aids; benchmark gold was not changed.
