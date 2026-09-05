# Full periodic corpus Keyword Retrieval baseline

Scope: 1,054 periodic receipts, 82,862 retained sections, 1,401,752 retrieval table projections. No embedding, pgvector, vector search, LLM, or Agent integration. Metrics are cold single-connection runs over the existing 36 manually reviewed questions.

| Mode | Top1 | R@3 | R@5 | R@10 | MRR | Avg ms | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FTS | 50.00% | 55.56% | 55.56% | 58.33% | 0.522 | 61.41 | 57.11 | 136.85 |
| Trigram | 16.67% | 19.44% | 27.78% | 30.56% | 0.198 | 490.56 | 412.23 | 2146.68 |
| Combined | 22.22% | 27.78% | 36.11% | 47.22% | 0.271 | 475.16 | 396.21 | 2142.84 |

## Pilot -> full change

| Mode | d Top1 | d R@5 | d MRR | d Avg ms |
|---|---:|---:|---:|---:|
| FTS | -8.33pp | -13.89pp | -0.102 | +12.60 |
| Trigram | -5.56pp | -27.78pp | -0.164 | +3731.53 |
| Combined | -16.67pp | -44.44pp | -0.281 | +365.78 |

## Interpretation

Full-corpus competition lowers exact section recall and increases latency, especially for trigram and combined ranking. This is expected ranking ambiguity, not parser corruption. FTS is the current default due to the best latency/Top-1 tradeoff; Combined is useful only when larger-K recall is preferred. Trigram GIN indexes are used but similarity recheck/ranking remains expensive.

## Coverage and storage

- Parser: 1,054/1,054 receipts succeeded; 0 exceptions.
- Sections: 86,452 input; 82,862 retained (3,590 empty leaf excluded).
- Tables: 1,401,752 raw records and projections; 0 orphan.
- PostgreSQL relation sizes: document_sections 1,092 MB; retrieval_tables 4,006 MB (indexes included).
- Lossless tables JSONL is approximately 16 GB; retain as archive, not in retrieval rows.

## Gold/evaluation policy

XML/HTML questions use section-level gold. PDF fallback uses page-level synthetic sections and page-number provenance; synthetic titles are not treated as semantic section titles. The 36-question set is a regression baseline, not a representative full-corpus benchmark.

## Next gate

Before pgvector/vector work, expand the manually reviewed benchmark to roughly 80-150 questions, formalize no-space matching and report-type/date semantics, and decide whether the 16 GB lossless archive should be compressed/chunked.
