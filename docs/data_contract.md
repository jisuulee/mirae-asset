# DART Disclosure Metadata Data Contract

- Metadata schema: `3.0.0`
- Final index / delivery schema: `5.0.0`
- Compatible content schema: `2.0.0`
- Canonical metadata authority: corpus `manifest.jsonl`
- Join key: `doc_id`
- Secondary consistency key: `rcept_no`

## Phase 5 final index addendum

Phase 5 adds `data/final/document_index.jsonl`. It contains exactly one
lightweight row per manifest disclosure and does not embed the large Phase 2
content object. The authoritative join is:

```text
document_index.doc_id = metadata_normalized.doc_id = parsed_disclosures.doc_id
```

Consumers must also compare `rcept_no`. A `doc_id` match with a different
`rcept_no` is a failed join, not a recoverable alias.

### Index row

| Path | Type | Nullable | Meaning |
| --- | --- | --- | --- |
| `schema_version` | string | no | Always `5.0.0` |
| `doc_id` | string | no | Unique logical disclosure identifier |
| `rcept_no` | string | no | Unique 14-character receipt identifier; never cast to number |
| `metadata` | object | field-dependent | Canonical Phase 3 metadata from manifest authority |
| `content_summary.parse_status` | enum | no | `success`, `partial`, or `failed` |
| `content_summary.has_title` | boolean | no | Extracted canonical main title is non-empty |
| `content_summary.has_body` | boolean | no | Main body blocks or source table cells are non-empty |
| `content_summary.has_sections` | boolean | no | Source-supported section boundaries exist |
| `content_summary.section_count` | integer | no | Canonical main-document section count |
| `content_summary.has_tables` | boolean | no | Canonical main-document tables exist |
| `content_summary.table_count` | integer | no | Structural table count; not a semantic table class |
| `content_summary.attachment_count` | integer | no | Preserved attachment document count |
| `content_summary.source_file_count` | integer | no | Physical source files for the logical disclosure |
| `content_summary.source_sha256_count` | integer | no | Physical sources with SHA-256 |
| `content_summary.has_pdf_fallback` | boolean | no | Phase 2 explicitly used PDF fallback |
| `content_summary.has_table_span_warning` | boolean | no | At least one span-normalization warning exists |
| `lineage` | object | field-dependent | Phase 4 projection governed by the rules below |
| `quality` | object | no | Deterministic caution flags; not an LLM score |
| `references` | object | no | Canonical artifact names and `doc_id` join policy |

The index does not create business-event categories, table semantics, section
summaries, keywords, chunks, embeddings, or inferred metadata.

### Lineage safety fields

| Field | Complete chain | Partial / ambiguous / unresolved / unknown |
| --- | --- | --- |
| `is_complete_chain` | `true` | `false` for a recorded partial chain, otherwise `null` |
| `is_latest_in_complete_chain` | boolean | `null` |
| `is_superseded_in_complete_chain` | boolean | `null` |
| `root_rcept_no` | safe root | nullable evidence-preserving value |
| `parent_rcept_no` | confirmed parent for a correction | `null` when unconfirmed |
| `latest_rcept_no` | complete-chain latest | nullable descriptive chain value |

Only complete chains authorize automatic latest/superseded decisions. `null`
does not mean `false`. A partial, ambiguous, or unresolved document must not be
silently removed from search results, attached to a guessed original, or
converted to a latest-document boolean.

`quality.requires_lineage_guard=true` identifies corrections for which an
automatic lineage deduplication policy is unsafe. Default search should retain
them and surface a caution. A lineage-aware view may remove only rows where
`is_superseded_in_complete_chain=true`; guarded rows remain visible.

### Quality flags

| Field | Rule |
| --- | --- |
| `requires_parse_caution` | `parse_status != "success"` |
| `requires_metadata_caution` | Phase 3 status is not `valid` |
| `requires_lineage_guard` | correction is unresolved/ambiguous or lacks complete-chain proof |
| `safe_for_automatic_lineage_dedup` | non-correction or member of a complete chain |

