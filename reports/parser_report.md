# DART Evidence-Preserving Unified Parser 결과 보고서

- schema version: 2.0.0
- 시작 시각(UTC): 2026-08-26T15:51:24.897913+00:00
- 종료 시각(UTC): 2026-08-26T17:26:51.557523+00:00
- 실행 시간: 5,726.66초
- Python: 3.13.14
- 입력: zip / C:\Users\USER\Downloads\aifestival_project\3.공시.zip
- 논리 루트: 3.공시/corpus

## 1. 실행 범위와 논리 공시 수

manifest 논리 공시는 **4,204건**, 이번 실행 대상은 **4,204건**, JSONL 출력은 **4,204줄**이다.
설정된 요구 기준은 **4,204건**이며 manifest 일치 여부는 **예**다.
전체 코퍼스 실행이며 출력 줄 수와 manifest 문서 수의 일치 여부는 **예**다.

### 논리 공시 parse status

| status | 문서 수 |
| --- | --- |
| success | 2,928 |
| partial | 1,276 |

### doc_group별 결과

| doc_group | 합계 | success | partial |
| --- | --- | --- | --- |
| exchange | 1,469 | 1,363 | 106 |
| holding | 1,083 | 1,013 | 70 |
| periodic | 1,054 | 41 | 1,013 |
| major | 598 | 511 | 87 |

## 2. 물리 파일·형식·역할

논리 공시를 구성한 물리 파일은 **4,622개**, 첨부는 **415개**다.
SHA-256 기록은 **4,622/4,622개**이며 누락은 **0개**다.

### content detection 형식별 상태

| detected format | 파일 수 | success | partial |
| --- | --- | --- | --- |
| xml | 3,147 | 1,791 | 1,356 |
| html | 1,472 | 1,363 | 109 |
| pdf | 3 | 3 | 0 |

### 파일 역할

| role | 파일 수 |
| --- | --- |
| main | 4,201 |
| audit_report | 208 |
| consolidated_audit_report | 207 |
| primary_pdf | 3 |
| viewer_html | 3 |

## 3. title/body/section/table 추출

| 항목 | 개수 |
| --- | --- |
| main title 추출 문서 | 4,202 |
| main title 추출 성공률 | 99.95% |
| main title 미추출 문서 | 2 |
| main body 추출 문서 | 4,204 |
| main non-empty body 비율 | 100.00% |
| main empty body | 0 |
| main section 보유 문서 | 2,732 |
| main section 미추출 문서 | 1,472 |
| main section 수 | 111,261 |
| main table 보유 문서 | 4,202 |
| main table 수 | 1,440,210 |
| 전체 물리 block 수 | 2,402,213 |
| 전체 물리 section 수 | 115,030 |
| 전체 물리 table 수 | 1,551,111 |
| provenance 대상 수 | 41,660,802 |
| provenance 누락 수 | 0 |

## 4. warning codes와 소스 이상

구조화 경고는 총 **8,041건**이다.

| warning code | 발생 수 |
| --- | --- |
| SECTION_HIERARCHY_UNCERTAIN | 2,723 |
| BARE_AMPERSAND_PRESERVED | 1,620 |
| ENCODING_DECLARATION_MISMATCH | 1,469 |
| MARKUP_RECOVERY_APPLIED | 1,465 |
| SOURCE_METADATA_MISMATCH | 745 |
| TABLE_SPAN_NORMALIZATION_FAILED | 12 |
| TITLE_NOT_FOUND | 3 |
| HTML_VIEWER_NOT_SUBSTANTIVE | 2 |
| PDF_FALLBACK_USED | 2 |

### warning severity

| severity | 발생 수 |
| --- | --- |
| warning | 6,421 |
| info | 1,620 |

### PDF fallback·인코딩·source anomaly

| 신호 | 건수 |
| --- | --- |
| PDF fallback | 2 |
| 인코딩 선언 불일치 | 1,469 |
| source metadata 불일치 | 745 |
| source 탐색/읽기 문제 | 0 |
| manifest 미연결 raw | 0 |
| SHA-256 누락 | 0 |

