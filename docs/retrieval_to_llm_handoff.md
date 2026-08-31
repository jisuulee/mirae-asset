# Retrieval → LLM 인수인계 가이드

대상: HyperCLOVA X, prompt, structured output, answer generation, citation, answer verification 담당자

기준 branch: `feature/retrieval-v1`

Retrieval V1 checkpoint: `1d0421d0725f4d1f743ba944686b7d781f8f3bc6`

Question Router V1 checkpoint: `a2d7b19cca764d6b53b15cd6f30d7945222be7b4`

## 한눈에 보는 책임 경계

```text
User Question
→ Question Router
→ SQL / RAG / Correction / Composite
→ provenance-backed evidence
→ [여기부터 LLM/Prompt/Verification 담당]
→ HyperCLOVA X
→ answer + citation + verification
```

Retrieval 담당 범위는 질문 routing, evidence 검색, provenance 제공까지다. LLM 담당은 검색된 evidence만 사용한 답변 생성, evidence와 citation 연결, unsupported claim 방지, 답변 검증을 담당한다. Retrieval V1의 내부 설정을 HCX 계층에서 조정하지 않는다.

## 3번 담당 5분 Quick Start

1. `.env.example`을 참고해 PostgreSQL 환경변수를 준비하고 `docker compose up -d postgres`로 DB를 실행한다.
2. 현재 shell에 `DATABASE_URL`을 설정한다. RAG가 포함될 수 있으면 `CLOVASTUDIO_API_KEY`도 설정한다. `.env` 파일은 Python 코드가 자동으로 읽지 않는다.
3. `src.db.load_postgres.connect()`로 연결하고 `src.retrieval.retrieve()`를 호출한다.
4. 먼저 `result.decision.needs_clarification`, 그다음 `result.errors`와 `result.evidence`를 확인한다.
5. `result.to_dict()`는 날짜와 `Decimal`까지 JSON-safe하게 변환한다.
6. HCX context에는 evidence의 내용과 식별용 provenance만 넣고, retrieval score는 기본적으로 넣지 않는다.

```python
import os

from src.db.load_postgres import connect
from src.retrieval import retrieve
from src.retrieval.embeddings import ClovaEmbeddingV2

question = "삼성전자의 연구개발 분야를 설명해줘"
provider = ClovaEmbeddingV2(os.environ["CLOVASTUDIO_API_KEY"])

with connect(os.environ["DATABASE_URL"]) as connection:
    result = retrieve(question, connection, provider=provider)
    payload = result.to_dict()

if payload["router"]["needs_clarification"]:
    # HCX 답변 생성 대신 clarification 질문을 생성한다.
    ...
elif not payload["evidence"]:
    # evidence 없이 사실 답변을 만들지 않는다.
    ...
else:
    # payload["evidence"]를 HCX 입력용 context로 변환한다.
    ...
```

`retrieve()`는 RAG route에서 query embedding API를 호출한다. SQL/Correction-only 질문은 embedding provider 없이 실행할 수 있다. 전체 예제는 `examples/retrieval_handoff_demo.py`에 있다.

## 권장 integration entrypoint

일반 사용자 질문은 다음 함수 하나로 시작하는 것을 권장한다.

```python
from src.retrieval import retrieve

retrieve(
    question: str,
    connection,
    *,
    provider: EmbeddingProvider | None = None,
    rerank_cache: RerankCache | None = None,
    known_companies: list[str] | None = None,
    limit: int = 10,
) -> RoutedSearchResult
```

- `connection`: psycopg DB connection. 필수다.
- `provider`: RAG route가 포함되면 필수다. V1 production provider는 `ClovaEmbeddingV2`다.
- `rerank_cache`: 선택적 deterministic reranker cache다.
- `known_companies`: 생략하면 DB의 `companies`에서 읽는다. 테스트나 이미 로드된 vocabulary가 있을 때만 전달한다.
- `limit`: SQL 결과 한도이자 최종 RAG `top_k`다. RAG에서는 frozen fusion depth 때문에 실질적으로 최대 10이다.
- 반환값 `RoutedSearchResult`는 `decision`, route-tagged `evidence`, non-fatal adapter `errors`를 가진다.

