# Periodic full-corpus Keyword baseline

> Status: provisional on all 120 candidate rows; official on the reviewed 36-row regression subset.

All modes used the same natural-language question text and `periodic-full-v1` snapshot. No benchmark-driven ranking or index tuning was performed. `cold` means one application-level pass; PostgreSQL/OS caches were not forcibly flushed.

## All 120 candidate rows

| Mode | Section Top-1 | Section R@5 | Section R@10 | Section MRR | Receipt Top-1 | Receipt R@5 | Table Top-1 | Avg ms | P50 ms | P95 ms | Max ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FTS | 15.93% | 17.70% | 18.58% | 0.1662 | 16.67% | 23.33% | 0.00% | 12.89 | 3.80 | 50.71 | 391.80 |
| TRIGRAM | 7.08% | 13.27% | 15.93% | 0.0923 | 14.17% | 23.33% | 0.00% | 2554.69 | 396.77 | 15666.97 | 25781.46 |
| COMBINED | 8.85% | 15.93% | 21.24% | 0.1156 | 11.67% | 22.50% | 0.00% | 2159.08 | 395.77 | 16253.14 | 18211.03 |

Section metrics exclude table-only rows (n=113). Table metrics cover rows with actual table gold (n=9); all three modes scored 0 through R@10.

## Reviewed 36-row regression

FTS reproduced section Top-1 50.00%, R@3 55.56%, R@5 55.56%, R@10 58.33%, MRR 0.5216; receipt Top-1 55.56% and R@5 77.78%.

## FTS failure analysis

FTS had 99 primary-target misses. `semantic_paraphrase` accounts for 23/99 (23.23%).

| Taxonomy | Count |
|---|---:|
| gold_ambiguity | 10 |
| korean_spacing | 9 |
| lexical_mismatch | 26 |
| metadata_filter | 7 |
| pdf | 4 |
| ranking | 6 |
| semantic_paraphrase | 23 |
| table_target | 7 |
| tokenizer | 7 |

The automatic taxonomy is triage metadata, not human-reviewed diagnosis. Korean spacing/tokenizer misses and ambiguous gold require manual inspection before final publication.

## Representative query plans

| Query | Execution ms |
|---|---:|
| section_fts | 911.82 |
| section_trigram | 7117.69 |
| section_combined | 7263.83 |
| table_fts | 714.95 |
| table_trigram | 13741.57 |
| table_combined | 12100.65 |

The section FTS plan uses `document_sections_search_idx`. Section trigram uses the normalized/no-space trigram indexes. Representative table trigram/combined plans include a sequential scan and dominate latency. No index or weight was tuned after observing these results.

## Interpretation

The gap between the reviewed short-query regression and natural-language candidate rows is evidence that Keyword Retrieval alone is insufficient for semantic paraphrase. It is enough evidence to proceed to a separately implemented Vector baseline, but the 84 new gold rows still require independent human review before claiming an official cross-system comparison.