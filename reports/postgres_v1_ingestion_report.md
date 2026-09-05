# PostgreSQL Retrieval V1 계약 적재 보고서

실행일: 2026-08-28  
환경: Windows + Docker Compose + PostgreSQL 15.19 (`postgres:15-alpine`)  
결과: PASS

## 1. 전처리 데이터 확인과 매핑

`role2_final`은 전체 본문 복사본이 아니라 Role 2 인계 패키지다. 전체 경량
metadata/index와 correction sidecar는 포함하지만, 26.6GB canonical content인
`data/processed/parsed_disclosures.jsonl`은 포함하지 않는다.

| 파일 | 형식 | 행 수 | 이번 적재 용도 |
| --- | --- | ---: | --- |
| `metadata_normalized.jsonl` | JSONL | 4,204 | canonical 회사·공시 metadata 및 validation |
| `document_index.jsonl` | JSONL | 4,204 | 공시 metadata, parse/quality/lineage flag |
| `correction_lineage.jsonl` | JSONL | 1,004 | 정정 evidence와 deterministic resolution |
| `correction_chains.jsonl` | JSONL | 407 | complete 341, partial 66 chain 검증 |
| `sample_disclosures.jsonl` | JSONL | 90 | content 구조 확인용 sample |
| `sample_document_index.jsonl` | JSONL | 90 | sample index |
| `sample_selection.jsonl` | JSONL | 90 | sample 선정 provenance |
| `artifact_manifest.json` | JSON | 1 | schema/count/hash 계약 |

전체 index에는 회사 70개, 고유 `receipt_no` 4,204개가 있고 중복은 없다. 공시군은
periodic 1,054, major 598, exchange 1,469, holding 1,083건이다. 계약 canonical
subtype `단일판매공급계약체결`은 1,106건이다.

### V1 필드 매핑

| V1 대상 | 전처리/변환 원천 | 처리 |
| --- | --- | --- |
| `companies.corp_code/stock_code/company_name` | `document_index.metadata` | 직접 매핑; 이름 정규화만 별도 파생 |
| `disclosures.receipt_no/receipt_date` | `rcept_no`, `rcept_date` | 문자열 ID 유지, ISO date cast |
| `category/report_name/report_type` | `doc_group`, `report_nm`, `doc_subtype` | 직접 매핑 |
| `is_correction_name` | `is_correction` | 직접 매핑 |
| `is_correction_rm` | 인계본에 별도 RM flag 없음 | `false`; 원 canonical flag와 구분 |
| `disclosure_files` | 원본 file manifest + 원본 파일 | 경로·role·root tag·encoding·크기 매핑, SHA-256 재계산 |
| 계약 금액·상대방·기간·매출비율 | 원본 exchange XML | 기존 보수적 table parser로 별도 staging 변환 |
| raw 값 | 원본 XML cell | 그대로 보존 |
| normalized 값 | 계약 parser | 숫자/date/decimal 파생; `-`는 NULL |
| `field_provenance` | XML 경로와 table row/cell locator | JSONB로 보존 |
| `correction_links` | Role 2 `linked_exact/company_date_report/confidence=1.0` | `explicit_original_filing_date/high`로 명시적 변환; evidence 원본 보존 |

전체 계약 content sidecar가 전달되지 않았으므로 계약 필드는 전처리 파일에서 직접
복사하지 않고 원본 XML의 별도 staging 변환으로 생성했다. 원본과 `role2_final`은
수정하지 않았다.

기존 SQLite V0가 적재한 1,126건과 20건 차이가 난다. SQLite 필터는 보고서명 부분
일치로 계약 해지 20건도 포함했지만, V1은 canonical 계약체결 subtype 1,106건만
포함한다.

## 2. PostgreSQL 환경과 스키마

생성 산출물:

- `docker-compose.yml`
- `.env.example` 및 `.env` ignore 규칙
- `docs/postgres_v1_local.md`

실제 서버는 PostgreSQL 15.19이며 컨테이너 healthcheck가 healthy다. `schema_v1.sql`은
재실행 가능하도록 `IF NOT EXISTS`만 추가했다. 테이블 구조나 Retrieval 설계 의도는
변경하지 않았다.

스키마 검증:

- `pg_trgm`: 설치됨
- public table: 6개
- FK: 9개
- PK/UNIQUE: 11개
- CHECK: 13개
- index: 25개
- pgvector/embedding: 설치·생성하지 않음

## 3. Staging 및 적재

