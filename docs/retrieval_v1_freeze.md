# Retrieval V1 freeze policy

Date: 2026-08-31

## Frozen components

- FTS ranking and depth 20
- CLOVA Studio Embedding v2 / bge-m3, version `embedding-v2`, dimension 1,024
- section paragraph chunking policy and exact-cosine Vector depth 20
- original-query Title/Path FTS+trigram channel, final depth 20
- unweighted RRF k=60 and fusion depth 10
- deterministic feature reranker formula and depth 10
- company/receipt/date/report/namespace filter semantics
- explicit/high-confidence correction retrieval semantics

Default changes require a Retrieval V2 experiment or explicit regression evidence. Benchmark tuning against the 84 independently unreviewed candidate questions is not sufficient.

## Not adopted in V1

- HNSW: metadata-filtered recall/latency trade-off did not justify replacing exact cosine.
- Depth50/100 expansion: candidate ceiling increased, but final ranking/cost did not justify promotion.
- Parent aggregation: only one additional split Top-10 hit and no split Top-5/Top-1 improvement.
- Deterministic query expansion: title/easy/exact drift outweighed the small ceiling gain.
- Spacing-only expansion: relatively safe but did not improve the Top-100 candidate ceiling.

All experiment code and reports remain diagnostic artifacts and are not imported by `search_default_sections()`.

## Downstream contract

Each result preserves `section_id`, `receipt_no`, company/report metadata, title, section path, evidence text, source file/locator/provenance, final rank/score, RRF/reranker scores, per-channel ranks/raw scores, `retrieval_sources`, and Vector chunk provenance where present. Existing source field names remain available; convenience aliases do not replace them.

## Known limitations and V2 backlog

- 19 questions have no gold candidate in any current channel at Top-100.
- semantic mismatch remains the dominant unresolved candidate/ranking class.
- 84 of 120 questions have no independent human review; 120 is an internal/candidate development benchmark.
- reviewed 36 is the stronger regression evidence.
- the deterministic reranker is not a neural or LLM reranker.
- query embedding provider latency can have outliers.
- HCX rewrite and stronger reranking are V2/backlog only, not part of this freeze.