`partial` parsing and `valid_with_warning` metadata remain searchable. These
flags communicate caution; they are not exclusion rules.

### Evidence, artifacts, and large-file policy

`body_text` is a convenience search view and is not a citation source. A
citation must resolve to a Phase 2 block, section, table, or raw table cell
provenance locator and the corresponding `source_files[].sha256`. For PDF
fallback, use the page locator. When table span normalization warned, use
`raw_rows` and cell provenance rather than assuming the normalized grid is
complete.

`data/final/artifact_manifest.json` records the relative path, schema version,
byte size, SHA-256, record count, and purpose of every canonical artifact.
Consumers must verify the hash before reuse. The default build does not create
a merged copy of the 26.6 GB content file. `--materialize-merged` is explicit
opt-in and publishes one large file atomically only after validation.

`handoff/role2` contains the contract, artifact manifest, document index,
representative sample and matching sample index, selection evidence, known
limitations, and integration notes. It intentionally excludes a second copy
of `parsed_disclosures.jsonl`.

Three non-canonical PDF/HTML `alternative_representations` have
`content_included=false` to avoid duplicating content. Their physical path,
format, parse status, warnings, and SHA-256 remain preserved. The Phase 2 run
counted 930 provenance items while parsing those representations; those items
are not serialized and therefore are not part of the Phase 5 direct structured
content provenance count. Phase 5 reports both totals explicitly.

이 계약은 Phase 2의 본문 JSONL을 변경하지 않고, Phase 3 metadata sidecar를
`doc_id`로 1:1 결합하는 규칙을 정의한다. 검색·RAG·DB 시스템은 원문에서 다시
추출한 값으로 canonical metadata를 덮어쓰면 안 된다.

## 1. Dataset topology

| Artifact | Schema | 역할 |
| --- | --- | --- |
| `data/processed/parsed_disclosures.jsonl` | `2.0.0` | 원본 구조와 provenance를 보존한 canonical content |
| `data/processed/metadata_normalized.jsonl` | `3.0.0` | 전체 corpus canonical metadata와 검증 결과 |
| `reports/metadata_mismatches.jsonl` | mismatch contract | 원문 관찰값과 manifest 값의 차이 1 occurrence당 1행 |
| `reports/metadata_summary.json` | `3.0.0` | 집계, join, completeness, quality gate |
| `handoff/sample_disclosures.jsonl` | `2.0.0` | 개발용 대표 content sample; 원본 JSONL 행을 byte 그대로 복사 |
| `handoff/sample_selection.jsonl` | selection `1.0.0` | 대표 샘플 선정 근거와 원본 행 hash |
| `handoff/handoff_manifest.json` | handoff manifest `1.0.0` | 파일별 count, size, SHA-256, join 및 readiness |

모든 JSONL은 UTF-8, LF, BOM 없음, 한 줄당 JSON object 하나를 사용한다. 전체
content는 매우 크므로 line-by-line streaming으로 읽는다.

## 2. Join contract

```text
parsed_disclosures.jsonl.doc_id
  1 : 1
metadata_normalized.jsonl.doc_id
```

- `doc_id`는 전체 logical disclosure corpus에서 unique여야 한다.
- 조인 후 양쪽의 `rcept_no`가 값과 JSON type까지 같아야 한다.
- metadata-only, content-only, 중복 key, 다른 접수번호 연결은 quality-gate
  failure다.
- Phase 2의 top-level canonical fields와 `metadata.manifest`도 현재 manifest와
  값과 type이 같아야 한다.
- `parse_status=partial`은 조인 또는 검색 대상 제외 사유가 아니다.

## 3. Phase 3 sidecar schema