LLM 계층이 route를 다시 판정하거나 개별 retriever를 직접 선택할 필요는 없다. 단, 현재 unified `Evidence`에 없는 RAG diagnostic 필드까지 필요한 offline 분석 도구는 아래 `search_default_sections()`를 직접 사용할 수 있다.

## Public interface reference

### Question Router

```python
from src.retrieval import route_question

route_question(
    question: str,
    *,
    known_companies: Sequence[str] = (),
) -> RouteDecision
```

DB나 API 호출 없이 routing만 확인할 때 사용한다. 회사명은 전달된 vocabulary와 exact normalized match되는 경우에만 추출된다. 일반 integration에서는 `retrieve()`가 DB vocabulary를 자동 공급한다.

### Default RAG

```python
from src.retrieval import search_default_sections

search_default_sections(
    connection,
    query: str,
    provider: EmbeddingProvider,
    *,
    top_k: int = 10,
    company: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    report_type: str | None = None,
    receipt_no: str | None = None,
    corpus_namespace: str = "periodic-full-v1",
    config: RetrievalV1Config = DEFAULT_RETRIEVAL_V1,
    rerank_cache: RerankCache | None = None,
) -> list[dict[str, Any]]
```

서술형 section evidence를 직접 검색할 때 사용한다. `top_k`는 1~10이어야 한다. HCX integration에서는 `config`를 덮어쓰지 않는다.

### Structured SQL

```python
from src.retrieval.contracts import ContractSearch, get_contract, search_contracts

get_contract(connection, receipt_no: str) -> dict[str, Any] | None
search_contracts(connection, query: ContractSearch) -> list[dict[str, Any]]
```

`ContractSearch`의 실제 선택 필드는 `company`, `date_from`, `date_to`, `counterparty`, 금액/계약일/매출비율 범위, `active_on`, `latest_observed_only`, `order_by`, `limit`, `offset`이다. `limit`은 1~100이다. 정확한 계약금액·상대방·기간·매출비율에는 narrative RAG보다 authoritative structured SQL 값을 우선한다.

### Correction

```python
from src.retrieval.corrections import get_correction_history, get_latest_observed
from src.retrieval.contracts import get_latest_observed_contract

get_correction_history(connection, receipt_no: str) -> list[dict[str, Any]]
get_latest_observed(connection, receipt_no: str) -> dict[str, Any] | None
get_latest_observed_contract(connection, receipt_no: str) -> dict[str, Any] | None
```

`get_correction_history()`는 explicit/high-confidence edge로 연결된 filing을 관측 순서로 반환한다. `get_latest_observed*()`는 `observation_status="latest_observed_in_current_dataset"`를 붙인다. 이는 현재 dataset에서 마지막으로 관찰된 filing이지 법적으로 절대적인 최종 유효본이라는 뜻이 아니다.

## Route와 우선순위

| Route | 대표 질문 | 실행 경로 |
|---|---|---|
| `sql` | 계약금액, 상대방, 계약기간, 매출비율, 접수번호 exact lookup | `get_contract` 또는 `search_contracts` |
| `rag` | 사업내용, 연구개발, 위험요인, 전략, 회사 설명 | frozen `search_default_sections` |
| `correction` | 정정 여부, 이전/최초 공시, 최신 정정, 변경 이력 | `get_correction_history` |
| `composite` | 금액과 정정 내용처럼 둘 이상의 evidence가 필요한 질문 | atomic routes를 순서대로 실행 |

Correction terminology, structured field intent, narrative intent를 각각 보존한다. 최종 atomic route 실행 순서는 `sql → correction → rag`다. 접수번호만으로 correction 질문을 SQL로 강제하지 않는다. 확실한 rule이 없으면 general-document RAG fallback이며 confidence는 낮아질 수 있다.

