# PostgreSQL Retrieval V0 Query Plan 점검

기준: PostgreSQL 15.19, 계약 1,106건, 공시 4,204건, warm 상태에서
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` 실행.

| 대표 질의 | 실행 시간 | 사용 index | 판단 |
| --- | ---: | --- | --- |
| 회사 검색 | 0.458ms | `disclosures_category_type_date_idx`, `disclosures_company_date_idx`, contract receipt unique | 정상 |
| 회사+기간 | 0.110ms | `disclosures_company_date_idx`, contract receipt unique | 정상 |
| 회사 내 금액 정렬 | 0.293ms | `disclosures_company_date_idx`, contract receipt unique | 회사 후보를 좁힌 뒤 소규모 sort |
| 상대방 부분검색 | 0.166ms | `contracts_counterparty_trgm_idx`, `disclosures_pkey` | trigram index 정상 사용 |
| latest-only 최대 계약 | 0.425ms | company/date, contract receipt unique, correction predecessor index | 정상 |
| correction edge 전체 읽기 | 0.094ms | Seq Scan | 461건 전체 graph를 읽는 현재 함수에 합리적 |

회사 필터에서 `companies_name_trgm_idx`가 보이지 않는 이유는 회사가 70건뿐이고 정확
일치 조건이므로 planner가 작은 companies 테이블을 직접 읽는 편이 저렴하기 때문이다.
기간 필터는 `(company_id, receipt_date DESC, receipt_no DESC)` index를 사용한다.

계약금액 정렬은 전역 `contracts_amount_idx`보다 먼저 회사 후보를 줄인 뒤 정렬한다.
현재 데이터 크기에서는 더 빠르고 실행 시간도 0.3ms 미만이다. 상대방 `%substring%`
검색은 `pg_trgm` GIN index를 실제 사용했다.

Correction 함수는 정책상 explicit/high edge 전체를 메모리 graph로 만든다. 461건에서는
Seq Scan 0.094ms로 문제가 없지만 link가 크게 증가하면 recursive SQL 또는 receipt에서
시작하는 양방향 graph 조회로 변경할 수 있다.

## Baseline 이후 개선 후보

현재 baseline만으로 즉시 추가할 index는 없다. 다음 조건에서 재검토한다.

- `sales_ratio_desc` 질문이 늘고 contracts가 수십만 건으로 증가할 때
  `contracts(sales_ratio DESC NULLS LAST)` 후보
- 회사별 금액 정렬이 병목이 될 때 contract와 company를 함께 좁힐 수 있는 denormalized
  strategy 또는 materialized latest view
- correction edge가 대폭 증가해 전체 graph 로드가 병목이 될 때 predecessor/successor
  양방향 index와 recursive query

원본 JSON plan 전체는 `reports/retrieval_query_plans.json`에 보존했다. Baseline을 먼저
남긴다는 원칙에 따라 이번 작업에서는 index migration을 추가하지 않았다.
