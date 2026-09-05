# Retrieval V1 구현 및 평가 계획

## 이번 단계의 결정

계약 V0는 PostgreSQL 정형 검색을 기준선으로 삼는다. 접수 이벤트, 실제 XML 파일, 추출 계약값, 명시 정정 edge를 분리했다. Agent가 SQL을 만들지 않고 `search_contracts`, `get_contract`, `get_largest_contract`, `get_correction_history`, `get_latest_observed_contract`를 호출하도록 인터페이스를 고정했다.

## 적재 순서와 검증 게이트

1. `companies` → `disclosures` → `disclosure_files` 순으로 manifest를 적재한다.
2. exchange `단일판매ㆍ공급계약%`만 parse해 raw/normalized/provenance와 parser version을 `contracts`에 넣는다.
3. phase2 correction chain에서 explicit/high edge만 적재한다. candidate 수가 0건 적재되었는지 검사한다.
4. FK, 파일 수, SHA-256, receipt별 source order, 추출 NULL 비율을 검증한다.
5. benchmark snapshot을 고정하고 쿼리 결과와 latency를 기록한다.

원본 XML은 읽기 전용이다. 재적재는 대상 DB transaction에서 수행하며 원본 디렉터리를 변경하지 않는다.

## Retrieval 평가

`eval/retrieval_questions.jsonl`의 필수 필드는 `id`, `question`, `type`, `expected_company`, 그리고 `expected_receipt_no` 또는 `expected_receipt_nos`다. 선택 필드는 `filters`, `query_receipt_no`, `answer_scope`, `source`다. 다답 질문은 어느 정답이 처음 등장하는지로 rank를 계산한다.

runner 결과 JSONL은 다음 형태다.

```json
{"id":"contract_latest_001","ranked_receipt_nos":["20260120800597"],"latency_ms":12.4}
```

`evaluate_retrieval.py`는 Top-1 Accuracy, Recall@3, Recall@5, MRR, 평균 latency, nearest-rank P95를 산출한다. timeout/error도 빈 결과와 실제 경과시간으로 기록한다. 정확도는 전체 질문을 분모로 하며 누락 예측을 실패 처리한다. 개발/검증 질문을 분리하고 동일 chain의 변형 질문이 양쪽에 새지 않게 chain 단위로 split한다. 최초 benchmark는 사람이 원문 locator까지 검수한 30~50개로 확장한다.

권장 측정 조건은 동일 DB snapshot, 동일 connection pool, warm-up 20회 후 질문당 5회다. 정확도에는 한 실행의 deterministic ranking을, latency에는 반복 전체를 사용하며 평균/P95와 sample count를 함께 보관한다.

## 향후 periodic RAG 실험(설계만)

동일한 질문·정답 section/receipt pool과 동일 chunk snapshot으로 아래를 비교한다.

| 실험 | 통제/측정 |
|---|---|
| keyword only | PostgreSQL FTS 기준선; Recall@k, MRR, lexical miss, latency |
| vector only | embedding 모델/차원/거리 고정; semantic Recall@k, index latency/size |
| keyword + vector | fusion 방식과 가중치 기록; Recall@k/MRR 개선 및 비용 |
| metadata filter + vector | 회사·기간·보고서·concept filter 전후 recall, 후보 수, latency |
| hybrid + reranker | first-stage 후보 k와 reranker 고정; Top-1/MRR, end-to-end P95, 호출 비용 |

정답은 receipt만이 아니라 relevant `section_id` 집합으로 라벨링하고, 표 질문은 table-aware relevance를 별도로 표시한다. nDCG@10과 answer-grounding rate도 RAG 단계의 보조 지표로 추가한다. 아직 embedding 생성, pgvector 적재, reranker 호출은 수행하지 않는다.

## 현재 구현된 것 / 아직 구현하지 않은 것 / 다음 단계

현재 구현: PostgreSQL V1 DDL, 계약 검색용 DB-API 함수, explicit/high correction chain 및 최신 관측 조회, benchmark JSONL 형식·초기 사례, 독립 평가 스크립트.

아직 구현하지 않음: PostgreSQL 실제 인스턴스 적재, production parser 이관, section 본문 추출, pgvector/embedding, periodic hybrid RAG, reranker, Agent 전체.

다음 단계: 로컬 PostgreSQL에 V1 migration → 기존 V0 parser 결과를 staging으로 적재 → provenance/정정 graph 검증 → 30~50개 수동 검수 benchmark와 SQL runner 작성 → 기준 정확도/latency 측정. 그 결과가 안정된 뒤 periodic keyword baseline부터 시작한다.
