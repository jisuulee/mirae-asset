# Retrieval V1 작업 회고

- 작성일: 2026-08-22
- 담당 역할: 검색·RAG·DB
- 작업 범위: Retrieval V1 설계, 계약 공시 검색 V0, 정정 인지 조회, 평가 체계
- 기준 데이터: `analysis_results/phase2`

## 1. 시작 시점의 상황

프로젝트는 원본 공시 데이터의 1차 분석과 `analysis_results/phase2` 기반 2차 구조 분석까지 완료된 상태였다. 기존 저장소에는 exchange 단일판매·공급계약을 SQLite로 적재하고 조회하는 V0 프로토타입이 있었지만, PostgreSQL 운영 스키마와 Agent가 사용할 안정적인 검색 인터페이스, 검색 품질 평가 체계는 확정되지 않았다.

이번 작업에서는 전체 Agent나 periodic RAG를 앞서 구현하지 않고 다음 세 가지에 집중했다.

1. 검색 경로와 인터페이스 경계를 먼저 확정한다.
2. 계약 공시를 기준으로 PostgreSQL 최소 스키마와 SQL 검색 V0를 설계한다.
3. 검색 결과를 수치로 평가할 수 있는 benchmark 형식을 만든다.

원본 `data`는 분석 중 읽기만 했으며 수정·삭제·이동하지 않았다.

## 2. 확인한 데이터 특성

phase2 결과에서 설계에 직접 영향을 준 사실은 다음과 같다.

- XML 파일은 4,616개지만 고유 접수번호는 4,201개다.
- 하나의 `receipt_no` 아래 XML 파일이 1~3개 존재할 수 있다.
- `receipt_no`는 파일 식별자가 아니라 제출 이벤트 식별자다.
- 정정공시는 일반적으로 새로운 `receipt_no`를 가진다.
- exchange에는 단일판매·공급계약체결 1,040건과 자율공시 66건이 관측되었다.
- 계약금액, 계약상대방, 시작일, 종료일, 매출액 대비 비율은 안정적인 정형 필드 후보였다.
- periodic의 사업 내용·R&D 등은 section 검색이 적합하지만, 재무수치와 주석은 표 구조와 문맥을 함께 보존해야 한다.
- phase2 정정 분석에는 명시 연결과 similarity candidate가 함께 존재한다. similarity candidate는 확정 관계로 사용할 수 없다.
- 명시 chain의 마지막 접수도 법적·절대적인 최종값이 아니라 현재 데이터에서 관측된 마지막 본문일 뿐이다.

## 3. 이번에 내린 설계 결정

### 검색 경로 분리

검색을 세 경로로 분리했다.

- Structured Search: 계약금액, 상대방, 기간, 비율처럼 명시적으로 추출 가능한 값
- Document Search / RAG: 사업 내용, 연구개발, 위험요인, 제품·서비스, 재무제표 주석
- Correction-aware Search: 원공시, 정정, 재정정 이력과 현재 데이터의 최신 관측본

Agent는 내부 테이블이나 SQL을 직접 알지 않고 `search_contracts`, `get_contract`, `get_largest_contract`, `get_correction_history`, `get_latest_observed_contract` 같은 함수만 호출하도록 경계를 잡았다.

### PostgreSQL 스키마

다음 6개 테이블을 V1 최소 단위로 유지했다.

- `companies`
- `disclosures`
- `disclosure_files`
- `contracts`
- `correction_links`
- `document_sections`

특히 `disclosures`와 `disclosure_files`를 분리해 접수 1:N 파일 관계를 표현했다. 계약값은 raw 문자열과 정규화 값을 함께 저장하며, `source_file_id`와 `field_provenance`로 원본 XML 위치를 추적할 수 있게 했다.

범용 key-value 테이블, 회사 별칭 테이블, 별도 chunk·embedding 테이블은 현재 범위에 비해 과한 정규화라고 판단해 추가하지 않았다.

### 정정공시 처리

`correction_links`에는 다음 조건을 모두 만족하는 edge만 들어갈 수 있다.

- `link_method = explicit_original_filing_date`
- `confidence = high`

DB CHECK 제약으로 이 규칙을 강제해 similarity candidate가 실수로 최신값 판단에 섞이는 것을 막았다. 조회 결과에는 `latest_observed_in_current_dataset` 상태를 붙인다.

부분 정정에서 이전 접수의 값을 자동으로 상속하는 기능은 넣지 않았다. 필드별 변경 범위를 판별하지 않은 상태에서 값을 합치면 실제로 존재하지 않은 합성 레코드를 만들 수 있기 때문이다.

### 평가 체계

질문과 정답 `receipt_no`를 가진 JSONL benchmark와 독립 평가 스크립트를 만들었다. 측정 지표는 다음과 같다.

- Top-1 Accuracy
- Recall@3
- Recall@5
- MRR
- 평균 latency
- P95 latency

현재 benchmark에는 phase2 문서에 명시된 correction chain을 근거로 한 초기 질문 8개가 포함되어 있다. 이는 형식과 실행 흐름을 확인하기 위한 seed이며, 모델이나 검색 품질을 대표하는 최종 benchmark는 아니다.

## 4. 만들어진 산출물

