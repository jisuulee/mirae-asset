# Depth-50 Hybrid candidate expansion

> Full 120 is internal/candidate. Gold, retrievers, RRF k=60, and deterministic reranker scoring were unchanged.

Average FTS/Vector/union candidates: 6.32 / 48.92 / 51.78.

Union50 gold: 80; RRF Top10/20/30 gold: 48/62/71.

## Reviewed regression 36

| pipeline | Top1 | R@3 | R@5 | R@10 | MRR | Receipt Top1 | R@5 | R@10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| depth20-rerank10 | 22.22% | 44.44% | 52.78% | 63.89% | 0.3625 | 30.56% | 61.11% | 83.33% |
| depth50-rerank10 | 22.22% | 44.44% | 47.22% | 52.78% | 0.3431 | 30.56% | 58.33% | 80.56% |
| depth50-rerank20 | 22.22% | 44.44% | 47.22% | 66.67% | 0.3610 | 33.33% | 63.89% | 86.11% |
| depth50-rerank30 | 22.22% | 44.44% | 47.22% | 72.22% | 0.3676 | 33.33% | 63.89% | 86.11% |

## Full candidate 120

| pipeline | Top1 | R@3 | R@5 | R@10 | MRR | Receipt Top1 | R@5 | R@10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| depth20-rerank10 | 13.27% | 30.09% | 35.40% | 46.02% | 0.2345 | 21.24% | 47.79% | 71.68% |
| depth50-rerank10 | 13.27% | 30.09% | 33.63% | 42.48% | 0.2283 | 21.24% | 46.90% | 70.80% |
| depth50-rerank20 | 13.27% | 30.97% | 34.51% | 47.79% | 0.2373 | 22.12% | 49.56% | 71.68% |
| depth50-rerank30 | 13.27% | 30.97% | 34.51% | 49.56% | 0.2394 | 22.12% | 49.56% | 69.91% |

## Pruning and decision

Union50 contains 80 gold-bearing questions; RRF Top10/20/30 retains 48/62/71.

Expanded-30 has the best measured R@10/MRR with only a small reranker-latency increase, so it is the preferred experimental setting among the four choices. However, none of the 14 questions newly found beyond the old Top-20 union reached final Top-10. Depth-50 retrieval itself is therefore not yet proven as the cause of the metric gain; do not promote it as production default.
