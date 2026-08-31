"""
HCX 스모크 테스트 v2 — 실패 원인 반영 + 조합 진단

v1 실패 원인 (공식 문서 확인):
  HCX-007 은 thinking 기본값이 'low' 이며,
  추론 + Function calling / Structured Outputs 는 동시 사용 불가.
  → 두 기능 사용 시 thinking effort 를 명시적으로 'none' 으로 보내야 한다.

이 스크립트는 "되는 조합"을 실측해서 아키텍처 결정 근거를 만든다.

실행:
  export CLOVASTUDIO_API_KEY=nv-...
  python smoke_test.py
"""

from __future__ import annotations

import json
import traceback

from hcx_client import MODEL_LIGHT, MODEL_MAIN, NativeHCX, OpenAICompatHCX, Usage

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

TOTAL = Usage()
RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", note))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {note}")


def section(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


# ------------------------------------------------ 1. 추론 모델 응답 구조 확인
def test_thinking_structure() -> None:
    section("1. HCX-007 추론 응답 구조 (content vs thinkingContent)")
    hcx = NativeHCX()
    q = [
        {"role": "system", "content": "너는 한국 기업 공시를 분석하는 어시스턴트다."},
        {"role": "user", "content": "'사업보고서'가 무엇인지 한 문장으로 설명해."},
    ]

    for effort in ("none", "low"):
        try:
            r = hcx.chat(q, model=MODEL_MAIN, thinking_effort=effort,
                         max_completion_tokens=2048)
            TOTAL.add(r.usage)
            print(f"\n  --- effort={effort} ({r.latency_s:.2f}s, out={r.usage.completion_tokens}) ---")
            print(f"  content   : {r.text.strip()[:100]!r}")
            print(f"  thinking  : {r.thinking.strip()[:100]!r}")
            print(f"  finish    : {r.finish_reason!r}")
            record(f"추론 effort={effort}", bool(r.text.strip()))
        except Exception as exc:  # noqa: BLE001
            record(f"추론 effort={effort}", False, str(exc)[:180])


# ------------------------------------------- 2. Structured Outputs 조합 진단
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "company": {"type": "string", "description": "질의 대상 기업명. 없으면 빈 문자열"},
        "fiscal_year": {"type": "number", "description": "회계연도. 없으면 0"},
        "doc_type": {
            "type": "string",
            "description": "문서 유형",
            "enum": ["사업보고서", "반기보고서", "분기보고서", "주요사항보고서",
                     "거래소공시", "지분공시", "미상"],
        },
        "metric": {"type": "string", "description": "요구 지표"},
        "task_type": {
            "type": "string",
            "description": "과업 유형",
            "enum": ["단순조회", "비교연산", "복합추론", "답변불가"],
        },
    },
    "required": ["company", "fiscal_year", "doc_type", "metric", "task_type"],
}

SO_MESSAGES = [
    {"role": "system", "content": "사용자 질의를 지정된 JSON 스키마로만 변환한다. 설명 금지."},
    {"role": "user", "content": "삼성전자의 2025년 연결 기준 매출액은 얼마야?"},
]


def test_structured_output() -> None:
    section("2. Structured Outputs (thinking=none 강제) — 모델별")
    hcx = NativeHCX()
    for model in (MODEL_MAIN, MODEL_LIGHT):
        try:
            r = hcx.chat(
                SO_MESSAGES,
                model=model,
                response_format={"type": "json", "schema": INTENT_SCHEMA},
                max_completion_tokens=512,
            )
            TOTAL.add(r.usage)
            parsed = r.json()
            print(f"  {model:15s} {r.latency_s:5.2f}s  " + json.dumps(parsed, ensure_ascii=False))
            ok = bool(parsed.get("company")) and bool(parsed.get("task_type"))
            record(f"SO {model}", ok)
        except Exception as exc:  # noqa: BLE001
            record(f"SO {model}", False, str(exc)[:180])