### Clarification

`needs_clarification=True`이면 `retrieve()`는 retrieval을 실행하지 않고 빈 evidence를 반환한다. 현재 대표 조건은 빈 질문, 회사/접수번호가 없는 지시어 기반 structured 질문, 접수번호 없는 correction-chain 요청이다. LLM 계층은 이때 사실 답변 대신 `clarification_reason`을 바탕으로 필요한 식별자를 질문해야 한다.

## Router result contract

`RouteDecision.to_dict()`의 현재 필드는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `route` | `sql`, `rag`, `correction`, `composite` 중 최종 분류 |
| `routes` | 실제 실행할 atomic route의 안정된 순서 |
| `intent` | `contract_amount`, `correction_history`, `narrative_evidence` 등의 해석된 intent |
| `company`, `year`, `report_type`, `receipt_no` | 명시적으로 추출한 metadata |
| `structured_fields` | 탐지된 structured field 이름 목록 |
| `filters` | `date_from`, `date_to`, `report_type`, `company`, `receipt_no` |
| `confidence` | deterministic rule의 routing confidence; answer confidence가 아님 |
| `reason` | 어떤 일반 rule이 발동했는지 설명 |
| `needs_clarification` | retrieval 전에 사용자 확인이 필요한지 |
| `clarification_reason` | clarification 사유 |
| `router_version` | 현재 `question-router-v1` |

## Unified Evidence contract

`retrieve()`는 모든 route를 아래 `Evidence` dataclass로 감싼다. 빈 칸은 해당 route에서 현재 제공하지 않는 값이다.

| Field | SQL | RAG | Correction | 의미 |
|---|---|---|---|---|
| `route` | `sql` | `rag` | `correction` | evidence 생성 경로 |
| `evidence_type` | `structured_contract` | `document_section` | `correction_observation` | evidence 종류 |
| `receipt_no` | O | O | O | filing 식별자 |
| `company` | O | O | O | 회사명 |
| `report_name` | O | O | O | 원 보고서명 |
| `receipt_date` | O | O | O | 접수일; `to_dict()`에서는 ISO 문자열 |
| `evidence_text` | 구조화 값을 읽기 좋은 문자열로 조합 | retrieved section/chunk context | 접수일·보고서명·정정 flag 요약 | HCX가 읽을 기본 텍스트 |
| `evidence_value` | normalized 계약 필드 dict | `None` | 정정명/정정 RM boolean dict | 구조화 값 |
| `provenance` | `field_provenance` | section 및 vector chunk provenance | `explicit_chain`, `observation_order` | source 역추적 정보 |
| `source_file` | 계약 XML 상대경로 | disclosure file 상대경로 | chain filing의 대표 file 상대경로 | source file |
| `source_locator` | provenance에서 찾은 locator | section/page locator | 현재 `None` | source 내부 위치 |
| `rank`, `score` | rank / `None` | final rank / reranker score | observation order / `None` | ordering 및 검색 score |
| `retrieval_sources` | `structured_sql` | `fts`, `vector`, `title_path`의 조합 | `correction_chain` | candidate를 찾은 channel |

### 중요한 현재 경계

Unified `Evidence`에는 `section_id`, `title`, `section_path`, per-channel rank/raw score, `rrf_score`, `reranker_score`가 별도 top-level 필드로 승격되어 있지 않다. RAG의 `source_locator`, `source_file`, `provenance`, `retrieval_sources`는 보존된다. 더 상세한 offline diagnostic이 필요하면 `search_default_sections()` 직접 결과를 사용한다. 존재하지 않는 unified 필드를 production contract라고 가정하지 않는다.

`search_default_sections()` 직접 결과에는 현재 다음이 포함된다.

