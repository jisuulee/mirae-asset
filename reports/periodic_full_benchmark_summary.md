# Periodic full-corpus Retrieval benchmark summary

- Version: `periodic-full-v1.0`
- Corpus: `periodic-full-v1:receipts=1054:sections=82862:tables=1401752`
- Questions: 120 (regression 36, new 84)
- Companies: 66; gold years 2023–2026
- Source: XML 115, PDF page 5; table-related category 10
- Stable provenance DB validation: 153 section entries and 11 table entries checked, 0 mismatches

## Review gate

Only the inherited 36-question regression split is reviewed. The 74 `needs_review` and 10 `ambiguous` rows are benchmark candidates, not final official gold. Consequently the 120-row scores below are provisional; reviewed-only regression scores remain authoritative until independent review is complete.

## Distributions

### category

| Value | Count |
|---|---:|
| ambiguous_similar_sections | 10 |
| company_concept | 15 |
| company_section_title | 15 |
| difficult_or_ambiguous | 5 |
| exact_phrase | 10 |
| narrative_description | 20 |
| partial_korean_phrase | 10 |
| pdf_fallback | 5 |
| spacing_or_expression_variation | 10 |
| table_related | 10 |
| year_report_filtered | 10 |

### difficulty

| Value | Count |
|---|---:|
| easy | 25 |
| hard | 20 |
| medium | 75 |

### retrieval_target

| Value | Count |
|---|---:|
| either | 3 |
| pdf_page | 5 |
| section | 105 |
| table | 7 |

### review_status

| Value | Count |
|---|---:|
| ambiguous | 10 |
| needs_review | 74 |
| reviewed | 36 |

### split

| Value | Count |
|---|---:|
| development | 56 |
| held_out | 28 |
| regression | 36 |

### source_type

| Value | Count |
|---|---:|
| pdf | 5 |
| xml | 115 |

### gold_company

| Value | Count |
|---|---:|
| CJ제일제당 | 2 |
| HD현대일렉트릭 | 1 |
| HD현대중공업 | 1 |
| HMM | 1 |
| JYP Ent | 1 |
| KB금융 | 5 |
| LG생활건강 | 1 |
| LG씨엔에스 | 1 |
| LG에너지솔루션 | 1 |
| LG유플러스 | 1 |
| LG이노텍 | 2 |
| LIG디펜스앤에어로스페이스 | 1 |
| NAVER | 1 |
| NC | 1 |
| OCI홀딩스 | 1 |
| POSCO홀딩스 | 3 |
| SK텔레콤 | 1 |
| SK하이닉스 | 2 |
| 고려아연 | 1 |
| 기아 | 1 |
| 대우건설 | 2 |
| 두산로보틱스 | 1 |
| 두산에너빌리티 | 2 |
| 두산퓨얼셀 | 1 |
| 디앤디파마텍 | 1 |
| 레인보우로보틱스 | 1 |
| 메리츠금융지주 | 1 |
| 미래에셋증권 | 1 |
| 삼성E&A | 1 |
| 삼성SDI | 1 |
| 삼성바이오로직스 | 1 |
| 삼성생명 | 2 |
| 삼성전기 | 1 |
| 삼성전자 | 29 |
| 삼성중공업 | 1 |
| 삼성화재해상보험 | 1 |
| 세아베스틸지주 | 2 |
| 셀트리온 | 1 |
| 시프트업 | 2 |
| 신한지주 | 1 |
| 아모레퍼시픽 | 1 |
| 알테오젠 | 1 |
| 에스엠 | 2 |
| 에코프로비엠 | 1 |
| 엘에스일렉트릭 | 1 |
| 와이지엔터테인먼트 | 1 |
| 우리금융지주 | 1 |
| 이마트 | 1 |
| 카카오 | 2 |
| 케이티 | 2 |
| 하나금융지주 | 1 |
| 하이브 | 1 |
| 한국항공우주 | 1 |
| 한미반도체 | 1 |
| 한미약품 | 1 |
| 한전기술 | 1 |
| 한화솔루션 | 3 |
| 한화에어로스페이스 | 3 |
| 한화오션 | 4 |
| 현대건설 | 1 |
| 현대로템 | 1 |
| 현대모비스 | 2 |
| 현대오토에버 | 1 |
| 현대자동차 | 1 |
| 현대제철 | 1 |
| 효성중공업 | 2 |

### gold_year

| Value | Count |
|---|---:|
| 2023 | 12 |
| 2024 | 64 |
| 2025 | 32 |
| 2026 | 18 |

### gold_report_type

| Value | Count |
|---|---:|
| annual | 66 |
| half | 14 |
| quarter | 44 |
