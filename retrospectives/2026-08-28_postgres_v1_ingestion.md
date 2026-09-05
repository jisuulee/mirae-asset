# PostgreSQL Retrieval V1 실제 구축 회고

- 작성일: 2026-08-28
- 담당 역할: 검색·RAG·DB
- 작업 범위: Role 2 전처리 인계 분석, PostgreSQL 15 구축, 계약 V0 적재·검증·검색
- 기준 데이터: `role2_final`, 원본 `data` read-only
- 결과: PASS

## 1. 시작 시점

Retrieval V1 아키텍처와 PostgreSQL 스키마, 계약 검색 및 correction-aware 함수는
작성돼 있었지만 실제 PostgreSQL 인스턴스에 적용하거나 전체 데이터를 적재한 적은
없었다. 이번 작업의 목표는 설계를 크게 바꾸지 않고 계약 Retrieval V0를 로컬에서
재현 가능한 실행 상태로 만드는 것이었다.

## 2. Role 2 인계 데이터에서 확인한 점

인계본에는 전체 4,204건의 canonical metadata/index와 1,004건 correction lineage,
407개 correction chain이 있었다. 다만 26.6GB canonical content
`data/processed/parsed_disclosures.jsonl`은 용량 때문에 복사되지 않았다.

이 제약 때문에 회사·공시·lineage는 Role 2 결과를 authority로 사용하고, 계약 정형
필드는 원본 exchange XML을 읽는 별도 staging 변환으로 보완했다. 원본과 인계 파일은
수정하지 않았다.

계약 범위도 재확인했다. 기존 SQLite V0의 1,126건은 문자열 부분일치로 계약 해지
20건을 포함한다. PostgreSQL V1은 canonical subtype `단일판매공급계약체결` 1,106건을
정확한 계약 범위로 사용했다.

## 3. 구현한 것

- Docker Compose 기반 PostgreSQL 15 환경
- `.env.example`과 secret 제외 규칙
- 재실행 가능한 `schema_v1.sql`
- Role 2 metadata/index join과 원본 파일 검증 staging
- PostgreSQL staging schema와 transaction 기반 idempotent upsert
- 파일별 SHA-256 재계산
- 계약 raw/normalized/provenance 적재
- explicit/high correction link 변환 및 검증
- PostgreSQL 자동 품질 검증기
- 실제 Retrieval smoke runner와 통합 테스트

## 4. 실제 적재 결과

| 테이블 | 건수 |
| --- | ---: |
| companies | 70 |
| disclosures | 4,204 |
| disclosure_files | 4,616 |
| contracts | 1,106 |
| correction_links | 461 |

동일 적재 명령을 다시 실행해도 건수가 유지됐다. 중복 receipt, FK orphan,
correction cycle, 다중 predecessor, similarity 오연결은 모두 0이었다.

## 5. 스키마와 데이터 품질

실제 PostgreSQL은 15.19였다. `pg_trgm`, public table 6개, FK 9개,
PK/UNIQUE 11개, CHECK 13개, index 25개를 확인했다.

모든 계약 필드의 raw 값과 source/provenance가 존재했다. normalized NULL은 원문이
`-`인 경우만 발생했다.

- 계약금액 89건, 8.0470%
- 상대방 34건, 3.0741%
- 시작일 119건, 10.7595%
- 종료일 203건, 18.3544%
- sales ratio 65건, 5.8770%

## 6. Correction 정책

Role 2 lineage 중 `linked_exact`, `company_date_report`, confidence 1.0만 기존
`explicit_original_filing_date/high` 정책으로 변환했다. 원래 resolution과 evidence는
JSONB에 보존했다. inferred 120건, ambiguous 91건, unresolved 332건은 자동 연결하지
않았다.

계약 correction chain 10개에서 history와 `latest_observed_in_current_dataset` 조회를
검증했다. “법적 최종값”이 아니라 현재 snapshot의 마지막 관측이라는 표현을 유지했다.

## 7. 검색 검증

기존 Retrieval 함수로 회사+기간, receipt 직접 조회, 최대 계약, correction history,
latest observed 계약을 실제 DB에 연결해 확인했다. 기존 SQLite 테스트 5개와 신규
PostgreSQL 통합 테스트 3개가 모두 통과했다.

## 8. 잘된 점

설계 문서에서 정의한 경계를 실제 DB에서도 유지했다. 원본 파일과 접수 이벤트를
분리했고, raw와 normalized를 함께 저장했으며, uncertainty가 있는 lineage를 억지로
연결하지 않았다.

staging 검증과 운영 적재를 분리해 잘못된 입력이 운영 테이블 일부만 갱신하는 상황을
막았다. 실제 서버에서 DDL, 제약조건, index, 함수까지 연결 검증해 문서 설계와 실행
환경 사이의 간극도 제거했다.

## 9. 아쉬운 점과 남은 위험

- 전체 canonical content가 없어 `document_sections`는 비어 있다.
- `receipt_at`과 독립적인 `is_correction_rm` 값은 인계 데이터에서 확보하지 못했다.
- 계약명 전용 parser가 없어 `contract_name`은 NULL이다.
- Docker Desktop 시작과 Python driver 설치가 최초 1회 필요하다.
- 현재 검색 검증은 smoke test이며 정확도·latency benchmark는 아직 작다.

## 10. 다음 단계

다음 완료 기준은 고정된 PostgreSQL snapshot에서 검수된 계약 질문 30~50개를 실행해
Top-1, Recall@3/5, MRR과 cold/warm latency를 수치로 남기고, 실패 원인 및 query plan을
분석하는 것이다. 이 baseline을 확정하기 전에는 pgvector, embedding, hybrid RAG,
reranker 또는 전체 Agent 연동으로 확장하지 않는다.

상세 실행 증거는 `reports/postgres_v1_ingestion_report.md`,
`reports/postgres_validation.json`, `reports/postgres_retrieval_samples.json`에 있다.