- identity/context: `section_id`, `receipt_no`, `company_name`, `company`, `report_name`, `receipt_date`, `title`, `section_path`
- content: `body_snippet`, `rerank_text`, `evidence_text`
- source: `source_file`, `source_locator`, `provenance`, `file_role`
- final ranking: `rank`, `score`, `rrf_score`, `reranker_score`, `reranker_features`, `hybrid_rank`, `original_hybrid_rank`
- channel diagnostics: `retrieval_sources`, `channel_ranks`, `channel_scores`, `channel_provenance`, `fts_rank`, `vector_rank`, `title_path_rank`, `title_rank`
- vector provenance: `channel_provenance["vector"]`에 존재할 경우 `vector_chunk_id`, `chunk_order`, locator/provenance/file
- policy marker: `retrieval_config="retrieval-v1-frozen"`

## LLM 입력과 diagnostic 분리

### Answer generation에 권장

- `evidence_type`, `receipt_no`, `company`, `report_name`, `receipt_date`
- `evidence_text`와 SQL/Correction의 `evidence_value`
- `source_file`, `source_locator`, `provenance`
- RAG direct 결과를 사용하는 경우 `title`, `section_path`

### Retrieval diagnostic/debug 전용

- `confidence`를 answer truth confidence로 사용하지 않는다.
- `score`, `rrf_score`, `reranker_score`, `reranker_features`
- `channel_ranks`, `channel_scores`, `fts_rank`, `vector_rank`, `title_path_rank`
- `vector_chunk_id`, `chunk_order`, `hybrid_rank`, `original_hybrid_rank`

Diagnostic 값은 ranking 설명과 재현에는 유용하지만 factual evidence가 아니다.

## HCX 입력용 권장 변환 예시

아래는 production schema가 아니라 LLM 계층에서 만들기 좋은 conceptual payload다.

```json
{
  "question": "이 회사가 집중하는 연구개발 분야를 설명해줘",
  "route": "rag",
  "evidence": [
    {
      "evidence_id": "E1",
      "receipt_no": "...",
      "company": "...",
      "report_name": "...",
      "filing_date": "2026-03-20",
      "title": null,
      "section_path": null,
      "text": "...retrieved evidence text...",
      "source_file": "periodic/.../document.xml",
      "source_locator": "...",
      "provenance": {}
    }
  ]
}
```

Unified `retrieve()`만 사용하면 `title`/`section_path`가 없으므로 위 예시에서 `null`이 될 수 있다. 이를 반드시 요구한다면 HCX integration 전에 Retrieval 담당자와 output adapter 확장을 별도 계약해야 하며, LLM 계층이 DB를 재조회해 임의 조합하지 않는다.

## Citation 연결 가이드

답변을 만든 뒤 문자열 검색으로 출처를 다시 추측하지 않는다. HCX context의 각 evidence에 `E1`, `E2` 같은 ID를 부여하고, 생성된 claim이 사용한 evidence ID를 반환하도록 만든 뒤 해당 evidence의 `receipt_no`, `source_file`, `source_locator`, `provenance`로 citation을 구성하는 방식을 권장한다.

- XML/HTML: section locator/path 및 file provenance를 사용한다.
- PDF fallback: semantic title이 없을 수 있으므로 page locator와 provenance를 사용한다.
- SQL: `field_provenance` 안의 field별 locator와 `source_file`을 사용한다.
- Correction: receipt와 observation order/source file을 사용한다. unified correction evidence의 `source_locator`는 현재 없다.

## SQL evidence 주의사항

`search_contracts()` 직접 결과에는 normalized 값과 일부 raw 값이 함께 있다: `contract_amount`/`contract_amount_raw`, `counterparty`/`counterparty_raw`, 날짜, `sales_ratio`, `field_provenance`, `source_path`. 반면 unified `Evidence.evidence_value`에는 `contract_name`, normalized amount/currency, normalized counterparty, normalized start/end date, `sales_ratio`만 들어간다.

