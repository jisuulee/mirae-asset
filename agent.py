"""
공시 Agent 루프 뼈대 v0

파이프라인 (실측 검증된 호출 패턴만 사용):
  0. 가드레일   : 인젝션/투자의견/미래예측 차단 (규칙 기반, LLM 이전)
  1. 라우팅     : DASH-002 플레인 텍스트 분류 (0.7s)
  2. 구조화     : HCX-007 + Structured Output, effort=none (1.6s)
  3. 사전 해석  : 별칭 사전 → resolve_company (결정론 우선)
  4. FC 루프    : HCX-007 + tools, effort=none, 최대 8스텝
  5. 답변 생성  : HCX-007, 복합추론이면 effort=low, 아니면 none
  6. 검증       : 답변 수치가 근거에 실재하는지 (v0: 숫자 대조)
  7. 조립       : {question_id, question, retrieved_context, think_trace, answer}

교체 지점: tools_mock.REGISTRY → 2번(DB) 실구현으로 갈아끼우면 끝.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from hcx_client import MODEL_LIGHT, MODEL_MAIN, NativeHCX, OpenAICompatHCX, Usage

TOOLS_BACKEND = os.environ.get("TOOLS_BACKEND", "mock").lower()

# 툴 백엔드 전환: TOOLS_BACKEND=real 이면 실 DB 어댑터, 기본은 mock
if TOOLS_BACKEND == "real":
    from tools_real import ALIASES, AMBIGUOUS, UNIVERSE, dispatch
    from tools_real import _load_universe as _init_backend
else:
    from tools_mock import ALIASES, AMBIGUOUS, UNIVERSE, dispatch
    _init_backend = lambda: None  # noqa: E731

try:
    from kr_number import extract_amounts  # 값 기반 검증 (루트에 kr_number.py 필요)
except ImportError:
    extract_amounts = None

MAX_STEPS = 8


def _grounded_value(v: int, blob: str) -> bool:
    """값이 근거 텍스트에 '독립된 수'로 존재하는지. 부분 문자열 오탐 방지
    (예: 33,605,900 은 333,605,900 의 부분이므로 불인정)."""
    for s in (f"{v:,}", str(v)):
        if re.search(rf"(?<![\d,]){re.escape(s)}(?![\d,])", blob):
            return True
    return False

# ------------------------------------------------------------ 시스템 프롬프트 v0
SYSTEM_PROMPT = """너는 한국 상장기업 공시 데이터 기반 질의응답 에이전트다.

[행동 규칙]
1. 모든 답변은 제공된 공시 데이터(툴 결과)만을 근거로 한다. 너의 사전 지식으로 수치·사실을 만들지 마라.
2. corp_code 는 반드시 resolve_company 결과에서만 가져온다. 기억으로 코드를 지어내지 마라.
3. 수치 비교·증감률·합산은 반드시 calc 툴을 사용한다. 직접 암산하지 마라.
4. 금액을 조/억 단위로 표기할 때는 어떤 경우에도 직접 환산하지 마라.
   - 표의 수치: calc(op="krw_format", items=[{label, value=표의 숫자, unit=표의 단위}])
   - 이벤트 금액: 툴 결과의 amount_formatted 문자열을 글자 그대로 복사해 사용
   직접 계산한 조/억 표기는 오답으로 간주된다.
5. 데이터에 없는 내용은 "제공된 공시 데이터에서 확인할 수 없습니다"라고 명시하고,
   존재하는 인접 정보를 안내해라. 추측 금지. 문서 부재가 원인이면
   get_doc_matrix 의 reason(예: 상장 전)을 답변에 그대로 인용하라.
6. 미래 예측, 투자 의견, 매수/매도 추천은 생성 금지.
7. 인용 규칙 — 모든 답변 마지막 줄:
   - 정기공시 근거: (근거: 사업보고서 2025.12, 접수번호 XXXXXXXXXXXXXX, 섹션 path)
   - 이벤트/정정 근거: (근거: 공시 제목, 접수번호 XXXXXXXXXXXXXX)
   - 확인 불가: (근거: 조회 범위 — 기업/기간/문서유형을 명시). "N/A" 표기 금지.