### source issue 예시

_해당 항목 없음_

## 5. Auxiliary JSON 제외 검증

기업별 수집 목록 JSON은 **280개**이며 검증된 record 합계는 **22,980개**다.
이 JSON을 논리 disclosure로 포함했는가? **아니오**.
auxiliary JSON은 형식·레코드 검증 대상일 뿐 최종 공시 JSONL에는 포함되지 않는다.

| auxiliary status | 파일 수 |
| --- | --- |
| success | 280 |

### auxiliary root type

| root type | 파일 수 |
| --- | --- |
| array | 280 |

### partial/failed 원인 신호 분류

아래 값은 warning 발생 횟수의 진단 분류이며 문서 수가 아니고 서로 배타적이지 않다. 특히 `MARKUP_RECOVERY_APPLIED`는 원본 XML/HTML 문법 이상을 tolerant parser가 복구한 source-data 신호이고, `SECTION_HIERARCHY_UNCERTAIN`은 근거 없는 계층 추론을 하지 않은 parser 제한 신호다.

| 원인 범주 | warning 발생 수 |
| --- | --- |
| source syntax/표기 이상 | 5,299 |
| 보수적 parser 한계/불확실성 | 2,738 |
| 대체표현·role 선택 | 4 |
| 물리 source/linkage 실패 | 0 |

## 6. suspicious document 예시

### title 미추출

| doc_id | rcept_no | doc_group | report_nm |
| --- | --- | --- | --- |
| periodic_20260619000667 | 20260619000667 | periodic | [기재정정]사업보고서 (2025.12) |
| periodic_20240514001522 | 20240514001522 | periodic | [기재정정]분기보고서 (2024.03) |

### section 미추출

| doc_id | rcept_no | doc_group | report_nm |
| --- | --- | --- | --- |
| exchange_20250728800035 | 20250728800035 | exchange | 단일판매ㆍ공급계약체결 |
| exchange_20250731800028 | 20250731800028 | exchange | [기재정정]단일판매ㆍ공급계약체결 |
| exchange_20240424800596 | 20240424800596 | exchange | 신규시설투자등 |
| exchange_20240726800615 | 20240726800615 | exchange | 신규시설투자등 |
| exchange_20241220800005 | 20241220800005 | exchange | 투자판단관련주요경영사항 |
| exchange_20260225801974 | 20260225801974 | exchange | 신규시설투자등 |
| exchange_20230530800118 | 20230530800118 | exchange | 단일판매ㆍ공급계약체결(자율공시) |
| exchange_20230612800469 | 20230612800469 | exchange | 단일판매ㆍ공급계약체결(자율공시) |
| exchange_20230612800470 | 20230612800470 | exchange | 단일판매ㆍ공급계약체결(자율공시) |
| exchange_20230622800158 | 20230622800158 | exchange | 단일판매ㆍ공급계약체결(자율공시) |

### partial

| doc_id | rcept_no | doc_group | report_nm |
| --- | --- | --- | --- |
| periodic_20230515002335 | 20230515002335 | periodic | 분기보고서 (2023.03) |
| periodic_20230814002534 | 20230814002534 | periodic | 반기보고서 (2023.06) |
| periodic_20231114002109 | 20231114002109 | periodic | 분기보고서 (2023.09) |
| periodic_20240312000736 | 20240312000736 | periodic | 사업보고서 (2023.12) |
| periodic_20240516001421 | 20240516001421 | periodic | 분기보고서 (2024.03) |
| periodic_20240814003284 | 20240814003284 | periodic | 반기보고서 (2024.06) |
| periodic_20241114002642 | 20241114002642 | periodic | 분기보고서 (2024.09) |
| periodic_20250311001085 | 20250311001085 | periodic | 사업보고서 (2024.12) |
| periodic_20250515001922 | 20250515001922 | periodic | 분기보고서 (2025.03) |
| periodic_20250814003156 | 20250814003156 | periodic | 반기보고서 (2025.06) |

