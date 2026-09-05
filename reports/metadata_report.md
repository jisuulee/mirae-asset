# DART Metadata Normalization, Validation & Data Contract 결과 보고서

- metadata schema version: 3.0.0
- Phase 2 content schema version: 2.0.0
- 시작 시각(UTC): 2026-08-27T00:27:54.620743+00:00
- 종료 시각(UTC): 2026-08-27T00:34:43.070768+00:00
- 실행 시간: 408.45초
- Python: 3.13.14
- canonical metadata source: manifest.jsonl

## 1. Corpus와 join 검증

manifest **4,204건**, Phase 2 content **4,204건**, Phase 3 metadata **4,204건**을 `doc_id`로 조인했다.

| join/check | 건수 |
| --- | --- |
| matched | 4,204 |
| manifest without content | 0 |
| content without manifest | 0 |
| doc_id duplicate | 0 |
| rcept_no duplicate | 0 |
| metadata/content link inconsistency | 0 |
| main content missing | 0 |

## 2. Metadata validation status

| status | 문서 수 |
| --- | --- |
| valid | 3,554 |
| valid_with_warning | 650 |
| invalid | 0 |

| Phase 2 parse status | 문서 수 |
| --- | --- |
| success | 2,928 |
| partial | 1,276 |
| failed | 0 |

Phase 2 `partial`은 metadata 제거 사유가 아니며 검증된 4,204건 모두 sidecar에 유지했다.

## 3. Field completeness와 형식

| field | non-null | null | empty | whitespace-only | unique | format 위반 | NFC 필요 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| doc_id | 4,204 | 0 | 0 | 0 | 4,204 | 0 | 0 |
| corp_code | 4,204 | 0 | 0 | 0 | 70 | 0 | 0 |
| corp_name | 4,204 | 0 | 0 | 0 | 70 | 0 | 0 |
| listed_name | 4,204 | 0 | 0 | 0 | 70 | 0 | 0 |
| stock_code | 4,204 | 0 | 0 | 0 | 70 | 0 | 0 |
| industry | 4,204 | 0 | 0 | 0 | 8 | 0 | 0 |
| sector | 4,204 | 0 | 0 | 0 | 20 | 0 | 0 |
| doc_group | 4,204 | 0 | 0 | 0 | 4 | 0 | 0 |
| doc_subtype | 3,606 | 598 | 0 | 0 | 8 | 0 | 0 |
| report_nm | 4,204 | 0 | 0 | 0 | 216 | 0 | 0 |
| is_correction | 4,204 | 0 | 0 | 0 | 2 | 0 | 0 |
| rcept_no | 4,204 | 0 | 0 | 0 | 4,204 | 0 | 0 |
| rcept_dt | 4,204 | 0 | 0 | 0 | 765 | 0 | 0 |
| flr_nm | 4,204 | 0 | 0 | 0 | 155 | 0 | 0 |
| base_year | 1,054 | 3,150 | 0 | 0 | 4 | 0 | 0 |
| base_month | 1,054 | 3,150 | 0 | 0 | 4 | 0 | 0 |
| rcept_date | 4,204 | 0 | 0 | 0 | 765 | 0 | 0 |

## 4. Uniqueness

| field | non-null | unique | duplicate occurrences | 해석 |
| --- | --- | --- | --- | --- |
| doc_id | 4,204 | 4,204 | 0 | 전체 corpus에서 unique여야 함 |
| rcept_no | 4,204 | 4,204 | 0 | 전체 corpus에서 unique여야 함 |
| corp_code | 4,204 | 70 | 4,134 | 회사별 반복이 정상 |
| stock_code | 4,204 | 70 | 4,134 | 회사별 반복이 정상 |

## 5. SOURCE_METADATA_MISMATCH 재검증

Phase 2 baseline **745회 / 643문서**를 Phase 3 taxonomy로 재분류했다.

| canonical field | 발생 수 |
| --- | --- |
| corp_name | 745 |
| listed_name | 0 |
| rcept_dt | 0 |
| report_nm | 0 |
| doc_group | 0 |
| doc_subtype | 0 |
| corp_code | 0 |
| stock_code | 0 |
| other | 0 |

| reason code | 발생 수 |
| --- | --- |
| DISPLAY_NAME_DIFFERENCE | 54 |
| UNICODE_NORMALIZATION_DIFFERENCE | 0 |
| WHITESPACE_DIFFERENCE | 0 |
| DATE_FORMAT_DIFFERENCE | 0 |
| REPORT_NAME_FORMAT_DIFFERENCE | 0 |
| SOURCE_VALUE_MISSING | 0 |
| MANIFEST_VALUE_MISSING | 0 |
| SOURCE_VALUE_DIFFERENT | 691 |
| PARSER_EXTRACTION_AMBIGUOUS | 0 |
| UNKNOWN_MISMATCH | 0 |

| severity | 발생 수 |
| --- | --- |
| info | 54 |
| warning | 691 |

고유 canonical 회사는 **27개**, 고유 source 표기는 **76개**다. 여러 canonical 회사로 매핑된 source 표기는 **0개**다.

