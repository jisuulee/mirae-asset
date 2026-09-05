# Schema changelog

## 5.0.0 — Phase 5 final index and quality-gate contract

- Added `data/final/document_index.jsonl`, one lightweight row per canonical
  disclosure, without copying the Phase 2 content payload.
- Added structural content summaries, deterministic parse/metadata/lineage
  caution flags, and artifact references joined by `doc_id` plus `rcept_no`.
- Added complete-chain-only `is_latest_in_complete_chain` and
  `is_superseded_in_complete_chain` booleans. Both remain `null` for partial,
  ambiguous, unresolved, and otherwise unproven lineage.
- Added `data/final/artifact_manifest.json` with byte size, SHA-256, schema,
  record count, and purpose for every canonical Phase 2~5 artifact.
- Added atomic final publication, independent final validation, and the
  `handoff/role2` development package.
- Kept Phase 2 content, Phase 3 metadata, and Phase 4 lineage byte-compatible
  and unchanged. A merged 26.6 GB-class artifact remains opt-in only.

Backward compatibility: schemas `2.0.0`, `3.0.0`, and `4.0.0` are unchanged.
Phase 5 is an additive index and delivery contract.

## 4.0.0 — Phase 4 correction lineage contract

- Added `correction_lineage.jsonl`, one additive row per canonical correction.
- Added raw/normalized correction evidence with physical source provenance.
- Added deterministic resolution statuses: `linked_exact`, `linked_inferred`,
  `external_target`, `ambiguous`, and `unresolved`.
- Added explicit `target`, `parent`, `root`, `latest`, `chain_id`, and nullable
  sequence semantics.
- Added complete and partial `correction_chains.jsonl` graph components.
- Added opt-in, cached OpenDART lookup without persisting API keys.
- Phase 2 content and Phase 3 metadata files remain byte-compatible and are not
  rewritten. Consumers opt in by joining the Phase 4 sidecar on `doc_id` and
  checking `rcept_no`.

See [correction_schema.md](correction_schema.md).

## 3.0.0 — Phase 3 metadata contract

- Phase 2 `parsed_disclosures.jsonl`은 재작성하지 않았다.
- `metadata_normalized.jsonl` additive sidecar를 추가했다.
- manifest를 canonical metadata authority로 명시했다.
- `rcept_dt`를 보존하면서 strict 형식 변환값 `rcept_date`를 추가했다.
- `source_metadata.observations`를 canonical metadata와 분리했다.
- `metadata_validation.status`, `mismatch_count`, metadata 전용 `warnings`, join
  `checks`를 추가했다.
- Phase 2 `SOURCE_METADATA_MISMATCH`를 occurrence 단위 sidecar와 표준 reason
  taxonomy로 재검증했다.
- Role 2용 deterministic sample, selection evidence, artifact hash manifest를
  추가했다.
- correction lineage, DB, chunking, embedding, search 필드는 추가하지 않았다.

Backward compatibility: content schema `2.0.0`은 byte-level로 그대로 유지된다.
Phase 3는 `doc_id` 1:1 sidecar join을 사용하는 opt-in 확장이다.

## 2.0.0 — Phase 2 evidence-preserving parser

- logical disclosure 1행당 canonical content, attachments, alternative
  representations, source files, parse status와 warnings를 정의했다.
- block/table/source locator와 physical source SHA-256을 보존했다.