| 산출물 | 역할 |
|---|---|
| `docs/retrieval_v1_architecture.md` | 세 검색 경로, 인터페이스, 계약 V0 및 정정 정책 |
| `src/db/schema_v1.sql` | PostgreSQL V1 실행 DDL |
| `src/retrieval/contracts.py` | 계약 검색, 단건 조회, 최신 관측 계약, 최대 계약 조회 |
| `src/retrieval/corrections.py` | explicit/high correction chain 및 최신 관측본 조회 |
| `eval/retrieval_questions.jsonl` | 초기 계약 검색 benchmark |
| `eval/evaluate_retrieval.py` | 정확도 및 latency 평가기 |
| `reports/retrieval_v1_plan.md` | 적재·평가·향후 RAG 실험 계획 |

기존 SQLite V0 프로토타입은 삭제하거나 대체하지 않았다. PostgreSQL V1 설계의 참고 구현 및 회귀 테스트 대상으로 보존했다.

## 5. 검증 결과

- Python 모듈 compile 검사 통과
- 기존 계약 ingestion/query 테스트 5개 통과
- benchmark JSONL 8개 행 parsing 및 ID 중복 검사 통과
- 평가기의 Top-1, Recall@3/5, MRR, 평균/P95 latency smoke test 통과
- Windows PowerShell에서 생성된 UTF-8 BOM JSONL도 읽도록 호환성 보완

로컬 환경에는 `psql` 실행 파일이 없어서 `schema_v1.sql`을 실제 PostgreSQL 인스턴스에 적용하는 통합 검증은 하지 못했다.

## 6. 잘된 점

첫째, 검색 기술보다 데이터 의미와 인터페이스를 먼저 고정했다. pgvector를 바로 도입하지 않아 계약 질문처럼 SQL이 더 정확한 영역과 장문 검색 영역의 책임이 섞이지 않았다.

둘째, 접수와 파일을 분리했다. phase2의 4,201개 접수/4,616개 XML 차이를 스키마에 반영해 향후 연결·별도 재무제표나 첨부 XML을 무리 없이 수용할 수 있다.

셋째, 정정 관계의 불확실성을 숨기지 않았다. similarity candidate를 최신값 결정에서 제외하고 “최신 관측본”이라는 표현을 API 수준에서 유지했다.

넷째, provenance를 부가 정보가 아니라 필수 설계 요소로 취급했다. 검색 결과에서 원본 XML과 필드 위치까지 되돌아갈 수 있어 추출 오류 검수와 답변 grounding에 활용할 수 있다.

다섯째, 평가기를 검색 구현과 분리했다. PostgreSQL 함수, API 또는 향후 Agent가 동일한 prediction JSONL을 만들면 같은 기준으로 비교할 수 있다.

## 7. 아쉬운 점과 위험 요소

초기 benchmark가 8개로 작고 최신 관측 질문에 치우쳐 있다. 계약금액, 상대방, 기간 검색과 최대 계약 질의를 포함한 수동 검수 정답이 더 필요하다.

회사명 정규화는 공백 제거와 case folding 수준이다. `삼성E&A`, 법인 표기, 과거 회사명 같은 별칭 문제는 아직 해결하지 않았다. 별칭을 성급하게 자동 병합하면 다른 회사를 합칠 위험이 있어 별도 검수 정책이 필요하다.

`latest_observed_only`는 명시 successor가 있는 공시를 제외하는 V1 방식이다. 독립 정정, chain 누락, 철회 공시는 현재 관측 데이터의 한계로 남는다.

PostgreSQL 실적재가 없으므로 DDL 실행, query plan, 실제 index 효과, connection pool을 포함한 latency는 아직 측정하지 못했다.

periodic `document_sections`는 스키마와 검색 계약만 존재한다. section parser, 표 보존 표현, chunk 정책은 실험 전 검증이 필요하다.

## 8. 다음 작업 우선순위

1. PostgreSQL 개발 인스턴스에 `schema_v1.sql`을 적용하고 migration test를 추가한다.
2. 기존 SQLite 계약 parser 결과를 staging을 거쳐 PostgreSQL에 적재한다.
3. 접수 수, 파일 수, SHA-256, raw/normalized NULL 비율과 provenance를 검증한다.
4. explicit/high correction edge만 적재됐는지 검사하고 cycle·branch 검증을 수행한다.
5. 계약금액·상대방·기간·최대 계약 질문을 포함해 benchmark를 30~50개로 확장한다.
6. 동일 snapshot에서 SQL runner를 실행해 정확도와 warm/cold latency 기준선을 남긴다.
7. 이후에만 periodic keyword baseline을 만들고 vector/hybrid/reranker 실험으로 확장한다.

## 9. 현재 상태 요약

현재 구현된 것은 Retrieval V1 아키텍처, PostgreSQL DDL, 계약 검색 인터페이스, explicit correction chain 조회, 최신 관측본 정책, 초기 benchmark와 평가기다.

아직 구현하지 않은 것은 PostgreSQL 실제 적재와 운영 검증, periodic section parser, embedding 생성, pgvector 대량 적재, hybrid 검색, reranker, 전체 Agent다.

다음 단계의 완료 기준은 “PostgreSQL에 계약 V0가 재현 가능하게 적재되고, 검수된 benchmark로 정확도와 latency가 숫자로 보고되는 상태”다.