### 대표 mismatch 사례

| doc_id | field | canonical | source | reason | example category | pair count | source file |
| --- | --- | --- | --- | --- | --- | --- | --- |
| holding_20250702000176 | corp_name | NC | (주)엔씨소프트 | DISPLAY_NAME_DIFFERENCE | reason:DISPLAY_NAME_DIFFERENCE | 35 | raw/holding/NC/20250702000176/20250702000176.xml |
| major_20230703000242 | corp_name | 신한지주 | (주)신한금융지주회사 | SOURCE_VALUE_DIFFERENT | reason:SOURCE_VALUE_DIFFERENT, field:corp_name | 47 | raw/major/신한지주/20230703000242/20230703000242.xml |
| periodic_20230515002373 | corp_name | 신한지주 | (주)신한금융지주회사 | SOURCE_VALUE_DIFFERENT | top_frequency | 47 | raw/periodic/신한지주/20230515002373_quarter_2023_03/20230515002373.xml |
| periodic_20230515001451 | corp_name | 한국항공우주 | 한국항공우주산업(주) | SOURCE_VALUE_DIFFERENT | top_frequency | 43 | raw/periodic/한국항공우주/20230515001451_quarter_2023_03/20230515001451.xml |
| periodic_20230512001100 | corp_name | NAVER | 네이버(주) | SOURCE_VALUE_DIFFERENT | top_frequency | 35 | raw/periodic/NAVER/20230512001100_quarter_2023_03/20230512001100.xml |
| periodic_20230515002067 | corp_name | NC | (주)엔씨소프트 | DISPLAY_NAME_DIFFERENCE | top_frequency | 35 | raw/periodic/NC/20230515002067_quarter_2023_03/20230515002067.xml |
| periodic_20230515002761 | corp_name | 삼성생명 | 삼성생명보험주식회사 | SOURCE_VALUE_DIFFERENT | top_frequency | 32 | raw/periodic/삼성생명/20230515002761_quarter_2023_03/20230515002761.xml |
| periodic_20230515002902 | corp_name | 에스엠 | 주식회사 에스엠엔터테인먼트 | SOURCE_VALUE_DIFFERENT | top_frequency | 30 | raw/periodic/에스엠/20230515002902_quarter_2023_03/20230515002902.xml |
| periodic_20240314001585 | corp_name | KB금융 | KB금융지주 | SOURCE_VALUE_DIFFERENT | top_frequency | 29 | raw/periodic/KB금융/20240314001585_annual_2023_12/20240314001585.xml |
| periodic_20230515002541 | corp_name | KB금융 | 주식회사 KB금융지주 | SOURCE_VALUE_DIFFERENT | top_frequency | 26 | raw/periodic/KB금융/20230515002541_quarter_2023_03/20230515002541.xml |
| periodic_20230515000906 | corp_name | 한전기술 | 한국전력기술 | SOURCE_VALUE_DIFFERENT | top_frequency | 20 | raw/periodic/한전기술/20230515000906_quarter_2023_03/20230515000906.xml |
| periodic_20230515002433 | corp_name | HD현대중공업 | 에이치디현대중공업(주) | SOURCE_VALUE_DIFFERENT | top_frequency | 19 | raw/periodic/HD현대중공업/20230515002433_quarter_2023_03/20230515002433.xml |
| periodic_20241113000353 | corp_name | HMM | 에이치엠엠(주) | SOURCE_VALUE_DIFFERENT | top_frequency | 19 | raw/periodic/HMM/20241113000353_quarter_2024_09/20241113000353.xml |
| periodic_20230814002680 | corp_name | 삼성SDI | 삼성에스디아이(주) | SOURCE_VALUE_DIFFERENT | top_frequency | 18 | raw/periodic/삼성SDI/20230814002680_half_2023_06/20230814002680.xml |
| periodic_20260323001211 | corp_name | LIG디펜스앤에어로스페이스 | 엘아이지넥스원(주) | SOURCE_VALUE_DIFFERENT | top_frequency | 18 | raw/periodic/LIG디펜스앤에어로스페이스/20260323001211_annual_2025_12/20260323001211_00760.xml |
| periodic_20230811001538 | corp_name | JYP Ent | (주)제이와이피엔터테인먼트 | SOURCE_VALUE_DIFFERENT | top_frequency | 18 | raw/periodic/JYP Ent/20230811001538_half_2023_06/20230811001538.xml |
| major_20240808000377 | corp_name | 에스엠 | (주)에스엠엔터테인먼트 | SOURCE_VALUE_DIFFERENT | top_frequency | 18 | raw/major/에스엠/20240808000377/20240808000377.xml |
| periodic_20230814002141 | corp_name | POSCO홀딩스 | 포스코홀딩스 | SOURCE_VALUE_DIFFERENT | top_frequency | 17 | raw/periodic/POSCO홀딩스/20230814002141_half_2023_06/20230814002141.xml |
| periodic_20230515002177 | corp_name | LG씨엔에스 | 엘지씨엔에스 | SOURCE_VALUE_DIFFERENT | top_frequency | 15 | raw/periodic/LG씨엔에스/20230515002177_quarter_2023_03/20230515002177.xml |
| periodic_20230515000336 | corp_name | LIG디펜스앤에어로스페이스 | 엘아이지넥스원 주식회사 | SOURCE_VALUE_DIFFERENT | top_frequency | 15 | raw/periodic/LIG디펜스앤에어로스페이스/20230515000336_quarter_2023_03/20230515000336.xml |