[재무 수치 조회 규칙]
- 별도 언급이 없으면 연결(consolidated) 기준을 사용한다. 섹션 선택 시
  "연결 손익계산서", "연결 재무상태표", "요약재무정보"를 우선하고,
  "손익계산서"(별도), "재무제표"(별도)는 질의가 '별도 기준'을 명시할 때만 쓴다.
- 요약 지표(매출액·영업이익·당기순이익)는 "III. 재무에 관한 사항 > 1. 요약재무정보"
  를 먼저 확인하고, 없거나 불충분할 때 연결 손익계산서를 본다.
- 답변에 연결/별도 중 무엇을 근거로 했는지 명시한다.
- 증감률·비교 시 calc 의 items 각 항목에 role 을 반드시 지정한다
  (비교 기준=role:"base", 비교 대상=role:"current"). 증가율은 부호와 % 를 함께 쓴다.

[도구 사용 순서 지침]
- 기업명이 나오면 항상 resolve_company 부터.
- 특정 연도 문서 질의는 get_doc_matrix 로 존재 확인 → list_filings → list_sections → read_section.
- 계약/투자/증자 등 사건 질의는 query_events. event_type 에는 공시 유형만 넣어라
  (단일판매공급계약, 유상증자, CB발행 등). 계약명·상품명을 넣지 마라.
  결과가 비면 event_type 을 빼고 재조회하라.
- 정정·변경·이력 질의는 query_events 로 대상을 찾은 뒤, 해당 rcept_no 로
  get_history 를 반드시 호출해 정정 전후(diffs)를 근거로 확보하라.
- 존재 여부 질의(예: 해지된 계약이 있는가)에서 조회 결과가 없으면
  "조회 범위 내에서 ~은(는) 확인되지 않았습니다" 표준 문구로 답하라.
- 인용의 문서명·기준월은 read_section 또는 list_filings 가 반환한
  doc_title(예: 사업보고서 (2025.12))을 그대로 사용하라. 접수일로 추측하지 마라.
