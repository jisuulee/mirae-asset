# Retrieval Architecture V1

## Frozen default — 2026-08-31

Retrieval V1의 자연어 RAG 경로는 다음으로 고정한다.

```text
Question
→ shared metadata routing/filter
→ FTS20 + Exact Vector20 + Title/Path20
→ unweighted RRF (k=60)
→ Top10
→ deterministic reranker
→ evidence with stable provenance
```

- FTS는 기존 body/title/path keyword ranking을 변경하지 않는다.
- Vector는 CLOVA Studio Embedding v2 / bge-m3, 1,024차원, exact cosine을 사용한다.
- Title/Path 채널은 original query로 heading hierarchy만 검색하며 body를 사용하지 않는다.
- metadata filter의 company, receipt, date/year, report type, loader namespace 의미는 세 채널에서 동일하다.
- Hybrid RAG는 structured SQL retrieval이나 correction-chain retrieval을 대체하지 않는다.
- production interface는 `src.retrieval.search_default_sections()`이다.

Structured 질문은 contract/금액/날짜 등 authoritative column을 SQL로 조회한다. Correction 질문은 explicit/high-confidence `correction_links`만 따라 observation chain을 조회한다. 자연어 section evidence가 필요한 경우에만 위 RAG 경로를 사용한다. Freeze 세부 정책과 미채택 실험은 [Retrieval V1 freeze policy](retrieval_v1_freeze.md)를 따른다.

## 범위와 근거

V1은 `analysis_results/phase2`를 우선 근거로 한 DB/검색 계약이다. 분석 단위는 XML 4,616개, 접수번호 4,201개이며 한 접수 아래 XML 1~3개가 존재한다. `receipt_no`는 문서 파일 ID가 아니라 제출 이벤트 ID다. exchange의 단일판매·공급계약 계열 1,106건(일반 1,040 + 자율 66)을 첫 구현 범위로 삼는다. 원본 `data`는 불변 source of record다.

## 세 검색 경로

| 경로 | 질문 유형 | 저장/검색 | 공개 인터페이스 |
|---|---|---|---|
| Structured Search | 계약금액·상대방·기간·매출액 비율, 시설투자, 재무수치처럼 명시 라벨과 값이 있는 질문 | 타입 컬럼 + 원문 + 단위/기준 + field provenance, SQL filter/sort | `search_contracts`, `get_contract`, 향후 `search_facility_investments`, `get_financial_metric` |
| Document Search / RAG | 사업 내용, R&D, 위험요인, 제품/서비스, 주석처럼 문맥이 필요한 질문 | 계층 section, 표 보존 표현, metadata filter, 우선 PostgreSQL FTS | `search_sections(query, company, concept, period, top_k, mode)` |
| Correction-aware Search | 원공시/정정/재정정 이력, 특정 계약의 현재 관측본 | 확정 edge graph + 접수 시각 + 필드 provenance | `get_correction_history(receipt_no)`, `get_latest_observed(receipt_no)`, `get_latest_observed_contract(receipt_no)` |

Agent는 위 함수의 요청/응답 DTO만 알아야 한다. 모든 결과는 최소 `receipt_no`, 회사, 접수일, `source_path`/locator를 돌려준다. “latest” 응답은 `observation_status=latest_observed_in_current_dataset`를 포함하며 최종 법적 유효값이라고 표현하지 않는다.

`search_sections`의 V1 계약은 다음과 같다. `mode`는 현재 `keyword`만 허용하고 결과는 `section_id`, `receipt_no`, `title`, `concept`, `snippet`, `score`, `source_locator`를 반환한다. `vector`, `hybrid`, `rerank`는 같은 응답 형태를 유지한 채 후속 adapter로 추가한다.

## 데이터 흐름과 경계

`data` XML → read-only parser → staging validation → 6개 V1 테이블 → retrieval 함수 → Agent/LLM 순이다. parser는 접수와 파일을 먼저 분리하고, 각 정규화 값 옆에 raw 값을 보존한다. `contracts.field_provenance`는 필드별 `{file_id, locator, label, parser_version}` 객체다. XML cell locator가 안정화되기 전 XPath 또는 `table-row/cell` 표현을 사용할 수 있으나 source file과 함께 재현 가능해야 한다.

periodic은 제목 기반 section만으로 재무 표를 충분히 찾을 수 없다. section anchor와 table-aware 표현을 병행하고 병합 셀, 단위, 주석을 잃지 않아야 한다. V1에서는 `document_sections.search_tsv`까지만 제공하며 embedding 컬럼과 pgvector extension은 모델/차원 결정 후 migration으로 추가한다.

## 계약 V0 질의 매핑

| 질문 | 호출/SQL 전략 |
|---|---|
| 현대건설의 2025년 계약 | `search_contracts(company, date_from, date_to)`; company/date 복합 index |
| 삼성E&A의 2024년 금액·상대방 | 같은 검색 후 projection은 API에서 제한 |
| 특정 계약의 최신 계약금액 | `get_latest_observed_contract(receipt_no)`; explicit chain의 마지막 관측 row |
| 기업의 가장 큰 계약 | `get_largest_contract`; 최신 관측 node만 남기고 amount desc limit 1 |
| 특정 기간 계약 공시 | date range filter, date/receipt desc |

기업명은 입력 경계에서 공백·법인표기 규칙을 적용해 `company_name_normalized`로 변환한다. 모호한 별칭은 자동 합치지 말고 기업 후보를 반환한다. 기간은 양끝 포함이다. amount가 비공개/미정이면 normalized `NULL`이고 raw 문자열로 상태를 설명한다.

`latest_observed_only`는 successor가 있는 row를 제외한다. 확정 chain 밖의 독립 정정은 독립 관측으로 남으며 similarity candidate 때문에 숨겨지지 않는다.

## Correction 규칙

적재 허용 조건은 `link_method=explicit_original_filing_date`이면서 `confidence=high`인 phase2 행뿐이다. `rare_table_cell_similarity_without_date`는 별도 분석 산출물에만 남기고 `correction_links`에 넣지 않는다. edge는 바로 앞 관측본 → 다음 관측본으로 저장한다. successor의 predecessor는 최대 하나이며 cycle/branch 검증 실패 시 적재를 중단한다.

history는 양방향으로 connected component를 찾은 뒤 `receipt_date`, `receipt_at`, `receipt_no`로 관측 순서를 정한다. 최신 계약 조회는 마지막 접수의 값과 provenance를 반환한다. 부분 정정 필드 상속은 V1에서 하지 않는다. 상속은 변경 범위 판별 없이는 잘못된 합성 레코드를 만들 수 있기 때문이다.

## 스키마 판단

후보 6개 테이블을 모두 유지한다. `disclosure_files`는 1:N과 원문 역추적 때문에 필수다. 범용 key-value 테이블, aliases, chunks/embeddings 분리는 아직 추가하지 않는다. `document_sections`가 V1 검색 단위이며 필요해질 때 chunk child table을 migration한다. 상세 PK/FK/NULL/UNIQUE/INDEX는 실행 가능한 `src/db/schema_v1.sql`이 명세의 기준이다.

## 운영 조건

SQL은 bind parameter만 사용하며 최대 `limit=100`이다. 검색 latency는 DB 호출 직전부터 결과 materialize 완료까지 `perf_counter`로 측정하고, warm-up과 cold run을 분리한다. 결과마다 데이터 snapshot/ingest version을 기록해야 benchmark 재현이 가능하다.
