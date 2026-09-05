# PostgreSQL Periodic Keyword Retrieval Baseline

Scope is the 20-receipt pilot DB and 36 manually reviewed section questions. No periodic parser/export was run in this step; no embedding, pgvector, hybrid RAG, reranker, or Agent integration is included.

## Normalization

Queries use Unicode NFKC, zero-width and special-space replacement, whitespace collapse, and casefold. Indexed source values remain unchanged; `search_text_normalized` is a generated search-only representation. Newlines and tabs are treated as whitespace.

## Benchmark composition

| Category | Questions |
|---|---:|
| company_section_title | 11 |
| company_concept | 4 |
| narrative_description | 4 |
| year_report_filtered | 3 |
| exact_phrase | 4 |
| unicode_spacing_variation | 2 |
| unicode_nfkc_variation | 1 |
| partial_korean_phrase | 3 |
| table_keyword | 3 |
| pdf_narrative | 1 |
| **Total** | **36** |

Gold section IDs were manually reviewed against receipt/order/title/body in the pilot DB; they were not generated from each method output. PDF gold intentionally tests the known page-section limitation.

## Cold comparison

| Mode | Top-1 section | R@3 | R@5 | R@10 | MRR | Top-1 receipt | R@5 receipt | Avg ms | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FTS | 58.33% | 63.89% | 69.44% | 72.22% | 0.624 | 77.78% | 88.89% | 5.43 | 3.97 | 18.95 |
| Trigram | 22.22% | 41.67% | 55.56% | 72.22% | 0.362 | 47.22% | 75.00% | 257.66 | 276.78 | 401.02 |
| Combined | 38.89% | 66.67% | 80.56% | 88.89% | 0.553 | 55.56% | 94.44% | 69.16 | 60.40 | 304.26 |

## Warm comparison

| Mode | Avg ms | P50 ms | P95 ms | Max ms |
|---|---:|---:|---:|---:|
| FTS | 4.67 | 3.56 | 13.84 | 19.11 |
| Trigram | 264.49 | 286.00 | 390.85 | 395.11 |
| Combined | 69.32 | 60.82 | 311.16 | 314.27 |

Warm runs use the same connection and repeated execution; PostgreSQL shared-cache state is not fully controlled. Connection setup is recorded separately and excluded from query latency.

## Category observations

FTS was strongest for exact phrases and had the lowest latency. Combined improved larger-K recall and receipt-level recall but paid a ranking cost. Trigram-only handled substring matching but was substantially slower and weaker at Top-1. Unicode NFKC variation succeeds; inserted-space variants remain a failure mode. Report-type filters use the DB code `annual`, not the display label `?????`.

## Recommendation

Use FTS as the default low-latency keyword baseline, with combined as an opt-in recall-oriented mode. ?Combined? means only FTS plus trigram scoring, not semantic hybrid retrieval. Weights are configurable (`0.60/0.30/0.10`) and are transparent starting values, not tuned claims.

## Known failures

The combined run has 4 failures; details and classifications are in [periodic_keyword_failures.json](periodic_keyword_failures.json). Receipt-level recall is higher than exact section recall, so both metrics are retained.

## Next gate

Before expanding to all 1,054 receipts, add a compact no-space search representation or spacing-aware query path, formalize report-type/date semantics, and decide whether PDF page sections are acceptable gold targets. Full parser/export and embedding remain separate later steps.
