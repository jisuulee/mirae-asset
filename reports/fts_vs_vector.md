# FTS vs Vector

Gold, chunking, embedding model, and ranking were not changed.

## Integrity

- Status: `pass`
- Vectors/chunks/links: `34,084` / `38,971` / `38,971`
- Model/version/dimension: `clova-studio/bge-m3` / `embedding-v2` / `1024`
- PDF narrative pages: `1,784` across `3` receipts
- Vector-related storage: `296.72 MiB`
- ANN indexes: none

## Overall comparison

| Scope | Engine | Section Top-1 | R@5 | R@10 | MRR | Receipt Top-1 | Receipt R@5 |
|---|---|---:|---:|---:|---:|---:|---:|
| Reviewed 36 | FTS | 50.00% | 55.56% | 58.33% | 0.5216 | 55.56% | 77.78% |
| Reviewed 36 | Vector | 2.78% | 8.33% | 19.44% | 0.0634 | 8.33% | 22.22% |
| Candidate 120 (113 section-eligible) | FTS | 15.93% | 17.70% | 18.58% | 0.1662 | 17.70% | 24.78% |
| Candidate 120 (113 section-eligible) | Vector | 7.08% | 22.12% | 31.86% | 0.1369 | 14.16% | 37.17% |

Vector latency is end-to-end query embedding plus exact PostgreSQL cosine search; it was a single application-level pass without forcibly flushing OS/PostgreSQL caches.

## Category comparison (section Top-1)

| Category | Eligible | FTS | Vector |
|---|---:|---:|---:|
| ambiguous_similar_sections | 10 | 0.00% | 10.00% |
| company_concept | 15 | 13.33% | 6.67% |
| company_section_title | 15 | 26.67% | 0.00% |
| difficult_or_ambiguous | 5 | 0.00% | 20.00% |
| exact_phrase | 10 | 40.00% | 0.00% |
| narrative_description | 20 | 10.00% | 0.00% |
| partial_korean_phrase | 10 | 10.00% | 0.00% |
| pdf_fallback | 5 | 20.00% | 60.00% |
| spacing_or_expression_variation | 10 | 10.00% | 0.00% |
| table_related | 3 | 66.67% | 0.00% |
| year_report_filtered | 10 | 10.00% | 20.00% |

`table_related` contains seven table-only questions excluded from section-vector denominators and three `either` questions.

## Difficulty comparison (section Top-1)

| Difficulty | Eligible | FTS | Vector |
|---|---:|---:|---:|
| easy | 25 | 32.00% | 0.00% |
| hard | 20 | 5.00% | 25.00% |
| medium | 68 | 13.24% | 4.41% |

## Chunking

- Unsplit: `96` questions, Top-1 `8.33%`, R@10 `35.42%`
- Split: `17` questions, Top-1 `0.00%`, R@10 `11.76%`
- Split misses are boundary/long-section review candidates, not proof that chunking caused the miss; no gold was changed.

## FTS failure recovery at Vector R@10

| FTS taxonomy | Failures | Recovered | Rate |
|---|---:|---:|---:|
| semantic_paraphrase | 23 | 5 | 21.74% |
| lexical_mismatch | 26 | 11 | 42.31% |
| korean_spacing | 9 | 3 | 33.33% |
| tokenizer | 7 | 2 | 28.57% |
| ranking | 6 | 1 | 16.67% |
| hard | 19 | 6 | 31.58% |

## Top-1 four-way comparison

| Scope | Both | FTS only | Vector only | Neither |
|---|---:|---:|---:|---:|
| Reviewed 36 | 1 | 17 | 0 | 18 |
| Candidate section-eligible 113 | 1 | 17 | 7 | 88 |

## Vector failure taxonomy

Automatic taxonomy is a human-review aid and does not change benchmark gold.

- `gold_ambiguity`: 9
- `long_section_or_chunk_boundary_candidate`: 15
- `overgeneralization`: 34
- `pdf`: 1
- `same_company_similar_section`: 16
- `semantic_mismatch`: 2

## End-to-end latency

- avg/P50/P95/max: `512.71` / `184.74` / `249.04` / `36493.54` ms
- The maximum includes provider rate-limit capacity waiting; DB-only plans are reported separately.

## DB-only exact cosine plans

| Filter | Execution ms |
|---|---:|
| namespace_only | 2499.41 |
| company_filter | 110.67 |
| receipt_filter | 18.04 |
| year_report_filter | 92.92 |

Plans use an existing stored 1,024-d vector to isolate PostgreSQL execution. No ANN index exists.

## Decisions

- HNSW: warranted as a controlled next experiment because namespace-only exact execution is about 2.5 s, while filtered exact queries are much faster. Exact R@10 remains the reference truth; no ANN index was created here.
- Semantic Hybrid: experimentally warranted because candidate Top-1 contains both FTS-only and Vector-only wins, especially on hard/PDF cases. Evidence is preliminary because the reviewed 36 has no Vector-only Top-1 win and 84 candidate questions still require review.
