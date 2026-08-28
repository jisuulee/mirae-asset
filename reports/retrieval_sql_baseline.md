# PostgreSQL 계약 Retrieval V0 SQL Baseline

- 실행일: 2026-08-28
- DB: PostgreSQL 15.19, Docker Compose
- snapshot: companies 70 / disclosures 4,204 / files 4,616 / contracts 1,106 /
  correction links 461
- 범위: structured contract SQL 및 explicit/high correction 조회
- 제외: 자연어 intent parser, Agent, RAG, embedding, pgvector, hybrid, reranker

## 1. Benchmark

기존 correction 중심 seed 8건을 42건으로 교체했다. 7개 유형에 각각 6개 질문을
배치했다.

| query_type | 질문 수 |
| --- | ---: |
| company_period | 6 |
| amount | 6 |
| counterparty | 6 |
| date_range | 6 |
| sales_ratio | 6 |
| correction | 6 |
| receipt_lookup | 6 |

질문 문구와 구조화 query specification은 수동으로 고정했다. Gold receipt는 현재 DB에
동일 조건을 실행해 얻고, 첫 정답의 source XML 경로를 `validation_source`에 기록했다.
정답을 사람의 기억이나 질문 문구에서 추측하지 않았다.

중요한 해석 한계가 있다. 이 baseline은 자연어를 query parameter로 변환하는 능력이
아니라 검수된 parameter를 받은 Retrieval API/SQL의 정확도를 측정한다. 따라서 100%
정확도는 Agent 자연어 이해가 100%라는 의미가 아니다.

Recall@k는 기존 evaluator 정의를 유지해 “정답 집합 중 하나라도 top-k에 존재하는
질문 비율”이다. 여러 계약을 묻는 광범위한 질문에서 전체 정답 집합의 coverage를
뜻하지 않는다.

## 2. 전체 결과

| 조건 | Top-1 | Recall@3 | Recall@5 | MRR | 평균 | P50 | P95 | 최대 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cold 근사 | 100% | 100% | 100% | 1.000 | 4.429ms | 3.976ms | 7.597ms | 18.619ms |
| Warm | 100% | 100% | 100% | 1.000 | 3.393ms | 3.117ms | 6.868ms | 7.371ms |

DB connection 생성은 443.539ms였으며 query latency와 분리했다. 애플리케이션에서 매
질문마다 새 connection을 만들면 이 비용이 SQL 자체보다 훨씬 크므로 connection pool
또는 장기 connection이 필요하다.

Cold 근사 평균 대비 warm 평균은 약 23.4% 감소했다. 최대 latency는 18.619ms에서
7.371ms로 낮아졌다. Cold는 컨테이너 재시작 직후 새 연결의 첫 ordered pass지만
Windows host page cache와 Docker/OS cache를 완전히 비우지는 못했다.

## 3. 유형별 Warm 결과

모든 유형은 6문항이며 Top-1, Recall@3/5, MRR가 각각 100%, 100%, 100%, 1.000이었다.

| query_type | 평균 latency |
| --- | ---: |
| receipt_lookup | 1.501ms |
| company_period | 2.481ms |
| counterparty | 2.493ms |
| correction | 3.660ms |
| date_range | 3.928ms |
| sales_ratio | 4.778ms |
| amount | 4.910ms |

유형당 6건뿐이므로 작은 평균 차이를 일반화하면 안 된다. amount와 sales ratio는 회사
후보를 필터한 후 정렬하므로 단건 PK 조회보다 느리지만 여전히 5ms 안팎이었다.

## 4. Retrieval 변경

평가 유형을 실제 API로 표현하기 위해 `ContractSearch`에 다음 최소 필드를 추가했다.

- `contract_start_from`, `contract_start_to`
- `active_on`
- `min_sales_ratio`, `max_sales_ratio`
- `order_by=sales_ratio_desc`

Evaluator는 새/구 prediction field를 모두 허용하고 P50, 최대 latency, query type별
집계와 실패 목록을 추가했다. 기존 지표 계산 방식은 유지했다.

## 5. 실패 분석

실행 오류, prediction 누락, gold miss는 cold/warm 모두 0건이었다. 따라서 실제 실패
목록은 비어 있다. 다만 다음 위험은 benchmark가 다루지 못한다.

- 회사 alias·과거 사명·법인 표기 변환
- 자연어에서 조건과 정렬 의도를 추출하는 과정
- raw `-` 때문에 normalized NULL인 계약의 숫자/date 검색
- inferred/ambiguous/unresolved correction 관계
- 광범위 multi-result 질문의 정답 전체 coverage

구조화 SQL baseline이므로 실패 0건은 기대 가능한 결과다. 다음 Agent 단계에서는
의도 파싱 오류와 alias 오류가 별도로 나타날 수 있다.

## 6. Query plan과 index

회사/기간 검색은 `disclosures_company_date_idx`, 상대방 부분검색은
`contracts_counterparty_trgm_idx`를 사용했다. 최대 계약 latest-only는 company/date,
contract receipt unique, correction predecessor index를 사용했다. 대표 실행 시간은
0.110~0.458ms였다.

Correction edge 전체 조회는 461건에 대해 Seq Scan을 사용했지만 실행 시간은
0.094ms였다. 현재 graph 구현은 전체 edge를 읽기 때문에 합리적인 plan이다.

현재 snapshot에서는 명백한 index 누락이나 병목이 없어 migration을 추가하지 않았다.
상세 내용은 `reports/retrieval_query_plans.md`와 JSON plan에 있다.

## 7. 다음 단계 전 해야 할 일

1. benchmark gold와 question wording을 다른 팀원이 독립 검수한다.
2. multi-result 질문에는 set coverage 또는 nDCG 같은 별도 지표를 추가한다.
3. Agent intent parser를 도입할 때 SQL 기능 점수와 parsing 점수를 분리한다.
4. 회사 alias 정책과 unknown/ambiguous 처리 규칙을 만든다.
5. connection pool을 포함한 end-to-end API latency를 별도로 측정한다.
6. correction inferred/ambiguous/unresolved 질문은 확정 link benchmark와 분리한다.

이 검증 이후에 periodic keyword baseline을 만들고, 그 다음에야 embedding/vector/hybrid
RAG 실험으로 넘어가는 것이 안전하다.
