# Full-corpus Retrieval benchmark policy

## Scope and version

`periodic-full-retrieval-v1` compares Keyword, future Vector, Hybrid, and Reranker systems against the same `periodic-full-v1` corpus snapshot. The main-document periodic corpus is authoritative: 1,054 disclosures and their `document_sections`/`retrieval_tables` projections. Original titles, bodies, tables, and lossless archives are never rewritten for evaluation.

The benchmark has three explicit splits. The existing 36 questions are the immutable `regression` split. Newly constructed questions are assigned deterministically to `development` or `held_out`; tuning is allowed only on development. Held-out results are for final model comparison, not ranking-weight selection.

## Candidate selection and leakage

New gold candidates are selected from corpus metadata and source content by deterministic stratification across company, year, report type, section kind and length, document size, and XML/PDF source. Retrieval outputs and scores are not candidate-selection inputs. Questions use user-like paraphrases where appropriate and must not simply copy an answer sentence. Exact and partial phrase categories are intentional lexical test cases.

Generated rows are not silently promoted to official gold. `reviewed`, `needs_review`, and `ambiguous` are distinct states. Official reports show all rows and `reviewed_only` separately. Human review checks neighboring sections, repeated titles, competing years/reports, table-only and hierarchy-only nodes, and semantically duplicated content. Every genuinely relevant section/table may be included as multi-gold.

## Corpus and attachment policy

The loader namespace is stored per row and must be passed explicitly to retrieval. `periodic-full-v1` is the authoritative namespace. The baseline evaluates the parser-selected main document. Audit, consolidated-audit, and alternative HTML attachments remain provenance-bearing source files but are not silently mixed into the main-document baseline. Any later attachment-inclusive track requires a separate corpus snapshot and benchmark version.

Report types use the database canonical values `annual`, `half`, and `quarter`. Company filtering uses the canonical company attached to the disclosure; aliases are normalized before filtering. Receipt filtering is exact. Year is represented by an inclusive `[date_from, date_to]` filing-date interval, and report-type filters are explicit. Filters are benchmark inputs shared by every retrieval method, not hints available to only one method.

## Text normalization

Queries follow the existing retrieval normalization: Unicode NFKC, zero-width character removal, whitespace collapse/trim, and case folding. No-space variants are derived for Korean spacing retrieval where the implementation supports them. The source title/body/table archive remains unchanged. Benchmark questions also remain unchanged across Keyword, Vector, Hybrid, and Reranker runs.

## Targets and stable gold

Each row declares one target:

- `section`: semantic XML/HTML section retrieval.
- `table`: `retrieval_tables` projection retrieval; it is not scored as a section-only task.
- `either`: either section context or its table projection may satisfy the information need, with both levels reported separately.
- `pdf_page`: page-level synthetic section retrieval for PDF fallback.

Numeric `section_id` is a snapshot-local convenience, not the permanent gold key. Each gold stores receipt number, file identity, source locator, section order, stable section key, section path when available, and source SHA-256 when available. Validation must confirm that snapshot-local IDs resolve to the same stable provenance. Table gold additionally stores stable `table_id`, containing section, source locator, and archive identifier. PDF gold uses `page:N` locators; synthetic `PDF page N` titles are never treated as semantic titles.

## Metrics

Section, deduplicated receipt, and table rankings report Top-1, Recall@3/5/10, and MRR. Receipt ranking is derived from ranked results by deduplicating `receipt_no` in first-occurrence order. Multi-relevant rows additionally report relevant-set coverage@10, MAP@10, and binary-relevance nDCG@10. For MAP, duplicate predictions count once and the denominator is `min(number of relevant items, 10)`. For nDCG, every gold item has equal binary relevance.

PDF rows are grouped separately and scored at their page-level section IDs/provenance. Reports aggregate overall, category, difficulty, target, report type, XML/PDF source, review status, and split. Missing predictions and empty result lists are misses, not dropped rows. Latency reports average, P50, P95, and maximum with cache condition (`cold` or repeated `warm`) recorded.

## Baseline and failure analysis

FTS, trigram, and their existing weighted combined mode are run without benchmark-driven ranking changes. The baseline records the corpus snapshot, query condition, and prediction artifacts. Representative `EXPLAIN ANALYZE` plans confirm FTS GIN use and document trigram/combined bottlenecks before any later optimization.

FTS misses receive a reviewable initial taxonomy: `lexical_mismatch`, `semantic_paraphrase`, `korean_spacing`, `tokenizer`, `ambiguous_section`, `same_company_same_title`, `cross_year_confusion`, `ranking`, `metadata_filter`, `table_target`, `pdf`, `insufficient_source_text`, or `gold_ambiguity`. The automatic label is a triage aid and remains subject to human correction.