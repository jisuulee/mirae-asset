# Parent-section aggregation experiment

> Diagnostic/internal candidate experiment. Gold, embeddings, chunks, FTS, Vector model, RRF, and reranker scoring were unchanged.

Chosen experimental variant: raw chunk Top-50 + bounded Top-2 support. Score = best similarity + support bonus capped at 0.02.

## Vector-only

| path | Top1 | R@3 | R@5 | R@10 | R@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|
| current exact | 7.08% | 16.81% | 22.12% | 31.86% | 48.67% | 0.1496 |
| parent support2 | 7.08% | 16.81% | 21.24% | 32.74% | 48.67% | 0.1494 |

## Split/unsplit

| subset/path | Top1 | R@3 | R@5 | R@10 | R@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|
| split current | 0.00% | 0.00% | 0.00% | 11.76% | 29.41% | 0.0300 |
| split aggregated | 0.00% | 0.00% | 0.00% | 17.65% | 29.41% | 0.0308 |
| unsplit current | 8.33% | 19.79% | 26.04% | 35.42% | 52.08% | 0.1708 |
| unsplit aggregated | 8.33% | 19.79% | 25.00% | 35.42% | 52.08% | 0.1704 |

- Hybrid same-run best chunk: Top1 13.27%, R@5 37.17%, R@10 46.02%, MRR 0.2415
- Hybrid parent aggregated: Top1 13.27%, R@5 37.17%, R@10 46.90%, MRR 0.2431

## Hybrid split/unsplit

| subset/path | Top1 | R@3 | R@5 | R@10 | R@20 | MRR |
|---|---:|---:|---:|---:|---:|---:|
| split best chunk | 5.88% | 5.88% | 5.88% | 17.65% | 17.65% | 0.0752 |
| split aggregated | 5.88% | 5.88% | 5.88% | 23.53% | 23.53% | 0.0829 |
| unsplit best chunk | 14.58% | 37.50% | 42.71% | 51.04% | 51.04% | 0.2709 |
| unsplit aggregated | 14.58% | 37.50% | 42.71% | 51.04% | 51.04% | 0.2714 |

## Rank movement and bias

- Split rank changes: {'unchanged': 14, 'worsened': 1, 'improved': 2}
- New threshold entries: {'new_top10': 1, 'new_top5': 0, 'new_top3': 0, 'new_top1': 0}
- Split gold parents with at least two raw Top-50 chunk hits: 3
- Chunk-count/rank-promotion correlation: 0.1478
- Long-section share in Top-10: 12.04% -> 13.27%

## Remaining split failures

- gold_chunk_retrieved_but_parent_misranked: 6
- insufficient_multiple_chunk_signal: 1
- no_gold_chunk_retrieved: 3
- semantic_mismatch: 5

## Latency

- Parent aggregation avg/P95: 0.158/0.204 ms
- RRF avg/P95: 0.051/0.095 ms
- Reranker avg/P95: 0.765/3.592 ms
- End-to-end avg/P50/P95/max: 353.86/208.22/333.19/15499.20 ms

> The maximum includes a query-embedding API outlier; aggregation itself is sub-millisecond.

## Cache-correctness note

The same-run baseline includes FTS/vector ranks in the deterministic reranker cache key. Historical reports are retained separately because the previous cache key could reuse a score computed under different ranks; the scoring formula did not change.

Parent aggregation is not adopted as default: it adds one split Top-10 hit but no split Top-5/Top-1 improvement, slightly lowers overall Vector R@5, and does not materially solve split ranking.
