# Candidate recall ceiling

> Diagnostic only. Full 120 remains internal/candidate; gold and retrieval algorithms were unchanged.

## Depth ceiling

| depth | FTS | Vector | Union | FTS only | Vector only | Both | Neither |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 21/113 (18.58%) | 36/113 (31.86%) | 54/113 (47.79%) | 18 | 33 | 3 | 59 |
| 20 | 26/113 (23.01%) | 55/113 (48.67%) | 66/113 (58.41%) | 11 | 40 | 15 | 47 |
| 50 | 27/113 (23.89%) | 71/113 (62.83%) | 80/113 (70.80%) | 9 | 53 | 18 | 33 |
| 100 | 27/113 (23.89%) | 84/113 (74.34%) | 92/113 (81.42%) | 8 | 65 | 19 | 21 |

## Current 61 failures

- fusion_demoted_within_top10: 2
- recovered_by_top20: 12
- recovered_by_top50: 14
- not_found_top100: 21
- recovered_by_top100: 12

Filter-ablation recoveries: {}

Taxonomy labels are automated review aids and never modify gold.