- `contract_amount`, `sales_ratio`는 DB에서 `Decimal`일 수 있으며 `RoutedSearchResult.to_dict()`가 문자열로 안전하게 변환한다.
- normalized 값이 `None`이면 숫자를 추측하지 않는다. raw 상태가 필요하면 direct SQL 결과를 확인한다.
- currency와 기준을 생략하지 않는다.
- exact numeric 질문은 SQL evidence를 우선하고, RAG 문장을 숫자의 authoritative 대체물로 사용하지 않는다.
- `latest_observed_only`는 successor가 있는 row를 제외하는 현재 dataset 관측 정책이다.

## Correction evidence 주의사항

Correction graph에는 `link_method=explicit_original_filing_date`이면서 `confidence=high`인 edge만 들어간다. inferred, ambiguous, similarity-only candidate는 연결하지 않는다. History는 접수일·접수시각·접수번호 관측 순서이며 branch/cycle 또는 missing disclosure는 조용히 추측하지 않고 오류가 난다.

V1은 부분 정정 필드 상속을 하지 않는다. 따라서 “최신 관측본”과 “법적 최종 유효값”, “모든 과거 필드를 합성한 완성 레코드”를 동일시하지 않는다. `latest_observed_in_current_dataset`라는 표현을 그대로 유지한다.

## RAG와 PDF 주의사항

Frozen RAG는 다음과 같다.

```text
Original Question
→ FTS20 + Exact Vector20 + Title/Path20
→ unweighted RRF k=60
→ Top10
→ deterministic reranker
```

LLM 담당자가 이 내부 depth, weight, embedding, chunking을 직접 제어할 필요가 없다. PDF fallback은 page-level synthetic section이며 semantic title이 `None`일 수 있다. `title=None`을 retrieval 오류로 보지 말고 `source_locator`, page provenance, `evidence_text`, `source_file`로 식별한다. synthetic `PDF page N` display title을 semantic heading으로 해석하지 않는다.

## Empty, partial failure, invalid metadata

처리 순서는 다음을 권장한다.

1. `needs_clarification=True`: retrieval 결과를 기다리지 말고 사용자에게 식별 정보를 요청한다.
2. `errors`가 있고 evidence가 없음: retrieval failure/no-answer로 처리하고 사실을 생성하지 않는다.
3. evidence가 없음: “현재 검색된 근거에서 확인할 수 없음” 정책을 적용한다.
4. Composite에서 일부 route만 성공: `errors`와 evidence의 `route`를 확인하고, 성공한 범위만 답하거나 누락 route를 명시한다.
5. Router metadata가 의도와 다르면 HCX가 임의로 filter를 바꾸지 말고 clarification 또는 Retrieval regression report로 보낸다.

`retrieve()`는 adapter의 `LookupError`/`ValueError`를 route별 error 문자열로 보존한다. DB 연결 오류나 provider 장애 등 다른 예외는 호출 계층에서 처리해야 한다.

## Composite 예제

질문: `접수번호 20240102000001 계약금액과 정정 내용을 알려줘`

- Router: `route="composite"`, `routes=["sql", "correction"]`
- Retrieval: SQL structured contract evidence와 correction observation evidence를 순서대로 수집
- LLM: 계약금액은 SQL evidence에서 인용하고, 정정 이력은 correction evidence에서 설명
- 주의: 특정 route의 결과가 없을 수 있으므로 evidence의 실제 `route`와 `errors`를 확인한다.

## 실행 예제 네 가지

모두 `DATABASE_URL`과 실행 중인 PostgreSQL이 필요하다. RAG가 포함된 예제는 `CLOVASTUDIO_API_KEY`와 정상 embedding API access도 필요하다. 실제 corpus에 존재하는 receipt/company로 바꾸어 실행한다.

```python
# A. SQL
retrieve("접수번호 20240102000001 계약금액 알려줘", connection)

# B. RAG
retrieve("삼성전자의 연구개발 분야를 설명해줘", connection, provider=provider)

# C. Correction
retrieve("접수번호 20240102000001의 최초 공시와 최신 정정을 알려줘", connection)

# D. Composite
retrieve("접수번호 20240102000001 계약금액과 정정 내용을 알려줘", connection)
```