- 목차로 못 찾는 주제는 search_chunks 폴백.
"""

TOOL_SPECS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "resolve_company",
        "description": "기업 통용명을 corp_code 로 변환. 기업명이 등장하면 가장 먼저 호출.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "사용자가 말한 기업명"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "get_doc_matrix",
        "description": "기업×연도×문서유형 존재 여부 확인. 문서 부재 시 답변 불가 판단의 근거.",
        "parameters": {"type": "object", "properties": {
            "corp_code": {"type": "string"},
            "base_year": {"type": "number"},
            "doc_type": {"type": "string"}},
            "required": ["corp_code"]}}},
    {"type": "function", "function": {
        "name": "list_filings",
        "description": "정기공시 문서 목록 조회. base_year 는 사업연도 기준.",
        "parameters": {"type": "object", "properties": {
            "corp_code": {"type": "string"},
            "doc_type": {"type": "string", "description": "사업보고서|반기보고서|분기보고서"},
            "base_year": {"type": "number"}},
            "required": ["corp_code"]}}},
    {"type": "function", "function": {
        "name": "list_sections",
        "description": "문서 목차(path)와 글자수 조회.",
        "parameters": {"type": "object", "properties": {
            "doc_id": {"type": "string"}}, "required": ["doc_id"]}}},
    {"type": "function", "function": {
        "name": "read_section",
        "description": "섹션 원문 읽기. 표는 마크다운. path 는 list_sections 결과를 그대로 사용.",
        "parameters": {"type": "object", "properties": {
            "doc_id": {"type": "string"}, "path": {"type": "string"}},
            "required": ["doc_id", "path"]}}},
    {"type": "function", "function": {
        "name": "search_chunks",
        "description": "키워드/의미 기반 청크 검색 폴백. 목차로 위치를 못 찾을 때 사용.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "top_k": {"type": "number"},
            "filters": {"type": "object", "properties": {
                "corp_code": {"type": "string"}, "base_year": {"type": "number"},
                "doc_type": {"type": "string"}}}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "query_events",
        "description": "계약·증자·CB·시설투자 등 사건 공시 정형 조회.",
        "parameters": {"type": "object", "properties": {
            "filters": {"type": "object", "properties": {
                "corp_code": {"type": "string"},
                "event_type": {"type": "string",
                               "description": ("단일판매공급계약|유상증자|CB발행|BW발행|"
                                               "EB발행|신규시설투자|타법인주식취득 등. "
                                               "부분 문자열 매칭 지원")},
                "date_from": {"type": "string"}, "date_to": {"type": "string"}}},
            "is_latest": {"type": "boolean"}},
            "required": ["filters"]}}},
    {"type": "function", "function": {
        "name": "get_history",
        "description": "접수번호의 정정 체인과 변경 내용(diff) 조회.",
        "parameters": {"type": "object", "properties": {
            "rcept_no": {"type": "string"}}, "required": ["rcept_no"]}}},
    {"type": "function", "function": {
        "name": "calc",
        "description": ("결정론 계산기. 비교/합산/차이/증감률/순위와 금액 표기 변환은 "
                        "반드시 이 툴로. krw_format 은 표의 숫자+단위를 넣으면 "
                        "'333조 6,059억원' 형태의 정확한 표기를 돌려준다."),
        "parameters": {"type": "object", "properties": {
            "op": {"type": "string",
                   "enum": ["compare", "sum", "diff", "growth_rate", "rank", "krw_format"]},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "label": {"type": "string"}, "value": {"type": "number"},
                "role": {"type": "string", "enum": ["current", "base"],
                         "description": ("growth_rate 필수: 비교 대상=current, "
                                         "비교 기준=base")},
                "unit": {"type": "string",
                         "description": "krw_format 전용: 원|천원|만원|백만원|억원|조원"}},
                "required": ["label", "value"]}}},
            "required": ["op", "items"]}}},
]

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {"type": "array", "items": {"type": "string"},
                      "description": "질의에 등장한 기업명들 (원문 그대로)"},
        "fiscal_year": {"type": "number", "description": "회계연도. 없으면 0"},
        "doc_type": {"type": "string",
                     "enum": ["사업보고서", "반기보고서", "분기보고서", "주요사항보고서",
                              "거래소공시", "지분공시", "미상"]},
        "metric": {"type": "string", "description": "요구 지표/주제"},
        "task_type": {"type": "string",
                      "enum": ["단순조회", "비교연산", "복합추론", "답변불가"]},
    },
    "required": ["companies", "fiscal_year", "doc_type", "metric", "task_type"],
}

ABSTAIN_PHRASE = "제공된 공시 데이터에서 확인할 수 없습니다"

# ------------------------------------------------------------------ 가드레일
INJECTION_PATTERNS = [
    r"이전\s*(지시|명령|프롬프트).{0,10}(무시|잊)",
    r"(system|시스템)\s*(프롬프트|prompt).{0,15}(출력|보여|알려|공개)",
    r"ignore\s+(all\s+)?previous",
    r"너의\s*(규칙|지시|설정).{0,10}(무시|해제)",
    r"(개발자|관리자)\s*모드",
]
FORBIDDEN_PATTERNS = [
    r"(살까|사도\s*돼|사야|매수|매도|팔까|투자해도|투자할까)",
    r"(주가|목표가).{0,12}(전망|예측|오를|내릴|얼마나\s*갈)",
    r"(오를까|내릴까|상승할까|하락할까)",
]


def guard_input(question: str) -> str | None:
    """차단 사유 문자열 반환. None 이면 통과."""
    for p in INJECTION_PATTERNS:
        if re.search(p, question, re.IGNORECASE):
            return "injection"
    for p in FORBIDDEN_PATTERNS:
        if re.search(p, question):
            return "forbidden_topic"
    return None


GUARD_ANSWERS = {
    "injection": (
        "요청하신 내용은 수행할 수 없습니다. 저는 제공된 공시 데이터에 근거한 "
        "질의응답만 지원합니다. 기업 공시에 대해 질문해 주세요."),
    "forbidden_topic": (
        "투자 의견이나 미래 주가 예측은 제공하지 않습니다. "
        "공시에 근거한 사실 정보(실적, 계약, 투자, 지분 변동 등)는 답변할 수 있습니다."),
}

# ------------------------------------------------------------- 결정론 resolve
def _nrm(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", s or "").strip().lower().replace(" ", "")


def resolve_deterministic(companies: list[str]) -> list[dict[str, Any]]:
    """LLM 이전에 기업 확정: 사전 → 정규화 exact → 백엔드 resolve_company 폴백.
    폴백까지 실패해야만 unknown (조기 abstain 오판 방지)."""
    out = []
    for name in companies:
        key = _nrm(name)
        if key in AMBIGUOUS:
            out.append({"input": name, "status": "ambiguous",
                        "candidates": [UNIVERSE[c]["corp_name"] for c in AMBIGUOUS[key]]})
            continue
        if key in ALIASES:
            code = ALIASES[key]
            out.append({"input": name, "status": "resolved",
                        "corp_code": code, "corp_name": UNIVERSE[code]["corp_name"]})
            continue
        hit = next((c for c, i in UNIVERSE.items()
                    if _nrm(i["corp_name"]) == key), None)
        if hit:
            out.append({"input": name, "status": "resolved",
                        "corp_code": hit, "corp_name": UNIVERSE[hit]["corp_name"]})
            continue
        # 백엔드 폴백: real 이면 트라이그램 유사도까지 동원됨
        r = dispatch("resolve_company", {"name": name})
        if r.get("ok"):
            d = r["data"]
            out.append({"input": name, "status": "resolved",
                        "corp_code": d["corp_code"], "corp_name": d["corp_name"],
                        "matched_by": d.get("matched_by", "backend")})
        elif (r.get("error") or {}).get("code") == "AMBIGUOUS_COMPANY":
            out.append({"input": name, "status": "ambiguous",
                        "candidates": [c["corp_name"] for c in r.get("candidates", [])]})
        else:
            out.append({"input": name, "status": "unknown"})
    return out


# ------------------------------------------------------------------ 검증 v0
def verify_numbers(answer: str, context: str, question: str = "") -> list[str]:
    """답변의 4자리 이상 숫자가 근거에 존재하는지 대조. 미존재 숫자 목록 반환.
    제외: 접수번호(14자리+), 연도(1900~2100), 질문에 이미 등장한 숫자.
    비고: 조/억 환산 표기는 calc(krw_format) 결과가 근거에 포함되므로 대조 가능."""
    def norm(s: str) -> str:
        return s.replace(",", "")

    ctx = norm(context)
    qn = norm(question)
    missing = []
    for num in re.findall(r"\d[\d,]{3,}", answer):
        n = norm(num)
        if len(n) >= 14:  # 접수번호
            continue
        if len(n) == 4 and 1900 <= int(n) <= 2100:  # 연도
            continue
        if n in qn:  # 질문이 준 숫자
            continue
        if n not in ctx:
            missing.append(num)

    # 값 기반 검증: 답변의 한국어 금액 표현을 원 단위 값으로 파싱해 근거와 대조.
    # 근거의 raw 숫자에는 단위 배수(천/백만/억) 후보를 곱해 관대하게 매칭 →
    # 오탐(false positive)은 억제하고, 잘못 환산된 금액(자릿수 뒤섞임)은 확실히 잡는다.
    if extract_amounts is not None:
        ctx_vals: set[int] = set(extract_amounts(context))
        for run in re.findall(r"\d[\d,]{3,}", context):
            v = int(norm(run))
            if len(norm(run)) >= 14:
                continue
            for mult in (1, 10**3, 10**6, 10**8):
                ctx_vals.add(v * mult)
        for v in extract_amounts(answer):
            if v >= 10_000 and v not in ctx_vals:
                missing.append(f"금액값({v:,}원)")
    return missing


# ================================================================== 에이전트
@dataclass
class Trace:
    lines: list[str] = field(default_factory=list)

    def add(self, s: str) -> None:
        self.lines.append(s)
        print(f"    | {s}")

    def text(self) -> str:
        return "\n".join(self.lines)


class DisclosureAgent:
    def __init__(self) -> None:
        _init_backend()                   # real 백엔드면 DB 에서 유니버스/별칭 로드
        print(f"  [backend] {TOOLS_BACKEND.upper()} · 기업 {len(UNIVERSE)}개")
        if TOOLS_BACKEND != "real":
            print("  [경고] mock 백엔드입니다. 실 DB 평가는 TOOLS_BACKEND=real 필요")
        self.light = NativeHCX()          # DASH 라우팅용 (네이티브)
        self.fc = OpenAICompatHCX()       # FC 루프용 (호환)
        self.usage = Usage()

    # ---- 1. 라우팅
    def route(self, question: str, trace: Trace) -> str:
        r = self.light.chat(
            [{"role": "user", "content":
              "다음 질의를 분류해라. 카테고리: [단순조회, 비교연산, 복합추론, 답변불가]\n"
              f"질의: {question}\n카테고리 이름만 출력."}],
            model=MODEL_LIGHT, max_completion_tokens=20)
        self.usage.add(r.usage)
        cat = r.text.strip()
        cat = next((c for c in ("단순조회", "비교연산", "복합추론", "답변불가") if c in cat),
                   "단순조회")
        trace.add(f"[라우팅] {cat} (DASH-002)")
        return cat

    # ---- 2. 구조화
    def structure(self, question: str, trace: Trace) -> dict[str, Any]:
        r = self.light.chat(
            [{"role": "system", "content": "사용자 질의를 JSON 스키마로만 변환한다."},
             {"role": "user", "content": question}],
            model=MODEL_MAIN,
            response_format={"type": "json", "schema": INTENT_SCHEMA},
            max_completion_tokens=512)
        self.usage.add(r.usage)
        try:
            intent = r.json()
        except (json.JSONDecodeError, ValueError):
            intent = {"companies": [], "fiscal_year": 0, "doc_type": "미상",
                      "metric": "", "task_type": "단순조회"}
        trace.add(f"[구조화] {json.dumps(intent, ensure_ascii=False)}")
        return intent

    # ---- 4. FC 루프
    def run_tools(self, question: str, resolved: list[dict[str, Any]],
                  trace: Trace) -> tuple[list[dict[str, Any]], str, list[str]]:
        """모델이 툴 호출을 멈출 때까지 최대 MAX_STEPS. 근거(context)와 결측 문서 수집."""
        evidence: list[str] = []
        missing_docs: list[str] = []
        grounding_blob = ""   # 지금까지 수집된 모든 툴 결과 원문 (인자 검증용)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
             f"질의: {question}\n\n[사전 해석된 기업]\n"
             f"{json.dumps(resolved, ensure_ascii=False)}\n\n"
             "필요한 도구를 호출해 근거를 수집해라. 수치 답변이 필요하면 표에서 값을 "
             "읽은 뒤 calc(krw_format) 로 표기 문자열까지 만들어라. "
             "근거가 충분하면 도구 호출을 멈춰라."},
        ]
        for step in range(1, MAX_STEPS + 1):
            try:
                r = self.fc.chat(messages, model=MODEL_MAIN, tools=TOOL_SPECS,
                                 max_tokens=2048)
            except Exception as exc:  # noqa: BLE001
                # API 오류(429/400 등)로 루프가 죽으면 문항 전체를 잃는다.
                # 수집된 근거가 있으면 그것으로 생성 단계로 강등한다.
                trace.add(f"[FC {step}] 호출 실패({type(exc).__name__}: {str(exc)[:80]}) "
                          f"→ 수집된 근거 {len(evidence)}건으로 생성 진행")
                break
            self.usage.add(r.usage)
            if not r.tool_calls:
                trace.add(f"[FC {step}] 도구 호출 종료")
                break
            # assistant 턴 재구성 (tool_calls 포함)
            messages.append({
                "role": "assistant", "content": r.text or None,
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"],
                                             "arguments": c["arguments"]}}
                               for c in r.tool_calls]})
            for c in r.tool_calls:
                # calc(krw_format) 인자 근거 검증: LLM 전사 오류(자릿수 누락 등) 차단
                intercepted = None
                if c["name"] == "calc":
                    try:
                        args = (json.loads(c["arguments"])
                                if isinstance(c["arguments"], str) else c["arguments"]) or {}
                    except json.JSONDecodeError:
                        args = {}
                    if args.get("op") == "krw_format":
                        for it in args.get("items", []):
                            try:
                                v = int(float(it.get("value")))
                            except (TypeError, ValueError):
                                continue
                            if not _grounded_value(v, grounding_blob):
                                intercepted = {"ok": False, "error": {
                                    "code": "ARG_NOT_GROUNDED",
                                    "message": (f"value {v:,} 는 수집된 근거 원문에 없는 "
                                                "수치입니다. 표의 수치를 다시 읽고 "
                                                "정확히 그대로 옮겨 재호출하세요.")}}
                                break
                result = intercepted or dispatch(c["name"], c["arguments"])
                args_short = str(c["arguments"])[:90]
                ok = result.get("ok")
                if ok:
                    grounding_blob += json.dumps(result.get("data", {}),
                                                 ensure_ascii=False)
                trace.add(f"[FC {step}] {c['name']}({args_short}) → "
                          f"{'ok' if ok else result['error']['code']}")
                if ok and c["name"] in ("read_section", "search_chunks",
                                        "query_events", "get_history", "calc",
                                        "get_doc_matrix", "list_filings"):
                    evidence.append(
                        f"### 툴:{c['name']} 인자:{args_short}\n"
                        + json.dumps(result["data"], ensure_ascii=False, indent=1))
                if ok and c["name"] == "get_doc_matrix":
                    for row in result["data"].get("rows", []):
                        if not row.get("exists"):
                            missing_docs.append(
                                f"{row.get('base_year')}년 {row.get('doc_type')} 없음"
                                f" (사유: {row.get('reason', '미상')})")
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
        return messages, "\n\n".join(evidence), missing_docs

    # ---- 5. 답변 생성
    def synthesize(self, question: str, context: str, task_type: str,
                   trace: Trace) -> str:
        effort = "low" if task_type == "복합추론" else "none"
        trace.add(f"[생성] effort={effort}")
        r = self.light.chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content":
              f"질의: {question}\n\n[수집된 근거]\n{context if context else '(근거 없음)'}\n\n"
              "위 근거만으로 답하라. 근거가 부족하면 "
              f"'{ABSTAIN_PHRASE}' 로 시작하는 한계 고지를 하라. "
              "마지막 줄에 (근거: 문서명, 접수번호, 섹션) 형식으로 출처를 표기하라."}],
            model=MODEL_MAIN, thinking_effort=effort,
            max_completion_tokens=4096 if effort == "low" else 1536)
        self.usage.add(r.usage)
        return r.text.strip()

    # ---- 전체 파이프라인
    def answer(self, question_id: str, question: str) -> dict[str, Any]:
        trace = Trace()

        # 0. 가드레일
        if reason := guard_input(question):
            trace.add(f"[가드] 차단: {reason}")
            return self._envelope(question_id, question, "", trace,
                                  GUARD_ANSWERS[reason])

        # 1~2. 라우팅 + 구조화
        task_type = self.route(question, trace)
        intent = self.structure(question, trace)

        # 3. 결정론 resolve
        resolved = resolve_deterministic(intent.get("companies", []))
        trace.add(f"[사전해석] {json.dumps(resolved, ensure_ascii=False)}")

        # 구조화가 복합추론으로 봤으면 effort 상향 (라우터와 합의: 둘 중 하나라도)
        if intent.get("task_type") == "복합추론":
            task_type = "복합추론"

        # 모호성만 있는 질의 → LLM 없이 결정론 역질문 (정확도 100% 보장)
        ambigs = [r for r in resolved if r["status"] == "ambiguous"]
        if ambigs and not any(r["status"] == "resolved" for r in resolved):
            a = ambigs[0]
            ans = (f"'{a['input']}' 에 해당하는 기업이 여러 곳입니다: "
                   f"{', '.join(a['candidates'])}. "
                   "어느 기업에 대한 질문인지 특정해 주시면 해당 기업의 공시 기준으로 "
                   "답변드리겠습니다.")
            trace.add("[사전해석] 모호성 → 후보 제시 및 역질문 (결정론)")
            return self._envelope(question_id, question, "", trace, ans)

        # 대상 외 기업 조기 abstain
        if resolved and all(r["status"] == "unknown" for r in resolved):
            ans = (f"{ABSTAIN_PHRASE}. "
                   f"'{', '.join(r['input'] for r in resolved)}' 은(는) 평가 대상 "
                   "70개 기업에 포함되지 않습니다. 대상 기업에 대해 질문해 주세요.")
            return self._envelope(question_id, question, "", trace, ans)

        # 4. FC 루프
        _, context, missing_docs = self.run_tools(question, resolved, trace)

        # 결측 문서가 있으면 생성 지시문에 결정론 주입 (모델 재량에 맡기지 않음)
        if missing_docs:
            trace.add(f"[결측] {missing_docs}")
            context += ("\n\n[존재하지 않는 문서 — 아래 항목과 사유를 답변에 그대로 명시하고, "
                        f"해당 항목에 대해 반드시 '{ABSTAIN_PHRASE}' 문구를 포함해 "
                        "한계를 고지하라]\n"
                        + "\n".join(f"- {m}" for m in missing_docs))

        # 5. 생성 (API 오류 시에도 문항을 잃지 않는다)
        try:
            answer = self.synthesize(question, context, task_type, trace)
        except Exception as exc:  # noqa: BLE001
            trace.add(f"[생성] 실패({type(exc).__name__}) → 한계 고지로 강등")
            answer = (f"{ABSTAIN_PHRASE}. 답변 생성 중 일시적 오류가 발생했습니다. "
                      + (f"수집된 근거 접수번호: "
                         f"{', '.join(sorted(set(re.findall(r'\\d{14}', context))))}"
                         if context else ""))

        # 6. 검증
        missing = verify_numbers(answer, context, question)
        if missing:
            trace.add(f"[검증] 근거 미확인 수치 {missing} → 재생성 1회")
            try:
                answer = self.synthesize(
                    question,
                    context + f"\n\n[경고] 이전 답변의 수치 {missing} 는 근거에 없다. "
                              "근거에 있는 수치만 사용하거나 한계를 고지하라.",
                    task_type, trace)
            except Exception as exc:  # noqa: BLE001
                trace.add(f"[검증] 재생성 실패({type(exc).__name__}) → 1차 답변 유지")
            still = verify_numbers(answer, context, question)
            trace.add(f"[검증] 재검증 {'통과' if not still else f'실패 {still}'}")
        else:
            trace.add("[검증] 수치 대조 통과")

        return self._envelope(question_id, question, context, trace, answer)

    @staticmethod
    def _envelope(question_id: str, question: str, context: str,
                  trace: Trace, answer: str) -> dict[str, Any]:
        return {
            "question_id": question_id,
            "question": question,
            "retrieved_context": context,
            "think_trace": trace.text(),
            "answer": answer,
        }