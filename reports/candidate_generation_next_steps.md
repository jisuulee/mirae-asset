# Candidate-generation next steps

| priority | option | expected impact | complexity | latency | rule risk |
|---:|---|---|---|---|---|
| 1 | Increase diagnostic candidate depth to 50 with bounded downstream pruning | High: union rises from 66/113 at Top-20 to 80/113 at Top-50 | Low | Medium | Low |
| 2 | Parent-section aggregation diagnosis for split/long sections | High for the structurally weak split subset | Medium | Medium | Low |
| 3 | Query expansion/rewriting as a separate experiment | Potentially high for Top-100 neither misses | Medium/High | Medium | Medium; provider/rule approval required |
| 4 | Dedicated title/path retrieval, evaluated independently | Targeted; only a small failure subset has strong title/path overlap | Medium | Low | Low |
| 5 | PDF/table-specific retrieval | Low for these 61 section failures; table-only remains a separate track | High | Medium | Low |
| 6 | Metadata routing changes | Low: ablation recovered no misses | Medium | Low | Low |

Recommendation: test candidate depth 50 first because it is the lowest-risk change with a directly measured ceiling gain. Keep the current algorithms and evaluate latency plus reranker pruning. Next, diagnose parent-section aggregation on split/long sections. Top-100 still leaves 21/113 misses, so depth alone cannot solve the corpus.