`src/db/load_staging.py`가 두 JSONL을 `doc_id`로 join하고 `rcept_no`를 교차 검증한다.
원본 파일은 읽기만 하며 파일별 SHA-256을 다시 계산한다. `src/db/load_postgres.py`는
PostgreSQL `staging` schema에 먼저 적재한 다음 한 transaction에서 운영 테이블을
upsert한다.

적재 순서는 companies → disclosures → disclosure_files → contracts →
correction_links다. 같은 명령을 두 번 실행했을 때 아래 건수가 그대로 유지됐다.

| 테이블 | 실제 건수 |
| --- | ---: |
| companies | 70 |
| disclosures | 4,204 |
| disclosure_files | 4,616 |
| contracts | 1,106 |
| correction_links | 461 |
| document_sections | 0 (이번 단계 제외) |

## 4. 데이터 품질 검증

- 중복 `receipt_no`: 0
- 모든 FK orphan: 0
- provenance/source/parser version 누락: 0
- 모든 계약 필드 raw 값 누락: 0

| 필드 | normalized NULL | 비율 | 원인 |
| --- | ---: | ---: | --- |
| contract_amount | 89 | 8.0470% | 원문 `-` |
| counterparty | 34 | 3.0741% | 원문 `-` |
| contract_start_date | 119 | 10.7595% | 원문 `-` |
| contract_end_date | 203 | 18.3544% | 원문 `-` |
| sales_ratio | 65 | 5.8770% | 원문 `-` |

위 NULL은 parser 오류가 아니라 공시 원문이 값을 공개하지 않은 경우다. raw `-`와
cell locator는 유지된다.

## 5. 정정공시 검증

Role 2 lineage 1,004건 중 deterministic exact인 `linked_exact` +
`company_date_report` + confidence 1.0만 변환했다. inferred 120건, ambiguous 91건,
unresolved 332건은 `correction_links`에 넣지 않았다.

- 적재 링크: 461
- `explicit_original_filing_date/high` 외 링크: 0
- similarity candidate: 0
- cycle: 0
- 한 successor의 다중 predecessor: 0
- 누락된 predecessor/successor disclosure: 0
- correction history 호출: 정상
- 계약 chain latest observed sample: 10/10 정상

예시: `20230110800416` → `20250829800429` → `20250903800115` →
`20250912800544`. 최신 관측 계약은 현대건설, 393,815,880,000원,
counterparty는 홍콩 병원관리국이다.

## 6. 실제 Retrieval 결과

기존 `src/retrieval/contracts.py`, `corrections.py`를 psycopg connection에 직접
연결했다.

- 회사+기간: 대우건설, 2025-01-01~2025-12-31 조회 성공
- receipt 조회: `20251224800595`, 1,046,363,500,000원
- 최대 계약: 대우건설 `20250526800282`, 1,081,079,120,000원,
  `STATE CONCERN, TURKMENHIMIYA`
- correction history: 정상
- latest observed 계약: 정상, 상태값 `latest_observed_in_current_dataset`

상세 5건 기간조회 및 latest sample 10건은
`reports/postgres_retrieval_samples.json`에 보존했다.

## 7. 테스트

`python -m unittest discover -s tests -v` 결과 8개 테스트가 모두 통과했다.

- 기존 SQLite V0 테스트: 5/5
- PostgreSQL schema/data/retrieval 통합 테스트: 3/3

## 8. 아직 해결되지 않은 문제

1. 전체 canonical content `data/processed/parsed_disclosures.jsonl`이 인계본에 없어
   계약 이외 본문/section 적재는 수행할 수 없다.
2. `receipt_at`은 전달 metadata에 없어 NULL이다.
3. `is_correction_rm`의 독립 canonical 값이 없어 false로 두고 원래
   `is_correction`은 `is_correction_name`과 metadata JSONB에 보존했다.
4. 계약명 전용 안전한 parser가 없어 `contract_name_raw/name`은 NULL이다.
5. partial/inferred/ambiguous/unresolved lineage는 정책상 자동 연결하지 않았다.
6. pgvector, embedding, hybrid RAG, reranker, Agent 연동은 의도적으로 미구현이다.

## 9. 다음 단계

다음 단계는 고정된 DB snapshot을 사용해 평가 질문을 30~50개로 확장하고,
PostgreSQL structured search의 Top-1/Recall@k/MRR과 warm/cold latency 및 P95
baseline을 측정하는 것이다.