## 6. Canonicalization 규칙

- authority: `manifest.jsonl`
- 원문 metadata는 observation/mismatch 증거이며 canonical metadata를 덮어쓰지 않는다.
- `rcept_date`는 manifest `rcept_dt`가 strict YYYYMMDD일 때만 YYYY-MM-DD로 표시한다.
- `rcept_no`에서 날짜를 유도하지 않는다.
- rcept_no prefix와 rcept_dt가 다른 문서: **8건**
- corp_name과 listed_name이 다른 문서: **327건**
- `is_correction`만 보존하며 correction lineage는 생성하지 않는다.

## 7. Safe filter fields

| field | type | nullable | source | filter | 조건/주의 |
| --- | --- | --- | --- | --- | --- |
| doc_id | string | 아니오 | manifest.jsonl | 예 | exact match |
| rcept_no | string | 아니오 | manifest.jsonl | 예 | 문자열 exact match; 숫자 casting 금지 |
| corp_code | string | 아니오 | manifest.jsonl | 예 | 문자열 exact match; 숫자 casting 금지 |
| corp_name | string | 아니오 | manifest.jsonl | 예 | 서로 합치지 말고 field별 exact/NFC-aware match |
| listed_name | string | 아니오 | manifest.jsonl | 예 | 서로 합치지 말고 field별 exact/NFC-aware match |
| stock_code | string | 아니오 | manifest.jsonl | 예 | 문자열 exact match; 숫자 casting 금지 |
| doc_group | string | 아니오 | manifest.jsonl | 예 | exact match |
| doc_subtype | string | 예 | manifest.jsonl | 예 | null-aware exact match; null은 미분류이며 추론 금지 |
| rcept_date | string | 아니오 | strict format of manifest.rcept_dt | 예 | strict rcept_dt 형식 변환값; 날짜 범위 filter 가능 |
| is_correction | boolean | 아니오 | manifest.jsonl | 예 | boolean filter만 가능; 원공시 lineage 의미 없음 |

## 8. 역할 2 handoff sample

- sample documents: **80건**
- selection policy: `phase3-handoff-sample-v1`
- 원본 line byte 보존: **예**
- quota validation: **예**

| doc_group | sample 수 |
| --- | --- |
| exchange | 24 |
| holding | 18 |
| major | 18 |
| periodic | 20 |

| parse status | sample 수 |
| --- | --- |
| partial | 26 |
| success | 54 |

## 9. 잘못된 metadata 연결 의심

_해당 항목 없음_

## 10. Quality gates

| gate | 통과 |
| --- | --- |
| manifest_count_matches_expected | 예 |
| manifest_has_no_malformed_rows | 예 |
| manifest_file_paths_unique | 예 |
| manifest_doc_ids_unique | 예 |
| manifest_rcept_nos_unique | 예 |
| parsed_count_matches_manifest | 예 |
| metadata_count_matches_manifest | 예 |
| join_is_one_to_one | 예 |
| no_manifest_without_content | 예 |
| no_content_without_manifest | 예 |
| no_malformed_content_rows | 예 |
| doc_id_unique | 예 |
| rcept_no_unique | 예 |
| no_link_inconsistency | 예 |
| no_invalid_metadata | 예 |
| phase2_warning_sidecar_has_no_malformed_rows | 예 |
| phase2_mismatch_baseline_reconciled | 예 |
| all_mismatch_documents_joined | 예 |
| all_mismatch_evidence_verified | 예 |
| mismatch_warning_multiset_exact | 예 |
| sample_quota_validation_passed | 예 |
| sample_is_full_dataset_subset | 예 |
| sample_source_lines_copied_verbatim | 예 |
| content_dataset_not_rewritten | 예 |
| correction_lineage_not_generated | 예 |

## 11. Downstream 주의사항

- `null`은 확인할 수 없음이며 0, 없음, 해당 없음과 같지 않다.
- `corp_name`은 manifest canonical 회사명, `listed_name`은 별도 표시명이다.
- 과거 공시 원문의 법적명·과거명과 현재 manifest canonical 회사명이 다를 수 있다.
- `corp_code`, `stock_code`, `rcept_no`는 숫자로 변환하지 않는다.
- `doc_subtype`, `base_year`, `base_month` filter는 null-aware하게 구현한다.
- `partial` 문서를 자동 제외하지 말고 parse warning을 별도 품질 신호로 사용한다.
- Phase 3는 chunking, DB, embedding, correction lineage를 포함하지 않는다.