```json
{
  "schema_version": "3.0.0",
  "content_schema_version": "2.0.0",
  "doc_id": "periodic_20250318000000",
  "rcept_no": "20250318000000",
  "metadata": {
    "authority": "manifest",
    "doc_id": "periodic_20250318000000",
    "corp_code": "00126380",
    "corp_name": "삼성전자",
    "listed_name": "삼성전자",
    "stock_code": "005930",
    "industry": "...",
    "sector": "...",
    "doc_group": "periodic",
    "doc_subtype": "annual",
    "report_nm": "사업보고서 (2024.12)",
    "is_correction": false,
    "rcept_no": "20250318000000",
    "rcept_dt": "20250318",
    "rcept_date": "2025-03-18",
    "flr_nm": "삼성전자",
    "base_year": 2024,
    "base_month": 12
  },
  "source_metadata": {
    "observations": []
  },
  "metadata_validation": {
    "status": "valid",
    "mismatch_count": 0,
    "warnings": [],
    "checks": {}
  },
  "parse_status": "success",
  "content_reference": {
    "dataset": "parsed_disclosures.jsonl",
    "join_key": "doc_id",
    "secondary_check": "rcept_no"
  }
}
```

### Canonical metadata fields

| JSON pointer | 의미 | JSON type | Nullable | Canonical source | Normalization / format | Unique | Filter |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/doc_id` | logical disclosure ID | string | 아니오 | manifest `doc_id` | `[a-z][a-z0-9]*_[0-9]{14}`; 숫자 casting 금지 | 전체 corpus unique | 안전 |
| `/rcept_no` | DART 접수번호 | string | 아니오 | manifest `rcept_no` | 14 digits를 문자열로 보존 | 전체 corpus unique | 안전 |
| `/metadata/authority` | canonical source 표지 | string | 아니오 | Phase 3 contract | 항상 `manifest` | 해당 없음 | 품질 확인용 |
| `/metadata/doc_id` | canonical logical ID | string | 아니오 | manifest `doc_id` | top-level과 동일 | 전체 corpus unique | 안전 |
| `/metadata/corp_code` | DART 기업 고유코드 | string | 아니오 | manifest `corp_code` | 8 digits; 선행 0 보존 | 회사별 반복 정상 | 가장 안전한 기업 identity filter |
| `/metadata/corp_name` | manifest canonical 회사명 및 physical folder join name | string | 아니오 | manifest `corp_name` | raw 보존; 비교 시에만 NFC | 회사별 반복 정상 | 안전; source 법적명과 다를 수 있음 |
| `/metadata/listed_name` | 상장/display 명칭 | string | 아니오 | manifest `listed_name` | raw 보존; `corp_name`과 합치지 않음 | 회사별 반복 정상 | 안전 |
| `/metadata/stock_code` | 상장 종목코드 | string | 아니오 | manifest `stock_code` | 6 digits; 선행 0 보존 | 회사별 반복 정상 | 안전 |
| `/metadata/industry` | manifest 산업 분류 문자열 | string | 아니오 | manifest `industry` | 추론·외부 보강 없음 | 반복 정상 | exact-match 가능; taxonomy 의미는 manifest 범위 |
| `/metadata/sector` | manifest sector 분류 문자열 | string | 아니오 | manifest `sector` | 추론·외부 보강 없음 | 반복 정상 | exact-match 가능; taxonomy 의미는 manifest 범위 |
| `/metadata/doc_group` | 공시 대분류 | string | 아니오 | manifest `doc_group` | `periodic`, `major`, `exchange`, `holding` | 반복 정상 | 안전 |
| `/metadata/doc_subtype` | 공시 하위 분류 | string 또는 null | 예 | manifest `doc_subtype` | 빈 값을 추론하지 않음 | 반복 정상 | null-aware 조건으로 안전 |
| `/metadata/report_nm` | manifest 보고서명 | string | 아니오 | manifest `report_nm` | source title로 덮어쓰지 않음 | 반복 가능 | exact-match 가능; 자유 형식 주의 |
| `/metadata/is_correction` | 정정공시 여부 | boolean | 아니오 | manifest `is_correction` | boolean 그대로 보존 | 반복 정상 | 안전; lineage filter가 아님 |
| `/metadata/rcept_no` | canonical 접수번호 | string | 아니오 | manifest `rcept_no` | top-level과 동일 | 전체 corpus unique | 안전 |
| `/metadata/rcept_dt` | canonical 접수일 원문 표현 | string | 아니오 | manifest `rcept_dt` | 유효한 `YYYYMMDD`; 접수번호에서 유도 금지 | 반복 가능 | 원본/검증용 |
| `/metadata/rcept_date` | 접수일 ISO 표현 | string 또는 null | 예(잘못된 날짜만) | manifest `rcept_dt`의 형식 변환 | strict `YYYYMMDD` → `YYYY-MM-DD`; 의미 추론 없음 | 반복 가능 | 안전한 날짜/범위 filter |
| `/metadata/flr_nm` | 공시 제출인명 | string | 아니오 | manifest `flr_nm` | issuer 회사명이라고 가정하지 않음 | 반복 가능 | filer exact-match만 |
| `/metadata/base_year` | periodic 기준연도 | integer 또는 null | 예 | manifest `base_year` | 값 생성 금지 | 반복 정상 | null-aware 조건부 안전 |
| `/metadata/base_month` | periodic 기준월 | integer 또는 null | 예 | manifest `base_month` | 1–12 또는 null; 값 생성 금지 | 반복 정상 | null-aware 조건부 안전 |

ID 필드 `doc_id`, `corp_code`, `stock_code`, `rcept_no`는 어떤 적재 계층에서도
숫자로 변환하지 않는다.

## 4. Date policy

Canonical 접수일은 오직 `manifest.rcept_dt`다.

```text
valid rcept_dt 20250318 -> rcept_date 2025-03-18
invalid rcept_dt        -> rcept_date null + metadata warning
```

`rcept_no` 앞 8자리와 `rcept_dt`가 달라도 `rcept_dt`를 보존한다. 접수번호에서
날짜를 유도하거나 이를 이용해 canonical 값을 수정하지 않는다.

## 5. Company-name policy

- `corp_name`과 `listed_name`은 서로 다른 필드이며 병합하지 않는다.
- physical path를 원 corpus와 연결할 때는 `corp_name`을 사용한다.
- UI는 필요에 따라 `listed_name`을 표시할 수 있으나 canonical identity를
  바꾸지는 않는다.
- 원문 `company_name`은 당시 법적명, 과거명, `주식회사` 표기 등으로 manifest와
  다를 수 있다.
- cross-time entity filter에는 `corp_code`가 가장 안정적이다.
- Unicode 비교용 normalized view는 NFC와 공백 정리만 적용하며 raw 값을
  덮어쓰지 않는다.

## 6. Source metadata contract

`/source_metadata/observations[]`는 Phase 2 parser가 문서에서 실제로 관찰한
metadata만 보존한다.

| Field | 의미 |
| --- | --- |
| `source_file` | 관찰값이 나온 physical relative path |
| `file_role` | `main`, `audit_report`, `consolidated_audit_report` 등 |
| `detected_format` | content-based parser format |
| `values.<source_field>.canonical_field` | 비교 대상 canonical field |
| `values.<source_field>.raw` | 원문에서 읽은 raw/decoded 값 |
| `values.<source_field>.normalized` | NFC+공백 comparison view |
| `values.<source_field>.provenance` | 원문 locator; 존재할 때만 값 |

관찰하지 못한 source field는 `match`로 간주하지 않는다. `not observed`는
`equal`이나 `missing in source`를 뜻하지 않는다.

## 7. Mismatch contract

`reports/metadata_mismatches.jsonl`의 각 행은 한 source occurrence다.

| Field | 의미 |
| --- | --- |
| `mismatch_id` | occurrence 정보를 포함한 deterministic identifier |
| `doc_id`, `rcept_no` | disclosure join keys |
| `canonical_field`, `source_field` | 비교 필드 |
| `canonical_raw`, `source_raw` | 덮어쓰지 않은 양쪽 raw 값 |
| `phase2_compared_source_value` | Phase 2 warning이 실제 비교에 사용한 source normalized field |
| `canonical_normalized`, `source_normalized` | comparison-only 값 |
| `normalized_equal` | 보수적 normalization 후 같은지 여부 |
| `reason_code` | 아래 taxonomy |
| `severity` | `info`, `warning`, `error` |
| `source_file`, `file_role`, `source_provenance` | 관찰 위치 |
| `phase2_warning_present` | Phase 2 baseline warning에서 재검증했는지 여부 |
| `evidence_checks`, `evidence_verified` | warning, component path/role, 접수번호, source value를 occurrence 단위로 재대조한 결과 |
| `origin` | mismatch 생성 경로 |
| `comparison_policy_version` | 분류 정책 version |

Reason taxonomy:

```text
DISPLAY_NAME_DIFFERENCE
UNICODE_NORMALIZATION_DIFFERENCE
WHITESPACE_DIFFERENCE
DATE_FORMAT_DIFFERENCE
REPORT_NAME_FORMAT_DIFFERENCE
SOURCE_VALUE_MISSING
MANIFEST_VALUE_MISSING
SOURCE_VALUE_DIFFERENT
PARSER_EXTRACTION_AMBIGUOUS
UNKNOWN_MISMATCH
```

분류할 근거가 부족하면 `UNKNOWN_MISMATCH`를 사용한다. mismatch를 숨기거나
canonical 값을 source 값으로 교체하지 않는다.

## 8. Validation and quality status

`/metadata_validation/status` vocabulary:

- `valid`: canonical field가 유효하고 metadata warning/mismatch가 없음
- `valid_with_warning`: canonical field는 사용할 수 있으나 정보성 또는 경고성
  차이가 있음
- `invalid`: required canonical 값 누락 또는 canonical 값의 형식/type 위반

Content 연결 오류는 canonical metadata status와 별도인 `checks` 및 실행
quality gate를 실패시킨다. 즉 연결 오류를 숨기지는 않지만, 유효한 manifest
metadata 자체를 `invalid`로 잘못 분류하지도 않는다.

`SOURCE_METADATA_MISMATCH`나 display-name 차이만으로 `invalid`가 되지 않는다.
Phase 2 parser warning은 content의 `/warnings` 및 component warning에 남고,
Phase 3 warning은 `/metadata_validation/warnings`에 별도로 남는다.

## 9. Content and provenance contract

본문 구조는 [Phase 2 schema](phase2_schema.md)를 따른다.

| Content field | 의미 |
| --- | --- |
| `main_document` | logical disclosure의 canonical physical representation |
| `main_document.content.sections` | 확인 가능한 section boundary |
| `main_document.content.block_sequence` | 원문 순서를 보존한 block |
| `main_document.content.tables` | raw cell/span 및 안전할 때만 normalized grid |
| `attachments` | 감사·연결감사 등 별도 역할 문서 |
| `alternative_representations` | PDF/HTML 중 non-canonical representation |
| `source_files` | 모든 physical path, role, detected format, SHA-256 |

인용이나 evidence 추적에는 해당 block/table의 provenance locator와
`source_files[].sha256`을 함께 사용한다. 편의용 `body_text`만으로 원문 증거를
대체하지 않는다.

Parse quality vocabulary는 `success`, `partial`, `failed`다. `partial`은 주로
markup recovery 같은 명시적 불확실성이고 metadata 부적합을 뜻하지 않는다.

## 10. Safe filter fields

| Field | 바로 사용 가능 | 조건 |
| --- | --- | --- |
| `doc_id` | 예 | string exact match |
| `rcept_no` | 예 | string exact match; 숫자 casting 금지 |
| `corp_code` | 예 | cross-time company identity에 권장 |
| `corp_name` | 예 | manifest canonical 값 exact/NFC-aware match |
| `listed_name` | 예 | 별도 display field로 match |
| `stock_code` | 예 | string exact match; 선행 0 보존 |
| `doc_group` | 예 | 4개 vocabulary exact match |
| `doc_subtype` | 조건부 | null-aware exact match |
| `rcept_date` | 예 | ISO date exact/range filter; null 처리 |
| `is_correction` | 예 | boolean filter; 원공시 관계를 의미하지 않음 |
| `base_year`, `base_month` | 조건부 | periodic 중심, null-aware |
| `industry`, `sector` | 조건부 | manifest taxonomy 문자열 exact match만 보장 |
| `parse_status` | 품질 필터 | `partial` 자동 제외 금지 |

## 11. Null semantics

`null`은 원문 또는 canonical source에서 확인할 수 없음을 뜻한다. 다음과 같은
값으로 임의 치환하면 안 된다.

```text
0
빈 문자열
없음
해당 없음
false
다른 공시에서 복사한 값
```

실제 값 `0`, `false`, `"없음"`이 source에 명시된 경우와 `null`은 서로 다르다.

## 12. Correction policy

Phase 3는 `is_correction`만 보존한다. `true`는 정정공시임을 뜻하지만 원공시가
연결되었다는 뜻은 아니다. 다음 필드는 Phase 4 이전에 생성하지 않는다.

```text
target_rcept_no
root_rcept_no
parent_rcept_no
correction_chain
correction_reason
```

## 13. Handoff sample contract

- 샘플은 deterministic coverage policy로 선택한다.
- doc group, parse status, 정정 여부, periodic 파일 수, section/table, mismatch,
  warning code, 연도, 기업 다양성과 희귀 사례를 함께 포함한다.
- sample content는 Phase 2 원본 JSONL line을 재직렬화하지 않고 byte 그대로
  복사한다.
- `sample_selection.jsonl`은 원본 byte offset, length, line SHA-256, 선정 이유와
  coverage를 기록한다.
- 샘플은 개발용이며 전체 corpus 통계의 대체물이 아니다.

## 14. Backward compatibility and excluded scope

Phase 3는 Phase 2 content schema `2.0.0`을 변경하지 않는 additive sidecar다.
기존 Phase 2 consumer는 그대로 동작할 수 있으며, Phase 3 consumer만 `doc_id`로
sidecar를 추가 조인한다.

다음은 이 계약의 범위 밖이다.

```text
correction lineage
PostgreSQL / pgvector
chunking / embedding
keyword or vector search
reranking
LLM inference or summarization
```

## 15. Phase 4 correction-lineage addendum

Phase 4 now provides `data/processed/correction_lineage.jsonl` for the 1,004
canonical `is_correction=true` records and `correction_chains.jsonl` for graph
components. These are additive: the Phase 2 content and Phase 3 metadata files
remain authoritative and unchanged.

Downstream systems may automatically use `target_rcept_no`, `root_rcept_no`,
`parent_rcept_no`, and `latest_rcept_no` only when the resolution status is
`linked_exact` or `linked_inferred`. `external_target` is also deterministic but
its target content is not available locally. `ambiguous` and `unresolved` must
not be silently converted into links.

`root_rcept_no=null` and `sequence=null` in a partial chain mean that known
correction-to-correction edges were preserved but the ultimate original filing
was not established. They do not mean that an original filing does not exist.

The resolver uses canonical Phase 3 company identity and classification plus
source-explicit correction dates/report names. Source evidence never overwrites
manifest metadata. It does not use receipt-number date inference, nearest prior
filing, semantic similarity, or LLM judgment. Full field definitions and graph
invariants are in [correction_schema.md](correction_schema.md).
