# Deterministic Hybrid reranker baseline

> Reviewed 36 is regression; full 120 remains internal/candidate. Gold and candidate generation are unchanged.

Model: `deterministic-feature-reranker-v1` / `rank-lexical-v1`; rerank depth 10; API calls 0.

## Reviewed regression 36

| stage | Top-1 | R@3 | R@5 | R@10 | MRR | Receipt Top-1 | R@5 | R@10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fts | 50.00% | 55.56% | 55.56% | 58.33% | 0.5216 | 55.56% | 77.78% | 83.33% |
| vector | 2.78% | 5.56% | 8.33% | 19.44% | 0.0634 | 8.33% | 22.22% | 41.67% |
| d20_k60 | 16.67% | 27.78% | 38.89% | 63.89% | 0.2625 | 25.00% | 52.78% | 83.33% |
| reranked | 22.22% | 44.44% | 52.78% | 63.89% | 0.3625 | 30.56% | 61.11% | 83.33% |

## Full candidate 120

| stage | Top-1 | R@3 | R@5 | R@10 | MRR | Receipt Top-1 | R@5 | R@10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fts | 15.93% | 17.70% | 17.70% | 18.58% | 0.1662 | 17.70% | 24.78% | 26.55% |
| vector | 7.08% | 16.81% | 22.12% | 31.86% | 0.1369 | 14.16% | 37.17% | 58.41% |
| d20_k60 | 11.50% | 23.89% | 31.86% | 46.02% | 0.2004 | 19.47% | 46.90% | 71.68% |
| reranked | 13.27% | 30.09% | 35.40% | 46.02% | 0.2345 | 21.24% | 47.79% | 71.68% |

## Rank changes

- improved: 16
- unchanged: 33
- gold_not_in_candidates: 61
- worsened: 3

## Latency

Reranker avg/P50/P95/max: 4.504/4.372/7.961/12.089 ms.
End-to-end avg/P50/P95/max: 618.49/232.99/424.96/41611.55 ms.