### malformed table/span

| doc_id | rcept_no | doc_group | report_nm |
| --- | --- | --- | --- |
| periodic_20250814003918 | 20250814003918 | periodic | 반기보고서 (2025.06) |
| periodic_20250814004489 | 20250814004489 | periodic | [기재정정]반기보고서 (2025.06) |
| periodic_20251114002661 | 20251114002661 | periodic | 분기보고서 (2025.09) |
| periodic_20260316001292 | 20260316001292 | periodic | 사업보고서 (2025.12) |
| periodic_20250318000645 | 20250318000645 | periodic | 사업보고서 (2024.12) |
| periodic_20250515001302 | 20250515001302 | periodic | 분기보고서 (2025.03) |
| periodic_20250814002281 | 20250814002281 | periodic | 반기보고서 (2025.06) |
| periodic_20251114001436 | 20251114001436 | periodic | 분기보고서 (2025.09) |

### source metadata mismatch

| doc_id | rcept_no | doc_group | report_nm |
| --- | --- | --- | --- |
| periodic_20240319000684 | 20240319000684 | periodic | 사업보고서 (2023.12) |
| periodic_20250319000665 | 20250319000665 | periodic | 사업보고서 (2024.12) |
| periodic_20260317000635 | 20260317000635 | periodic | 사업보고서 (2025.12) |
| holding_20230307000265 | 20230307000265 | holding | 주식등의대량보유상황보고서(약식) |
| holding_20230607000335 | 20230607000335 | holding | 주식등의대량보유상황보고서(약식) |
| holding_20230807000139 | 20230807000139 | holding | 주식등의대량보유상황보고서(약식) |
| holding_20240527000026 | 20240527000026 | holding | 주식등의대량보유상황보고서(약식) |
| holding_20240708000018 | 20240708000018 | holding | 주식등의대량보유상황보고서(약식) |
| holding_20240805000236 | 20240805000236 | holding | 주식등의대량보유상황보고서(약식) |
| holding_20240925000388 | 20240925000388 | holding | 주식등의대량보유상황보고서(약식) |

## 7. 대표 파싱 예시

실행 통계가 보존한 대표 예시 **5건**을 표시한다. 없는 유형의 예시는 임의로 생성하지 않았다.

| kind | doc_id | 회사 | report_nm | format/role | sections | tables | source path | 원문 preview |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| periodic | periodic_20230515002335 | 삼성전자 | 분기보고서 (2023.03) | xml/main | 55 | 580 | raw/periodic/삼성전자/20230515002335_quarter_2023_03/20230515002335.xml | 분 기 보 고 서 (제 55 기)  목 차   【 대표이사 등의 확인 】 대표이사 확인서.jpg 대표이사 확인서  I. 회사의 개요 1. 회사의 개요 회사의 개요는 기업공시서식 작성기준에 따라 분기보고서에 기재하지 않습니다.(반기ㆍ사업보고서에 기재 예정) 2. 회사의 연혁 회사의 연혁은 기업공시서식 작성기준에 따라 |
| periodic_multi_file | periodic_20240312000736 | 삼성전자 | 사업보고서 (2023.12) | xml/main | 128 | 1,780 | raw/periodic/삼성전자/20240312000736_annual_2023_12/20240312000736.xml | 사 업 보 고 서 (제 55 기)  목 차  【 대표이사 등의 확인 】 제55기_대표이사 등의 확인서.jpg 제55기_대표이사 등의 확인서  I. 회사의 개요 1. 회사의 개요 가. 회사의 법적ㆍ상업적 명칭 당사의 명칭은 삼성전자주식회사이고 영문명은 Samsung Electronics Co., Ltd. 입니다. 나. |
| major | major_20241031000508 | 삼성전자 | 주요사항보고서(해외증권시장주권등상장폐지결정) | xml/main | 2 | 3 | raw/major/삼성전자/20241031000508/20241031000508.xml | 주요사항보고서 / 거래소 신고의무 사항  해외 증권시장 주권등 상장폐지 결정 7. 기타 투자판단에 참고할 사항 - 상기 1항의 상장폐지되는 주식의 종류는 당사 우선주를 기초로 한 주식예탁증서(Depositary Receipts, DR)입니다. (당사 원주 : DR 비율 = 25:1) - 상기 1항의 상장폐지주식 수 38 |
| exchange | exchange_20250728800035 | 삼성전자 | 단일판매ㆍ공급계약체결 | html/main | 0 | 1 | raw/exchange/삼성전자/20250728800035/20250728800035.xml | 단일판매ㆍ공급계약 체결 |
| holding | holding_20230120000563 | 삼성전자 | 주식등의대량보유상황보고서(일반) | xml/main | 26 | 33 | raw/holding/삼성전자/20230120000563/20230120000563.xml | 주식등의 대량보유상황보고서  제1부 보고의 개요 1. 발행회사에 관한 사항 2. 대량보유자에 관한 사항 가. 보고자 (1) 보고자 개요 (2) 보고자에 대한 구체적인 사항(법인, 기타단체, 법령상조합, 민법상 조합의 경우 기재) (단위: 백만원) ※ 자산총액, 부채총액, 자본총액, 자본금은 2021년말 연결 재무제표 기 |

