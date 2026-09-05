# Periodic Keyword Query Plans

Representative `EXPLAIN (ANALYZE, BUFFERS)` was run on the pilot DB for a Korean financial-statement query with a company filter.

## FTS

- Execution: 2.64 ms; 835 shared buffers hit.
- `disclosures_company_date_idx` was used through an Index Only Scan.
- `document_sections_receipt_order_idx` was used for section lookup; the GIN FTS index was not selected because the pilot/company join is small and receipt lookup is cheaper.
- Company lookup was a 70-row Seq Scan, appropriate at this scale.
- Top-N heapsort used 26 kB.

## Trigram

- Execution: 295.17 ms; 2,333 shared buffers hit.
- Both `document_sections_title_trgm_idx` and `document_sections_search_trgm_idx` were used through a BitmapOr.
- 1,649 bitmap candidates were removed during recheck; the broad body projection and similarity ranking dominate cost.
- Final ranking used a small quicksort after the bitmap heap scan.

## Combined

- Execution: 52.15 ms; 1,396 shared buffers hit.
- The planner used the receipt-order index and evaluated the FTS/trigram OR predicate while joining 100 section rows; it did not choose the trigram GIN path for this small filtered relation.
- Top-N heapsort used 26 kB.

The pilot is intentionally small, so Seq Scan and receipt-driven plans are not evidence of a missing index. No speculative index was added beyond the title/search trigram indexes justified by the trigram plan. Re-run plans after corpus expansion before changing indexes.
