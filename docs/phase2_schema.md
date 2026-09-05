# Phase 2 JSONL schema

`parsed_disclosures.jsonl`의 각 줄은 manifest 한 행과 1:1로 대응하는 logical disclosure다. 구현 기준 schema version은 `2.0.0`이다.

## Top level

| 필드 | 의미 |
| --- | --- |
| `doc_id`, `rcept_no` | 문자열 식별자. 숫자로 변환하지 않음 |
| `metadata.authority` | 항상 `manifest` |
| `metadata.manifest` | 권위값인 manifest record 원본 |
| `main_document` | canonical physical representation의 `ParseResult`, 없으면 `null` |
| `attachments` | 감사·연결감사 등 별도 역할의 `ParseResult` 배열 |
| `alternative_representations` | PDF+HTML 중 비정본 표현의 축약 record. content는 포함하지 않음 |
| `source_files` | logical disclosure에 연결된 모든 physical source |
| `parse_status` | `success`, `partial`, `failed` |
| `warnings` | logical assembly warning 배열 |

호환성과 필터 편의를 위해 주요 manifest 필드(`corp_code`, `corp_name`, `doc_group`, `report_nm`, `rcept_dt` 등)도 top level에 그대로 노출한다. 권위 원본은 `metadata.manifest`다.

## ParseResult

```json
{
  "source_file": {},
  "parse_status": "success",
  "content": {},
  "warnings": [],
  "parser_name": "lxml-xml",
  "source_metadata": {}
}
```

`source_metadata`는 문서에서 직접 읽은 값을 manifest와 분리한다. markup metadata는 normalized 값 외에 `<field>_raw`와 `<field>_provenance`를 함께 가진다. 이 값이 manifest와 다르더라도 manifest를 덮어쓰지 않는다.

## DocumentContent

| 필드 | 의미 |
| --- | --- |
| `title`, `raw_title` | 보수적 view와 decoded source-derived title. 명시 title이 없으면 `null` |
| `body_text` | table을 중복 삽입하지 않고 ordered blocks에서 파생한 검색용 text view |
| `block_sequence` | heading/paragraph/list/table/page break의 source order |
| `sections` | 보수적으로 확인된 section boundary와 block ID 범위 |
| `tables` | raw cell structure, span, normalized grid, provenance |
| `title_provenance`, `provenance` | title/root source locator |

`body_text`는 원문을 대체하는 evidence object가 아니다. 개별 문장은 `block_sequence`, 표 값은 `tables[].raw_rows`에서 인용해야 한다.

## Table

`raw_rows`는 원본 cell 경계와 빈 cell을 보존한다. 각 `TableCell`은 `raw_text`, 보수적으로 whitespace/NFC만 정리한 `text`, `rowspan`, `colspan`, `is_header`, source row/cell index, provenance를 가진다.

`normalized_rows`는 span expansion이 안전하게 성공한 경우의 편의 view다. continuation cell은 기본적으로 `null`이며 값을 임의 복제하지 않는다. cell 또는 table 전체 expansion 안전 한도를 넘으면 `normalized_rows=null`과 `TABLE_SPAN_NORMALIZATION_FAILED` warning을 남기고 `raw_rows`는 그대로 유지한다.

## Provenance

```json
{
  "source_file": "raw/periodic/.../document.xml",
  "file_role": "main",
  "source_index": 42,
  "dom_path": "/DOCUMENT/BODY/SECTION-1/P[2]",
  "tag": "p",
  "page_number": null,
  "char_start": null,
  "char_end": null,
  "attributes": {}
}
```

XML/HTML은 DOM-like path와 source order index를, PDF는 page number를 중심으로 사용한다. `source_files[].sha256`은 parser가 읽은 physical bytes의 식별자다. `raw_*`는 decoder/DOM을 거친 문자열이므로 byte-for-byte 동일성 주장이 아니다.

## PDF+HTML 및 attachments

- substantive viewer HTML이 있으면 `main_document`는 `viewer_html`이다.
- viewer HTML이 실행 shell뿐이면 `primary_pdf`를 canonical로 하고 `PDF_FALLBACK_USED`를 기록한다.
- 비정본 표현은 `alternative_representations`에 path/status/source metadata만 남기며 `content_included=false`다.
- 감사보고서와 연결감사보고서는 각각 `audit_report`, `consolidated_audit_report` attachment다.
- alternative representation은 attachment가 아니며 logical parse status를 낮추지 않는다.

## Status와 불확실성

- `success`: canonical content가 정상 추출되고 필수 구조 오류가 없음
- `partial`: source syntax recovery, table span 실패, 일부 page/attachment 실패 등 일부 불확실성이 명시됨
- `failed`: substantive canonical main을 만들 수 없음

누락값은 생성하지 않는다. title/section level은 확인할 수 없으면 `null`이고, decoding/role/recovery 문제는 code가 있는 warning으로 기록한다. correction lineage는 이 schema에 포함하지 않으며 후속 Phase에서 별도로 구성한다.
