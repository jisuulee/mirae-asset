# Query expansion diagnostic

> Deterministic, corpus-grounded internal/candidate experiment. Benchmark gold was not used or modified.

## Candidate retrieval

| path | Top1 | R@5 | R@10 | R@100 | MRR |
|---|---:|---:|---:|---:|---:|
| Original fts | 15.93% | 17.70% | 18.58% | 23.89% | 0.1695 |
| Combined fts | 15.93% | 25.66% | 35.40% | 51.33% | 0.2184 |
| Original vector | 7.08% | 22.12% | 31.86% | 74.34% | 0.1558 |
| Combined vector | 9.73% | 23.01% | 33.63% | 74.34% | 0.1786 |
| Original union | 9.73% | 30.09% | 42.48% | 81.42% | 0.1990 |
| Combined union | 12.39% | 31.86% | 46.90% | 82.30% | 0.2318 |

## Final Hybrid + deterministic reranker

| variant | Top1 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|
| original | 14.16% | 37.17% | 46.02% | 0.2444 |
| spacing | 15.04% | 36.28% | 46.90% | 0.2543 |
| title_path | 14.16% | 36.28% | 45.13% | 0.2414 |
| combined | 15.04% | 35.40% | 45.13% | 0.2498 |

## Recovery subsets

- top100_neither: 2 candidate recoveries; final Top10 0 / Top5 0 / Top1 0
- semantic_mismatch: 2 candidate recoveries; final Top10 0 / Top5 0 / Top1 0
- lexical_mismatch: 2 candidate recoveries; final Top10 0 / Top5 0 / Top1 0
- no_gold_chunk_retrieved: 0 candidate recoveries; final Top10 0 / Top5 0 / Top1 0
- split_long: 0 candidate recoveries; final Top10 0 / Top5 0 / Top1 0
- ambiguous_similar_sections: 0 candidate recoveries; final Top10 0 / Top5 0 / Top1 0

Rank movement: {'worsened': 30, 'improved': 40, 'unchanged': 43}; original Top10 lost: 3.

Failure taxonomy: {'candidate_found_but_low_rank': 42, 'expansion_drift': 9, 'still_no_candidate': 11, 'ambiguous_gold': 9}.

## Cost and decision

- Query embeddings generated: 261; attempts: 264; retries: 3
- Input tokens: 5396; estimated VAT-exclusive cost: 1.0792 KRW
- Decision: do_not_adopt
- Next: title/path dedicated retrieval before a rule-gated HCX rewrite experiment
