# Phase 4 correction lineage report

- schema: `4.0.0`
- 실행 모드: `corpus-only`
- 정정공시 입력/출력: **1,004/1,004건**

## 연결 결과

| status | 건수 |
| --- | ---: |
| `linked_exact` | 461 |
| `linked_inferred` | 120 |
| `external_target` | 0 |
| `ambiguous` | 91 |
| `unresolved` | 332 |

## Chain

- chain 수: **407**
- complete / partial chain: **341 / 66**
- status gate 적용 후 안전한 root: **476건 (47.41%)**
- parent 확인: **581건 (57.87%)**
- 최대 chain 길이(원공시 포함): **15**
- invariant: **통과**
- cross-company / evidence-less / cycle: **0 / 0 / 0**

## Resolver method

| method | 건수 |
| --- | ---: |
| `company_date_report` | 461 |
| `none` | 332 |
| `company_date_classification` | 120 |
| `multiple_company_date_classification_candidates` | 89 |
| `cycle_guard_ambiguous` | 2 |

## 미확정 원인

| unresolved reason | 건수 |
| --- | ---: |
| `TARGET_DATE_OUTSIDE_COMPANY_CORPUS_RANGE` | 314 |
| `NO_COMPANY_DATE_RECORD_IN_CORPUS` | 17 |
| `TARGET_DATE_INVALID_OR_MISSING` | 1 |

| ambiguous candidate 수 | 문서 수 |
| ---: | ---: |
| 2 | 74 |
| 3 | 8 |
| 4 | 6 |
| 1 | 2 |
| 5 | 1 |

| ambiguous reason | 건수 |
| --- | ---: |
| `MULTIPLE_DETERMINISTIC_CANDIDATES` | 89 |
| `GLOBAL_TARGET_CYCLE` | 2 |

## 판정 원칙

명시적 접수번호, 회사+명시 날짜+보고서명, 회사+날짜+공시분류의 순서로만 연결한다. 단일 후보가 아닐 때는 `ambiguous`, 후보가 없으면 `unresolved`이며 가장 가까운 이전 공시를 임의 선택하지 않는다.

## 알려진 한계

- corpus 밖 원공시는 corpus-only 모드에서 확인할 수 없다.
- 같은 회사·날짜·유형에 복수 후보가 남으면 자동 연결하지 않는다.
- `is_correction=true`는 정정 여부일 뿐 원공시 연결을 뜻하지 않는다.

상세 후보와 근거는 `correction_ambiguous.jsonl`, `correction_unresolved.jsonl`에, 상태·문서군별 결정론적 검토 샘플은 `correction_summary.json`에 보존했다.
