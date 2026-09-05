# Phase 4 correction lineage schema

Schema version: `4.0.0`

Phase 4 is an additive sidecar over the immutable Phase 2 content schema `2.0.0`
and Phase 3 metadata schema `3.0.0`. The join key is `doc_id`; `rcept_no` is a
required secondary consistency check. `correction_lineage.jsonl` contains exactly
one row for each manifest-authoritative `is_correction=true` disclosure.

## `correction_lineage.jsonl`

```json
{
  "schema_version": "4.0.0",
  "doc_id": "exchange_20250731800028",
  "rcept_no": "20250731800028",
  "is_correction": true,
  "correction_kind": "기재정정",
  "resolution": {
    "status": "linked_exact",
    "method": "company_date_report",
    "policy_version": "corpus-deterministic-v1",
    "confidence": 1.0,
    "target_rcept_no": "20250728800000",
    "target_doc_id": "exchange_20250728800000",
    "candidate_count": 1,
    "candidates": [],
    "stages": [],
    "external_lookup": {"enabled": false, "status": "not_requested"}
  },
  "lineage": {
    "parent_rcept_no": "20250728800000",
    "root_rcept_no": "20250728800000",
    "latest_rcept_no": "20250731800028",
    "chain_id": "chain_...",
    "sequence": 1
  },
  "correction_evidence": {},
  "metadata_snapshot": {},
  "content_reference": {
    "dataset": "parsed_disclosures.jsonl",
    "join_key": "doc_id"
  }
}
```

## Resolution status

| Status | Meaning | Safe automatic target use |
| --- | --- | --- |
| `linked_exact` | One corpus target matched an explicit receipt number, or canonical company + source-explicit date + compatible report name | Yes |
| `linked_inferred` | One corpus target remained after canonical company + source-explicit date + manifest classification | Yes, retain method/confidence |
| `external_target` | One OpenDART target was found outside the local corpus in explicit opt-in mode | Yes, but local content is unavailable |
| `ambiguous` | More than one target satisfies the strongest available deterministic rule | No |
| `unresolved` | No target satisfies the deterministic rules | No |

`confidence` is a policy label, not a learned probability. It is `1.0` for exact
rules, `0.8` for classification inference, and `0.0` when no target is asserted.
No similarity score or nearest-previous rule is used.

## Target, parent, root, latest

- `target_rcept_no`: the disclosure referenced by the resolver's accepted evidence.
- `parent_rcept_no`: the immediately preceding known chain member. It may differ
  from `target_rcept_no` when several corrections explicitly reference the root.
- `root_rcept_no`: the ultimate non-correction filing, only when the graph reaches
  one deterministically.
- `latest_rcept_no`: the latest known member within the constructed component.
- `chain_id`: SHA-256-derived deterministic identifier; it contains no inferred
  business meaning.
- `sequence`: zero-based at the root and positive for complete chains. It is
  `null` for a partial chain whose root is not known.

A complete chain has `root_rcept_no != null` and contiguous sequence values. A
partial chain preserves known correction-to-correction edges but has
`root_rcept_no=null`, `sequence=null`, and `correction_chains.is_complete=false`.

## Evidence contract

`correction_evidence.evidence[]` preserves:

- semantic field (`target_report_name`, `original_submission_date`,
  `related_disclosure_date`, `correction_date`, `correction_reason`, or
  `correction_items`);
- `raw_label` and `raw_value` from the parser output;
- conservative `normalized_value`;
- extraction type (`table_cell_pair`, `table_column_value`,
  `block_label_value`, adjacent-block value, or section marker);
- physical source path, file role, component/table/row/cell or block locator;
- Phase 2 provenance object.

Source values are never written back into canonical Phase 3 metadata. Invalid or
nonstandard dates remain `null`; the raw value remains available for review.

## `correction_chains.jsonl`

Each row describes one connected correction component. `members` are compact
references, not copies of Phase 2 content. `is_complete=true` requires an
in-corpus non-correction root. Partial chains use an `anchor_rcept_no` only to
make their deterministic ID reproducible; the anchor is not asserted to be the
business root.

## Optional OpenDART lookup

External lookup runs only with both `--enable-opendart` and
`OPEN_DART_API_KEY`. Responses are cached by public request parameters under
`data/cache/opendart`; the API key is excluded from cache keys and files.
`correction_external_lookup.jsonl` records attempts and classified failures.
Corpus-only runs never perform network requests and publish an empty lookup file.

## Null semantics

`null` means the source evidence or deterministic graph cannot establish the
value. It does not mean zero, false, not applicable, or that no relationship
exists. Downstream code must not fill null lineage values by receipt-number
proximity, title similarity, or LLM inference.

## Invariants

- Exactly 1,004 unique correction `doc_id` rows in the current corpus.
- Every `linked_exact`/`linked_inferred` target exists in the local metadata index.
- No directed correction cycle.
- No target date after its correction date.
- Every linked local row belongs to a complete or partial chain.
- Complete roots are non-corrections; complete sequence values are contiguous.
- Partial chain root and sequence values are null.
