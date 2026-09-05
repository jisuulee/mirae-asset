# Benchmark Human Review Guide

This package supports independent review of the 74 `needs_review` and 10 `ambiguous` questions. The existing 36 reviewed regression questions and the source benchmark are not edited by package generation.

## Open the review UI

Open `reports/human_review/index.html` directly in a modern browser. It is self-contained and does not load a CDN, external JavaScript, or a web API. `review_data.json` contains the same machine-readable cards.

Use Previous/Next, category and difficulty filters, review-status filtering, or question ID search. Progress is saved in browser `localStorage` under `periodic-full-benchmark-review-v1`. Local storage is browser/profile/local-file-origin specific, so export periodically.

The export button downloads `periodic_full_benchmark_review_export.json`. Keep this file outside the source benchmark until review is complete.

## Review order and leakage control

1. Read the question and filters.
2. Inspect current gold and Gold Evidence against the original disclosure text.
3. Inspect structural Alternative Evidence where needed. These are review candidates, not proposed gold.
4. Make a decision and record notes/accepted IDs.
5. Only afterward, if useful, open the collapsed Retrieval Diagnostic.

The diagnostic is explicitly marked `DO NOT USE RETRIEVAL RESULT AS GOLD TRUTH`. FTS/Vector success and ranking must not decide whether gold is correct.

## Decisions

- `ACCEPT`: question and current gold are valid as-is.
- `ACCEPT_MULTI_GOLD`: current gold is valid and additional evidence should be accepted.
- `CHANGE_GOLD`: question is valid but current gold is wrong.
- `REWRITE_QUESTION`: intended gold is valid but wording needs correction.
- `CHANGE_TARGET`: section/table/either/pdf_page is wrong.
- `AMBIGUOUS_KEEP`: intentional ambiguity is useful and should remain.
- `DROP`: unsuitable for the benchmark.

Use reviewer notes to explain the evidence. For change decisions, fill accepted receipt/section/table/page fields or the proposed question/target. IDs are comma-separated in the UI.

## Machine-readable fallback

`eval/periodic_full_benchmark_review.jsonl` contains exactly the 84 review targets with blank decision fields. It can be edited with JSONL-aware tools instead of the HTML UI.

## Validate a review export

The command below is dry-run by default. It validates decision values, duplicate and unknown IDs, accepted gold types/existence, source benchmark matching, and stable provenance. It writes a proposed-change report without editing the benchmark.

```powershell
python -m eval.apply_benchmark_review reports/human_review/periodic_full_benchmark_review_export.json
```

Actual output requires both `--apply` and an explicit `--output`; the source benchmark is never overwritten implicitly. Gold-changing decisions additionally require stable provenance reconstruction and validation. Do not apply until all 84 decisions have been independently reviewed.
