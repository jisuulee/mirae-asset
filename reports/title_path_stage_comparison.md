# Title/path dedicated retrieval experiment

> Original query only; body text, benchmark gold, external models, and query expansion were not used by the title channel.

## Title-only

| mode | Top1 | R@3 | R@5 | R@10 | R@20 | R@50 | MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| title_fts | 15.93% | 15.93% | 15.93% | 15.93% | 16.81% | 16.81% | 0.1599 |
| title_trigram | 20.35% | 24.78% | 25.66% | 27.43% | 30.97% | 30.97% | 0.2327 |
| title_combined | 23.89% | 28.32% | 29.20% | 30.97% | 34.51% | 34.51% | 0.2681 |

## Final ranking

| path | Top1 | R@3 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|---:|
| baseline | 14.16% | 32.74% | 37.17% | 46.02% | 0.2444 |
| title_1_0 | 14.16% | 34.51% | 38.05% | 53.10% | 0.2587 |

Chosen bounded comparison: title_1_0.
Candidate recoveries: {'count': 5, 'final_top10': 2, 'final_top5': 0, 'final_top3': 0, 'final_top1': 0, 'rows': [{'question_id': 'full-pk-020', 'title_rank': 2, 'final_rank': None}, {'question_id': 'full-pk-022', 'title_rank': 2, 'final_rank': None}, {'question_id': 'full-pk-035', 'title_rank': 4, 'final_rank': None}, {'question_id': 'full-ambi-002', 'title_rank': 1, 'final_rank': 6}, {'question_id': 'full-ambi-003', 'title_rank': 1, 'final_rank': 6}]}
Damage: {'top1_damaged': 0, 'top3_damaged': 0, 'top5_damaged': 0, 'top10_damaged': 0}
Failures: {'semantic_mismatch': 29, 'no_candidate_any_channel': 19, 'title_candidate_low_rank': 5}

Decision: adopt=True; next=retrieval_freeze.
