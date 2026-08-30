# Full-corpus Retrieval benchmark와 검색 단계 실험 회고

- 작성일: 2026-08-30
- 범위: benchmark 구축, Exact Vector, HNSW, Semantic Hybrid, deterministic reranker, candidate ceiling, Depth50 bounded pruning
- corpus: `periodic-full-v1`
- 상태: 실험·리포트 완료, Human Review 84문항 미완료, 관련 변경 미커밋

## 1. 시작 상태와 고정 정책

Full periodic corpus는 1,054 disclosures, 82,862 `document_sections`, 1,401,752 `retrieval_tables`로 구축됐다. embedding 대상은 narrative section과 PDF narrative page이며 hierarchy-only와 table-only는 제외했다. 기존 원문, chunking, benchmark gold, FTS ranking은 검색 결과에 맞춰 변경하지 않았다.

Benchmark는 120문항으로 구성됐다. 기존 36문항만 reviewed regression set이고 신규 84문항은 독립적인 전문 human review가 끝나지 않았으므로 전체 120은 internal/candidate development benchmark다. Human Review package는 보존하지만 자동 ACCEPT나 Codex 대리 판정은 하지 않는다.

정확한 정책은 [retrieval benchmark policy](../docs/retrieval_benchmark_policy.md), 전체 구성은 [benchmark summary](../reports/periodic_full_benchmark_summary.md)를 따른다.

## 2. Exact Vector baseline

CLOVA Studio Embedding v2의 `bge-m3`, 1,024차원 cosine embedding을 사용했다. 최종 상태는 unique vectors 34,084, chunks 38,971, links 38,971이며 integrity, dimension, namespace, provenance, orphan 검증을 통과했다. 34,084 unique text 중 실제 신규 API 생성은 33,900, 기존 cache reuse는 184였고 permanent failure는 0이었다.

Section은 8,000자 이하를 그대로 유지하고 긴 section만 paragraph 기반 약 6,000자 chunk로 나눴다. Exact Vector는 metadata filter 뒤 cosine exact scan을 reference truth로 고정했다.

Candidate 120 중 section eligible 113에서 Exact Vector는 Top-1 7.08%, R@5 22.12%, R@10 31.86%였다. reviewed 36에서는 Top-1 2.78%, R@5 8.33%, R@10 19.44%였다. 상세 수치와 failure는 [Vector baseline](../reports/periodic_full_vector_baseline.md), [Vector failures](../reports/periodic_full_vector_failures.json)에 남겼다.

## 3. HNSW 실험과 보류 결정

Exact Top-10을 ANN reference truth로 두고 cosine HNSW `m=16`, `ef_construction=64`, `ef_search=100/200/400`을 비교했다. index build는 20.724초, index 크기는 281,051,136 bytes였다.

가장 덜 손실적인 `ef_search=400`도 전체 ANN Recall@10이 84.07%였고 namespace-only probe는 88%였다. namespace-only latency는 동일 실험에서 Exact 평균 825.28ms에서 HNSW 67.13ms로 줄었지만, company/receipt/year-report처럼 선택적인 filter에서는 Exact가 더 빠르고 정확했다.

따라서 HNSW를 전역 또는 기본 backend로 채택하지 않았다. metadata-filtered Vector는 Exact를 유지하고 namespace-only HNSW 결과는 실험 artifact로만 보존했다. 상세 내용은 [HNSW report](../reports/periodic_full_hnsw_experiment.md)와 [query plans](../reports/periodic_full_hnsw_query_plans.json)에 있다.

## 4. FTS + Exact Vector Semantic Hybrid

기존 FTS Top-20과 Exact Vector Top-20을 `section_id`로 deduplicate하고 raw score를 직접 합치지 않는 unweighted RRF `k=60`으로 결합했다. HNSW, weighted tuning, reranker는 이 baseline에 포함하지 않았다.

Candidate 113 기준 결과는 다음과 같았다.

| Stage | Top-1 | R@5 | R@10 | MRR | Receipt R@10 |
|---|---:|---:|---:|---:|---:|
| FTS | 15.93% | 17.70% | 18.58% | 0.1662 | 26.55% |
| Exact Vector | 7.08% | 22.12% | 31.86% | 0.1369 | 58.41% |
| Hybrid RRF | 11.50% | 31.86% | 46.02% | 0.2004 | 71.68% |

Top-10 candidate complementarity는 FTS-only 18, Vector-only 33, both 3, neither 59였다. 두 retriever union의 gold 54개 중 Hybrid Top-10이 52개를 보존했다. Candidate recall은 크게 늘었지만 Top-1은 FTS보다 낮아 다음 병목이 ranking임을 확인했다. 상세 비교는 [Hybrid baseline](../reports/periodic_full_hybrid_baseline.md)과 [three-way comparison](../reports/fts_vector_hybrid_comparison.md)에 있다.

## 5. Deterministic reranker

저장소에는 외부 cross-encoder 또는 고정 HyperCLOVA X chat reranker를 명시적으로 허용하는 권위 있는 규정과 model/endpoint 계약이 없었다. 외부 pretrained model을 임의로 추가하지 않고 query, FTS/Vector rank, title/path overlap, retrieved chunk overlap, consensus만 쓰는 `deterministic-feature-reranker-v1`을 만들었다.