Receipt가 DB에 없으면 결과가 비어 있을 수 있다. 문서의 번호는 interface 예시이지 존재 보장 fixture가 아니다.

## 환경과 DB 실행

Repository의 실제 Compose service는 `postgres`, image는 `pgvector/pgvector:pg15`다.

```powershell
Copy-Item .env.example .env  # 최초 1회 후 실제 local 비밀번호로 수정
docker compose up -d postgres
docker compose ps
```

Python 실행 shell에는 최소 `DATABASE_URL`이 필요하다. RAG query embedding에는 `CLOVASTUDIO_API_KEY`가 추가로 필요하다. 값 자체를 source, log, 문서, prompt에 기록하지 않는다. DB driver가 없다면 코드가 안내하는 `python -m pip install "psycopg[binary]>=3.2,<4"`가 필요하다. 현재 Python 코드에는 `.env` 자동 loader가 없으므로 shell/process environment로 전달한다.

기본 corpus namespace는 `periodic-full-v1`이다. 전체 embedding 적재를 다시 실행할 필요는 없다. `RerankCache`는 선택 사항이며 cache가 없어도 deterministic 결과를 계산한다.

## Frozen Retrieval V1 policy

다음은 freeze 대상이다.

- FTS ranking/depth 20
- CLOVA Studio Embedding v2 / bge-m3 / `embedding-v2` / 1,024d / exact cosine
- section paragraph chunking과 Vector depth 20
- original-query Title/Path channel depth 20
- unweighted RRF `k=60`, fusion depth 10
- deterministic reranker formula/depth 10
- company/receipt/date/report/namespace filter semantics
- explicit/high-confidence correction semantics

답변 품질 문제를 발견해도 HCX 계층에서 이를 수정하지 않는다. 원 질문, router 결과, evidence와 기대 근거를 Retrieval 담당자에게 전달하고 V2 experiment/regression 근거로 처리한다.

## Known limitations

- current three-channel Top-100에서도 no-candidate 질문 19개가 남는다.
- semantic mismatch가 남아 있다.
- 120문항은 internal/candidate benchmark이며, 신규 84문항은 independent human review 미완료다.
- reviewed 36이 더 강한 regression evidence다.
- reranker는 neural/LLM reranker가 아닌 deterministic feature reranker다.
- query embedding provider latency outlier가 있을 수 있다.
- Router V1은 unknown company alias와 복잡한 대화 문맥을 추론하지 않는다.
- unified RAG evidence는 현재 section title/path와 per-channel diagnostics를 top-level로 노출하지 않는다.

## 3번 담당이 건드리지 않아도 되는 것

- corpus embedding 재생성 또는 model 교체
- FTS/index tuning, HNSW, vector index 변경
- chunking 또는 parent aggregation
- query expansion/rewrite
- title/path channel tuning
- RRF weight/depth tuning
- deterministic reranker tuning
- benchmark gold/review status

이 작업들은 HCX answer-generation 연결에 필요하지 않다.

## 문제 발생 시 Retrieval 담당에게 전달할 정보

- original question과 대화에서 명시적으로 제공된 metadata
- `result.decision.to_dict()` 전체
- `result.errors`
- 각 evidence의 `route`, `receipt_no`, `source_file`, `source_locator`, `rank`, `score`
- `retrieval_sources`와 가능한 경우 `provenance`
- 예상했지만 누락된 evidence의 receipt/section/page/locator
- 실제 반환 evidence와 잘못되었다고 판단한 이유
- exception type, stack trace, DB/provider 상태
- 사용한 commit, corpus namespace, provider model/version

이 정보가 있으면 Router 문제, filter 문제, candidate miss, ranking 문제, adapter 문제를 분리해 재현할 수 있다.

## 관련 기준 문서

- `docs/question_router_v1.md`
- `docs/retrieval_v1_architecture.md`
- `docs/retrieval_v1_freeze.md`
- `reports/retrieval_v1_freeze_summary.md`
- `docs/retrieval_evidence_contract.json`