# ------------------------------------------- 3. Function calling 조합 진단
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "resolve_company",
            "description": "기업 통용명을 정식 기업명과 corp_code 로 변환한다.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "사용자가 말한 기업명"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_filings",
            "description": "기업의 정기공시 문서 목록을 조회한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "corp_code": {"type": "string", "description": "기업 코드"},
                    "doc_type": {"type": "string", "description": "문서 유형"},
                    "base_year": {"type": "number", "description": "회계연도"},
                },
                "required": ["corp_code"],
            },
        },
    },
]

FC_MESSAGES = [
    {"role": "system", "content": "너는 공시 분석 에이전트다. 필요한 도구를 호출해라."},
    {"role": "user", "content": "현대차 2025년 사업보고서 찾아줘."},
]


def test_function_calling_native() -> None:
    section("3-A. Function calling — 네이티브 v3 (thinking=none 강제)")
    hcx = NativeHCX()
    for model in (MODEL_MAIN, MODEL_LIGHT):
        try:
            r = hcx.chat(FC_MESSAGES, model=model, tools=TOOLS, max_completion_tokens=2048)
            TOTAL.add(r.usage)
            if r.tool_calls:
                for c in r.tool_calls:
                    print(f"  {model:15s} 호출: {c['name']}({c['arguments']})")
                record(f"FC-native {model}", True, f"{len(r.tool_calls)}개")
            else:
                print(f"  {model:15s} 툴 호출 없음 → {r.text[:80]!r}")
                record(f"FC-native {model}", False, "toolCalls 없음")
        except Exception as exc:  # noqa: BLE001
            record(f"FC-native {model}", False, str(exc)[:180])


def test_function_calling_compat() -> None:
    section("3-B. Function calling — OpenAI 호환 (토큰 파라미터 2종 시도)")
    try:
        cli = OpenAICompatHCX()
    except Exception as exc:  # noqa: BLE001
        record("FC-compat 초기화", False, str(exc)[:180])
        return

    try:
        r = cli.chat(FC_MESSAGES, model=MODEL_MAIN, tools=TOOLS, max_tokens=2048)
        TOTAL.add(r.usage)
        if r.tool_calls:
            for c in r.tool_calls:
                print(f"  호출: {c['name']}({c['arguments']})")
            record("FC-compat", True, f"{len(r.tool_calls)}개")
        else:
            print(f"  툴 호출 없음 → {r.text[:80]!r}")
            record("FC-compat", False, "tool_calls 없음")
    except Exception as exc:  # noqa: BLE001
        record("FC-compat", False, str(exc)[:180])


# ------------------------------------------------------------ 4. 비용 실측
def report_cost() -> None:
    section("4. 토큰 사용량")
    print(f"  총 입력 토큰 : {TOTAL.prompt_tokens}")
    print(f"  총 출력 토큰 : {TOTAL.completion_tokens}")
    print("\n  [메모] thinking 을 켜면 출력 토큰이 급증한다.")
    print("         평가 질의당 비용 = 툴호출 N회 × 컨텍스트 + 최종답변(추론 포함)")
    print("         → effort 를 어디에 쓸지가 곧 예산 설계다.")


def main() -> None:
    print("HCX 스모크 테스트 v2\n")
    for fn in (
        test_thinking_structure,
        test_structured_output,
        test_function_calling_native,
        test_function_calling_compat,
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            record(fn.__name__, False, str(exc)[:180])
            traceback.print_exc(limit=2)

    report_cost()

    section("요약")
    for name, status, note in RESULTS:
        print(f"  {status:5s} {name:28s} {note}")

    print("\n  [해석 가이드]")
    print("  - 1번에서 effort=none 이 content 를 채우면 → 툴/스키마 단계는 none 고정")
    print("  - 2번 DASH-002 가 통과하면 → 질의 구조화는 저비용 모델로 처리 가능")
    print("  - 3-A/3-B 중 통과한 경로가 에이전트 루프의 기본 경로가 된다")


if __name__ == "__main__":
    main()