## 8. Phase 3 전 확인할 이슈

| 확인 항목 | 영향 건수 |
| --- | --- |
| partial 공시 warning 검토 | 1,276 |
| title 미추출 검토 | 2 |
| section 미추출 검토 | 1,472 |
| table span/malformed table 검토 | 12 |
| source metadata mismatch 검토 | 745 |
| PDF fallback의 표 행·열 구조 미보존 검토 | 2 |

정정공시 계보, RAG chunking, embedding과 의미 추론은 이 parser 단계에서 수행하지 않았으며 다음 단계에서 별도 정책과 평가가 필요하다.
PDF fallback은 OCR 없이 page text와 page locator만 보존한다. 따라서 해당 PDF의 표는 행·열/cell 구조로 역추적할 수 없으며, Phase 3 전에 viewer 대체 또는 별도 PDF table extractor 정책을 검토해야 한다.

## 9. 근거 보존 보증과 명시적 답변

| 보증 항목 | 충족 |
| --- | --- |
| manifest_row_is_logical_unit | 예 |
| group_listing_json_excluded | 예 |
| dispatch_is_content_based | 예 |
| strict_utf8_first | 예 |
| silent_decode_ignore_or_replace | 아니오 |
| semantic_inference_or_summarization | 아니오 |
| source_sha256_recorded | 예 |
| block_table_provenance_recorded | 예 |
| structured_content_provenance_recorded | 예 |
| body_text_derived_from_ordered_blocks | 예 |
| body_text_char_level_reverse_mapping | 아니오 |
| raw_source_mutated | 아니오 |

1. **소스에 없는 정보가 생성될 가능성이 있는가?** **아니오.** parser는 원문 추출과 NFC·공백 등 보수적 정규화만 수행하며, 원문에 없는 정보를 생성하지 않고 소스에 없는 사실의 추론·요약·보강을 수행하지 않는다.

2. **추출된 숫자·표·문장을 물리 소스까지 역추적할 수 있는가?** **구조화 항목은 예.** title/section/block/list item/table/cell마다 물리 경로와 파일 역할, DOM-like/source index 또는 PDF page locator가 있고 source 파일의 SHA-256 범위는 4,622/4,622개다. 다만 `body_text`는 ordered block에서 파생한 검색용 뷰이므로 개별 문자 범위(char offset) 역매핑은 제공하지 않는다. 문장과 숫자는 원래 block 또는 table cell 단위로 추적해야 한다.