Reranker는 기존 Hybrid Top-10의 순서만 바꿨다. Candidate 113에서 Top-1 13.27%, R@3 30.09%, R@5 35.40%, R@10 46.02%, MRR 0.2345가 됐다. R@10 candidate set은 완전히 보존하면서 rank 16개를 개선하고 33개를 유지했으며 3개를 악화했다. API 요청과 비용은 0이고 cache 재실행은 1,130/1,130 hit였다.

그러나 gold가 Hybrid Top-10에 없는 질문이 61개였기 때문에 가장 큰 병목은 reranking보다 candidate generation이었다. 설계와 결과는 [reranker design](../docs/reranker_baseline_design.md), [reranker baseline](../reports/periodic_full_reranker_baseline.md), [failure report](../reports/periodic_full_reranker_failures.json)에 있다.

## 6. Candidate Recall Ceiling

새 알고리즘을 추가하지 않고 FTS와 Exact Vector depth만 10/20/50/100으로 늘려 union ceiling을 측정했다.

| Depth | FTS | Vector | Union | Neither |
|---:|---:|---:|---:|---:|
| 10 | 18.58% | 31.86% | 47.79% | 59 |
| 20 | 23.01% | 48.67% | 58.41% | 47 |
| 50 | 23.89% | 62.83% | 70.80% | 33 |
| 100 | 23.89% | 74.34% | 81.42% | 21 |

현재 61 failure는 fusion Top-10 내부 demotion 2, Top-20 회복 12, Top-50 회복 14, Top-100 회복 12, Top-100에서도 miss 21로 나뉘었다. Filter ablation으로 회복된 질문은 0이었다. 자동 진단 tag는 lexical mismatch 56, semantic mismatch 36, both miss 21, same-company similar section 16, long section 14, chunk/parent issue 12, gold ambiguity 9, title/path mismatch 6이었다.

Split gold 17문항은 Union@20 35.29%로 unsplit 62.50%보다 불리했지만 Union@100에서는 82.35%까지 회복됐다. 정확한 question-level rank와 provenance는 [candidate failures](../reports/candidate_generation_failures.json), 요약은 [recall ceiling](../reports/candidate_recall_ceiling.md)에 있다.

## 7. Depth50 + bounded pruning

FTS50과 Exact Vector50을 합쳐 평균 51.78개 unique section을 만든 뒤 기존 RRF k=60으로 정렬하고 Top-10/20/30만 동일 deterministic reranker에 전달했다.

Union50에는 gold-bearing question이 80개 있었지만 RRF Top-10/20/30은 각각 48/62/71개를 보존했다. 기존 Top-20 union failure 47개 중 14개가 Union50에서 새로 발견됐으나 RRF Top-30에 7개만 남았고 최종 Top-10으로 올라온 것은 0개였다.

| Pipeline | Top-1 | R@3 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|---:|
| 기존 Depth20 → Rerank10 | 13.27% | 30.09% | 35.40% | 46.02% | 0.2345 |
| Depth50 → Rerank10 | 13.27% | 30.09% | 33.63% | 42.48% | 0.2283 |
| Depth50 → Rerank20 | 13.27% | 30.97% | 34.51% | 47.79% | 0.2373 |
| Depth50 → Rerank30 | 13.27% | 30.97% | 34.51% | 49.56% | 0.2394 |

측정된 설정 중 Depth50→Rerank30이 R@10과 MRR은 가장 높았고 기존 대비 P50 latency 증가는 약 9.86ms였다. 하지만 Top-1은 개선되지 않았고 R@5와 Receipt R@10은 하락했다. 새 Top50-only gold의 최종 회복도 0이므로 production default로 승격하지 않고 실험 결과로 보존했다.

Expanded-30의 남은 primary failure는 Top50 union에 없음 33, RRF pruning loss 9, reranker misrank 15였다. 상세 결과는 [Depth50 experiment](../reports/depth50_hybrid_experiment.md), [pruning analysis](../reports/depth50_pruning_analysis.json)에 있다.

## 8. 현재 결론과 다음 우선순위

1. 현재 기본 Vector backend는 metadata-filtered Exact cosine이다.
2. HNSW와 Depth50 Expanded pipeline은 실험 artifact이며 기본 경로가 아니다.
3. FTS와 Vector는 상호 보완적이고 Hybrid는 candidate recall을 높인다.
4. Deterministic reranker는 Top-10 내부 순위를 개선하지만 candidate miss를 해결하지 못한다.
5. 가장 큰 잔여 집합은 Top50 union에도 없는 33문항이다. 전체 impact 기준 다음 진단 후보는 query expansion/rewrite지만 대회 규정과 provider 허용 범위를 먼저 확인해야 한다.
6. 규정 위험이 낮은 다음 구현 후보는 split/long section의 parent-section aggregation이다.
7. 더 강한 reranker는 RRF Top-30 안에 gold가 있지만 최종 Top-10에서 놓친 15문항을 대상으로 해야 한다.
8. title/path dedicated retrieval은 제한된 6문항과 title 계열 진단을 위해 독립 실험으로 유지한다.

어떤 다음 단계를 수행하더라도 benchmark gold, embedding, chunking, 기존 FTS scoring을 결과에 맞춰 변경하지 않는다.

## 9. 검증 상태

마지막 Depth50 작업 시점 기준 Python compile, targeted tests, 전체 local regression 83 passed/1 deselected, `git diff --check`, secret scan을 통과했다. Benchmark file의 Git blob은 HEAD와 동일했다. 이 일련의 Human Review/HNSW/Hybrid/Reranker/ceiling/Depth50 변경은 아직 commit/push하지 않았다.
