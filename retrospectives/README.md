# Retrieval/RAG 회고 통합 목차

이 디렉터리는 작업 당시의 판단, 시행착오, 검증 결과를 날짜와 단계 순서로 읽기 위한 단일 진입점이다. 기존 회고 본문은 삭제하거나 축약하지 않았고, 파일명만 `YYYY-MM-DD_단계.md` 형식으로 평탄화했다.

`docs/`는 현재 적용할 설계와 정책, `reports/`는 재현 가능한 측정 결과와 원시 진단, `retrospectives/`는 왜 그렇게 결정했는지를 기록한다. 따라서 수치의 source of truth는 링크된 report이며 회고는 그 결과의 맥락과 다음 결정을 연결한다.

## 날짜순

| 날짜 | 단계 | 회고 | 핵심 범위 |
|---|---|---|---|
| 2026-08-22 | Retrieval V1 설계 | [Retrieval V1 작업 회고](2026-08-22_retrieval_v1.md) | 계약 검색 V0, PostgreSQL V1 설계, correction policy, 최초 평가 체계 |
| 2026-08-28 | PostgreSQL 실제 구축 | [PostgreSQL Retrieval V1 실제 구축 회고](2026-08-28_postgres_v1_ingestion.md) | Docker/PostgreSQL, 계약 적재, provenance/FK 검증, local regression |
| 2026-08-29 | Full periodic corpus + Keyword | [Full periodic parser/export 및 Keyword 회고](2026-08-29_full_periodic_keyword.md) | 1,054건 parser/export, 82,862 sections, 1,401,752 tables, FTS/trigram/combined |
| 2026-08-30 | Full-corpus benchmark → Vector → Hybrid → Reranker | [Full-corpus Retrieval 실험 회고](2026-08-30_full_corpus_retrieval_pipeline.md) | 120문항 benchmark, Exact Vector, HNSW, Hybrid, deterministic reranker, recall ceiling, Depth50 |

## 단계별 바로가기

### 1. Architecture와 정책

- [Retrieval V1 architecture](../docs/retrieval_v1_architecture.md)
- [Periodic parser design](../docs/periodic_parser_design.md)
- [Retrieval benchmark policy](../docs/retrieval_benchmark_policy.md)
- [Vector retrieval design](../docs/vector_retrieval_design.md)
- [Reranker baseline design](../docs/reranker_baseline_design.md)

### 2. Parser와 PostgreSQL corpus

- [Parser sample validation](../reports/periodic_parser_sample_validation.md)
- [Parser pilot](../reports/periodic_parser_pilot.md)
- [Pilot DB ingestion](../reports/periodic_pilot_db_ingestion.md)
- [Pilot retrieval smoke](../reports/periodic_pilot_retrieval_smoke.md)
- [PostgreSQL ingestion report](../reports/postgres_v1_ingestion_report.md)
- [Full DB validation](../reports/periodic_full_db_validation.json)

### 3. Keyword baseline

- [Pilot Keyword baseline](../reports/periodic_keyword_baseline.md)
- [Full-corpus 36문항 baseline](../reports/periodic_keyword_full_baseline.md)
- [Official-candidate Keyword baseline](../reports/periodic_full_keyword_official_baseline.md)
- [Keyword query plans](../reports/periodic_full_keyword_query_plans.json)
- [Keyword failures](../reports/periodic_full_keyword_failures.json)

### 4. Benchmark와 Human Review

- [Benchmark summary](../reports/periodic_full_benchmark_summary.md)
- [Human Review guide](../docs/benchmark_human_review_guide.md)
- 현재 정책: reviewed 36은 regression set이고 전체 120은 internal/candidate benchmark다. 미검수 84문항을 자동 ACCEPT하지 않는다.

### 5. Vector와 ANN

- [Vector integrity](../reports/periodic_full_vector_integrity.md)
- [Exact Vector baseline](../reports/periodic_full_vector_baseline.md)
- [FTS vs Vector](../reports/fts_vs_vector.md)
- [HNSW experiment](../reports/periodic_full_hnsw_experiment.md)
- [HNSW query plans](../reports/periodic_full_hnsw_query_plans.json)

### 6. Hybrid와 Reranker

- [Hybrid baseline](../reports/periodic_full_hybrid_baseline.md)
- [FTS/Vector/Hybrid comparison](../reports/fts_vector_hybrid_comparison.md)
- [Deterministic reranker baseline](../reports/periodic_full_reranker_baseline.md)
- [Retrieval stage comparison](../reports/retrieval_stage_comparison.md)

### 7. Candidate ceiling과 Depth50

- [Candidate recall ceiling](../reports/candidate_recall_ceiling.md)
- [Question-level candidate failures](../reports/candidate_generation_failures.json)
- [Candidate-generation next steps](../reports/candidate_generation_next_steps.md)
- [Depth50 experiment](../reports/depth50_hybrid_experiment.md)
- [Depth50 pruning analysis](../reports/depth50_pruning_analysis.json)
- [Depth50 stage comparison](../reports/depth50_stage_comparison.md)

## 읽는 순서

처음 프로젝트 맥락을 복구할 때는 날짜순 회고 4개를 먼저 읽고, 특정 결정의 정확한 수치가 필요할 때 단계별 report로 내려간다. 구현을 변경할 때는 회고보다 `docs/`의 현재 정책과 실제 코드·migration을 우선한다.
