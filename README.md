# 공시 Agent (Disclosure Analyst) — finance-agami

제10회 미래에셋증권 AI Festival · 공시 Agent 트랙 제출물.
DART 공시 데이터(70개 기업, 2023.01~2026.03)를 기반으로 자연어 질의에
**검색 → 정형 조회/계산 → 근거 인용 답변**을 수행하는 HyperCLOVA X 에이전트.

## 평가용 API End-point

- **URL**: `http://<공인IP>/answer`  ← *(배포 완료 후 4번이 기입)*
- **Method**: `GET`
- **Health**: `http://<공인IP>/health`

### 요청

```
GET /answer?question_id={질의ID}&question={평가 질의}
```

인증 헤더 없음. 포트 80(HTTP).

### 응답 (application/json, 모든 필드 string)

```json
{
  "question_id": "Q-001",
  "question": "삼성전자의 2025년 연결 기준 매출액은 얼마야?",
  "retrieved_context": "### 툴:read_section ... (근거 원문, 구분 태그 '### 툴:' 로 연결)",
  "think_trace": "[라우팅] ... → [FC n] 툴호출 로그 → [검증] ...",
  "answer": "삼성전자의 2025년 연결 기준 매출액은 333조 6,059억 3,800만원입니다.\n(근거: 사업보고서 (2025.12), 접수번호 20260310002820, 섹션 III. 재무에 관한 사항 > 1. 요약재무정보)"
}
```

- 확인 불가 시 answer 에 `제공된 공시 데이터에서 확인할 수 없습니다` 문구 포함
- 내부 오류 시에도 5xx 없이 위 스키마로 응답 (한계 고지)
- 동일 `question_id` 재요청 시 캐시 응답 (멱등)
- 질의는 직렬 처리 (HCX 호출 제한 보호). 질의당 15~60초, 300초 타임아웃 내

## 시스템 구성

```
질의 → 가드레일(인젝션/투자의견 차단) → 라우팅(HCX-DASH-002)
     → 질의 구조화(HCX-007 Structured Output) → 기업 확정(별칭사전→NFC exact→트라이그램)
     → Function-Calling 루프(HCX-007, 최대 8스텝, 9개 툴, 인자 근거 검증)
     → 답변 생성(복합추론만 thinking) → 값 기반 검증(kr_number) → 응답 조립
```

- **LLM**: HyperCLOVA X 만 사용 (HCX-007 / HCX-DASH-002, CLOVA Studio)
- **데이터**: PostgreSQL 15 — companies(70) / disclosures(4,204) / contracts(1,106) /
  correction_links(461) / document_sections(112,733, FTS)
- **툴 9종**: resolve_company, get_doc_matrix, list_filings, list_sections, read_section,
  search_chunks, query_events, get_history, calc(결정론 계산·금액표기)

설계 원칙: *결정론으로 풀 수 있는 것은 LLM 에 시키지 않는다* — 기업 식별, 단위 환산,
증감률 계산, 정정 최신본 판정, 결측 문서 사유, 모호성 역질문을 전부 코드가 담당.
상세: `decisions.md`, `tool_contract_v0.md`.

## 환경 구성

### 요구사항
- Python 3.12, Docker (PostgreSQL 15)
- CLOVA Studio API 키

### 1. DB 준비 (둘 중 하나)

**A. 덤프 복원 (권장, 원본 코퍼스 불필요)**
```bash
cd mirae-asset-feature-retrieval-v1/mirae-asset-feature-retrieval-v1
cp .env.example .env            # POSTGRES_PASSWORD 설정
docker compose up -d
docker exec -i <컨테이너명> psql -U disclosure disclosure < dump.sql
```

**B. 원본 적재**
```bash
# 팀레포에 data/(코퍼스), role2_final/, analysis_results/ 배치 후
python src/db/load_postgres.py --database-url $DATABASE_URL
python load_sections.py sections_full.jsonl
```

### 2. 환경변수 (`.env`, 루트)
```
CLOVASTUDIO_API_KEY=nv-...
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=disclosure
POSTGRES_USER=disclosure
POSTGRES_PASSWORD=...
TOOLS_BACKEND=real
```

### 3. 실행

```bash
pip install -r requirements.txt
python integration_check.py                 # DB/어댑터 점검 (LLM 미사용)
uvicorn server:app --host 0.0.0.0 --port 80
```

또는 Docker:
```bash
docker build -t disclosure-agent .
docker run -d --env-file .env --network host --name disclosure-agent disclosure-agent
```

### 4. 검증

```bash
curl "http://localhost/health"
curl -G "http://localhost/answer" --data-urlencode "question_id=T-1" \
     --data-urlencode "question=삼성전자 2025년 연결 기준 매출액은?"
python eval/harness.py --api http://localhost     # 자체 평가셋 12문항
```

## 디렉토리

```
agent.py            에이전트 루프 (라우팅/구조화/FC/검증/조립)
server.py           평가용 FastAPI 서버
hcx_client.py       HyperCLOVA X 클라이언트 (429 백오프, thinking/FC/SO 분리)
tools_real.py       9-툴 계약의 PostgreSQL 구현 (어댑터)
tools_mock.py       동일 계약의 mock (개발/회귀용) + calc 구현
kr_number.py        한국어 금액 파서 (값 기반 검증)
export_sections.py  정기공시 원문 → document_sections 변환
load_sections.py    섹션 적재기
ground_truth.py     실데이터 정답 라벨링 도구
eval/harness.py     자체 평가 하네스 (지표별/함정별 채점, 회귀 비교)
eval/questions.jsonl 평가셋
decisions.md        설계 결정·튜닝 로그
mirae-asset-feature-retrieval-v1/  DB 스키마·적재 (2번 담당)
```

## 팀

finance-agami — 데이터 파이프라인(1번) / 검색·DB(2번) / LLM·에이전트·검증(3번) / API·인프라(4번)
