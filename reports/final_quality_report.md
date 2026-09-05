# Phase 5 최종 데이터셋 품질 보고서

- 스키마 버전: `5.0.0`
- 생성 시각(UTC): `2026-08-27T16:35:06.622172+00:00`
- 최종 상태: **PASS**
- Python: `3.13.14`

## 1. Corpus 및 교차 단계 join

- 논리 공시: **4,204건**
- 기업: **70개**
- physical source: **4,622개**
- manifest/metadata/parsed/index join: **4,204/4,204**
- join failure: **0건**

## 2. Parsing 및 구조 품질

| 항목 | 개수 |
| --- | ---: |
| success | 2,928 |
| partial | 1,276 |
| failed | 0 |
| main title 보유 문서 | 4,202 |
| non-empty body 문서 | 4,204 |
| section 보유 문서 | 2,732 |
| section 미보유 문서 | 1,472 |
| main table | 1,440,210 |
| PDF fallback 문서 | 2 |
| table span warning 문서 | 8 |

partial 문서는 제외하지 않는다. `quality.requires_parse_caution=true`로 전달한다.
section이 없는 문서는 원문 구조가 없는 경우이며 임의 section을 생성하지 않았다.

## 3. Provenance

- serialized provenance items (directly revalidated): **41,659,872**
- Phase 2 parsed provenance items (before alternative-content elision): **41,660,802**
- intentionally elided alternative provenance items: **930**
- provenance missing: **0**
- source SHA-256: **4,622/4,622**

`body_text`는 검색 편의 뷰이며 citation source가 아니다. 인용은 block/table/cell locator와 `source_files[].sha256`을 사용한다.

## 4. Metadata 및 lineage

- canonical metadata invalid: **0건**
- source metadata mismatch: **745회**
- lineage exact/inferred: **461/120**
- ambiguous/unresolved: **91/332**
- complete/partial chains: **341/66**
- graph invariant errors: **0**

`is_latest_in_complete_chain`과 `is_superseded_in_complete_chain`은 complete chain에서만 boolean이다. partial/ambiguous/unresolved에는 반드시 `null`이다.

## 5. Artifact

| artifact | records | size | SHA-256 |
| --- | ---: | ---: | --- |
| parsed_disclosures | 4,204 | 26,585,627,476 | `967dcfbbf6fce28f8acd949a681d6389df0f3d6a1c42da1b4f5d69b3d764109c` |
| normalized_metadata | 4,204 | 8,279,693 | `8ad1c1c6c73299eb2841a338cc7001cec4600d3202c53048427d7c9d8bcfde57` |
| correction_lineage | 1,004 | 6,953,642 | `f367eed6395d7332f2a25e9469d1f694cfffb8229f0ff5a2eaf772128b589abc` |
| correction_chains | 407 | 227,698 | `c7c6eb77e73d5e0a92700a3da91e258b026c59ab02342bd3342d31c177b50bd9` |
| document_index | 4,204 | 7,067,636 | `e9b30f2f9ead141dacb7ede288ff663606247600d9cd7c6960651adb897cd6de` |

기본 실행에서는 merged dataset을 생성하지 않았다. Role 2는 `doc_id`로 content, metadata, lineage, document index를 join한다.

## 6. Quality gate

| gate | 결과 |
| --- | --- |
| canonical_hashes_match | PASS |
| canonical_inputs_unchanged | PASS |
| schema_versions_valid | PASS |
| all_jsonl_valid | PASS |
| manifest_metadata_parsed_index_one_to_one | PASS |
| rcept_no_secondary_join_valid | PASS |
| document_index_count_exact | PASS |
| metadata_invalid_zero | PASS |
| parse_failed_zero | PASS |
| source_sha256_complete | PASS |
| provenance_missing_zero | PASS |
| phase2_reported_provenance_missing_zero | PASS |
| phase4_graph_invariants_pass | PASS |
| unsafe_latest_boolean_zero | PASS |
| sample_coverage_complete | PASS |
| merged_not_generated_by_default | PASS |

## 7. Role 2 인계

- 위치: `handoff/role2`
- sample: **90건** (최대 100건)
- sample coverage 누락: **0개**
- 대형 `parsed_disclosures.jsonl`은 복사하지 않았고 manifest 경로·해시로만 참조한다.
