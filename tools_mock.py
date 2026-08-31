"""
mock 툴 — tool_contract_v0.md 스키마 그대로 구현한 가짜 툴.

목적: 1번(파서)·2번(DB)이 완성되기 전에 에이전트 루프를 개발·테스트하기 위함.
나중에 2번의 실구현으로 교체할 때 이 파일의 함수 시그니처가 곧 계약이다.

픽스처 시나리오 (자체 평가셋 함정 유형을 미리 반영):
  - 삼성전자: 정상 케이스 (사업보고서 + 공급계약 + 정정 체인)
  - 시프트업: 2024 상장 → 2023 문서 없음 (abstain 유도)
  - 두산*: 모호성 케이스 ("두산" 만 말하면 3사 충돌)
"""

from __future__ import annotations

import json
from typing import Any

# ----------------------------------------------------------------- 유니버스
UNIVERSE: dict[str, dict[str, Any]] = {
    "00126380": {"corp_name": "삼성전자", "stock_code": "005930",
                 "sector": "반도체·전자부품", "market": "KOSPI"},
    "00164742": {"corp_name": "현대자동차", "stock_code": "005380",
                 "sector": "자동차·모빌리티", "market": "KOSPI"},
    "01515323": {"corp_name": "LG에너지솔루션", "stock_code": "373220",
                 "sector": "2차전지", "market": "KOSPI"},
    "01721461": {"corp_name": "시프트업", "stock_code": "462870",
                 "sector": "게임", "market": "KOSPI"},
    "01390344": {"corp_name": "두산로보틱스", "stock_code": "454910",
                 "sector": "로봇", "market": "KOSPI"},
    "00159724": {"corp_name": "두산에너빌리티", "stock_code": "034020",
                 "sector": "원전", "market": "KOSPI"},
    "01223800": {"corp_name": "두산퓨얼셀", "stock_code": "336260",
                 "sector": "신재생에너지", "market": "KOSPI"},
}
# 주의: corp_code 는 mock 용 임의값 포함. 실데이터 적재 시 1번 산출물로 교체.

ALIASES: dict[str, str] = {
    "삼성전자": "00126380", "삼전": "00126380", "samsung": "00126380",
    "현대차": "00164742", "현대자동차": "00164742", "현차": "00164742",
    "lg에너지솔루션": "01515323", "엘지엔솔": "01515323", "lg엔솔": "01515323",
    "시프트업": "01721461",
    "두산로보틱스": "01390344", "두산에너빌리티": "00159724", "두산퓨얼셀": "01223800",
}

AMBIGUOUS: dict[str, list[str]] = {
    "두산": ["01390344", "00159724", "01223800"],
}

# ------------------------------------------------------------------- 문서
DOC_MATRIX: dict[tuple[str, int, str], dict[str, Any]] = {
    ("00126380", 2023, "사업보고서"): {"exists": True, "doc_id": "periodic_20240312000736"},
    ("00126380", 2024, "사업보고서"): {"exists": True, "doc_id": "periodic_20250311000523"},
    ("00126380", 2025, "사업보고서"): {"exists": True, "doc_id": "periodic_20260310002820"},
    ("01721461", 2023, "사업보고서"): {"exists": False, "reason": "2024년 상장 (상장 전)"},
    ("01721461", 2024, "사업보고서"): {"exists": True, "doc_id": "periodic_20250320001111"},
    ("01721461", 2025, "사업보고서"): {"exists": True, "doc_id": "periodic_20260315002222"},
}

FILINGS: dict[str, dict[str, Any]] = {
    "periodic_20260310002820": {
        "doc_id": "periodic_20260310002820", "rcept_no": "20260310002820",
        "rcept_dt": "2026-03-10", "corp_code": "00126380",
        "doc_type": "사업보고서", "base_year": 2025, "base_month": 12,
        "is_correction": False, "is_latest": True, "title": "사업보고서 (2025.12)",
    },
    "periodic_20260315002222": {
        "doc_id": "periodic_20260315002222", "rcept_no": "20260315002222",
        "rcept_dt": "2026-03-15", "corp_code": "01721461",
        "doc_type": "사업보고서", "base_year": 2025, "base_month": 12,
        "is_correction": False, "is_latest": True, "title": "사업보고서 (2025.12)",
    },
}

SECTIONS: dict[str, list[dict[str, Any]]] = {
    "periodic_20260310002820": [
        {"path": "I. 회사의 개요 > 1. 회사의 개요", "n_chars": 4200, "n_tables": 1, "is_stub": False},
        {"path": "II. 사업의 내용 > 2. 주요 제품 및 서비스", "n_chars": 1008, "n_tables": 3, "is_stub": False},
        {"path": "III. 재무에 관한 사항 > 1. 요약재무정보", "n_chars": 3120, "n_tables": 4, "is_stub": False},
        {"path": "III. 재무에 관한 사항 > 8. 기타 재무에 관한 사항", "n_chars": 42, "n_tables": 0, "is_stub": True},
    ],
    "periodic_20260315002222": [
        {"path": "II. 사업의 내용 > 1. 사업의 개요", "n_chars": 820, "n_tables": 1, "is_stub": False},
    ],
}

SECTION_TEXT: dict[tuple[str, str], dict[str, Any]] = {
    ("periodic_20260310002820", "III. 재무에 관한 사항 > 1. 요약재무정보"): {
        "text_md": (
            "1. 요약재무정보 (연결)\n\n(단위: 백만원)\n\n"
            "| 구분 | 제57기(2025) | 제56기(2024) |\n|---|---|---|\n"
            "| 매출액 | 333,605,900 | 300,870,903 |\n"
            "| 영업이익 | 41,210,500 | 32,725,961 |\n"
            "| 당기순이익 | 34,451,300 | 26,408,159 |\n"
        ),
        "rcept_no": "20260310002820",
        "unit_hints": [{"table_idx": 0, "unit": "백만원"}],
        "fs_scope_hints": [{"table_idx": 0, "scope": "연결"}],
    },
    ("periodic_20260310002820", "II. 사업의 내용 > 2. 주요 제품 및 서비스"): {
        "text_md": (
            "2. 주요 제품 및 서비스\n\n당사는 TV, 냉장고, 스마트폰 등 완제품과 "
            "DRAM, NAND Flash 등 반도체를 생산·판매하고 있습니다.\n\n(단위: 억원, %)\n\n"
            "| 부문 | 매출액 | 비중 |\n|---|---|---|\n"
            "| DX 부문 | 1,879,673 | 56.3% |\n| DS 부문 | 1,301,282 | 39.0% |\n"
            "| 총계 | 3,336,059 | 100.0% |\n"
        ),
        "rcept_no": "20260310002820",
        "unit_hints": [{"table_idx": 0, "unit": "억원"}],
        "fs_scope_hints": [{"table_idx": 0, "scope": "연결"}],
    },
    ("periodic_20260315002222", "II. 사업의 내용 > 1. 사업의 개요"): {
        "text_md": (
            "1. 사업의 개요\n\n당사는 게임 개발 및 퍼블리싱을 주요 사업으로 하며, "
            "주요 게임으로 '승리의 여신: 니케', '스텔라 블레이드' 등을 보유하고 있습니다.\n"
        ),
        "rcept_no": "20260315002222",
        "unit_hints": [],
        "fs_scope_hints": [],
    },
}

EVENTS: list[dict[str, Any]] = [
    {
        "event_id": "exchange_20250728800035", "rcept_no": "20250728800035",
        "rcept_dt": "2025-07-28", "corp_code": "00126380",
        "event_type": "단일판매공급계약", "title": "반도체 위탁생산 공급계약",
        "amount": 22764764160000, "unit": "원", "ratio": 7.6, "ratio_base": "매출액",
        "counterparty": "테슬라(Tesla, Inc.)",
        "period_start": "2025-07-24", "period_end": "2033-12-31",
        "is_correction": False, "is_latest": False, "status": "체결",
    },
    {
        "event_id": "exchange_20250731800120", "rcept_no": "20250731800120",
        "rcept_dt": "2025-07-31", "corp_code": "00126380",
        "event_type": "단일판매공급계약", "title": "[기재정정] 반도체 위탁생산 공급계약",
        "amount": 22764764160000, "unit": "원", "ratio": 7.6, "ratio_base": "매출액",
        "counterparty": "테슬라(Tesla, Inc.)",
        "period_start": "2025-07-24", "period_end": "2033-12-31",
        "is_correction": True, "is_latest": True, "status": "체결",
    },
]

HISTORY: dict[str, dict[str, Any]] = {
    "20250728800035": {
        "chain": [
            {"rcept_no": "20250728800035", "rcept_dt": "2025-07-28", "kind": "원본"},
            {"rcept_no": "20250731800120", "rcept_dt": "2025-07-31", "kind": "기재정정"},
        ],
        "diffs": [
            {"corrected_at": "2025-07-31", "field": "계약상대",
             "before": "글로벌 대형기업", "after": "테슬라(Tesla, Inc.)"},
        ],
    },
}
HISTORY["20250731800120"] = HISTORY["20250728800035"]


# =================================================================== 헬퍼
def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    out = {"ok": False, "error": {"code": code, "message": message}}
    out.update(extra)
    return out


def _validate_corp(corp_code: str) -> dict[str, Any] | None:
    """corp_code 화이트리스트 검증. 모델이 지어낸 코드를 여기서 차단한다."""
    if corp_code not in UNIVERSE:
        return _err("INVALID_ARG",
                    f"corp_code '{corp_code}' 는 대상 기업 목록에 없습니다. "
                    "resolve_company 출력의 corp_code 만 사용하세요.")
    return None


# =================================================================== 툴 구현
def resolve_company(name: str) -> dict[str, Any]:
    key = name.strip().lower().replace(" ", "")
    if key in AMBIGUOUS:
        return _err("AMBIGUOUS_COMPANY", f"'{name}' 에 해당하는 기업이 여러 개입니다.",
                    candidates=[
                        {"corp_code": c, "corp_name": UNIVERSE[c]["corp_name"]}
                        for c in AMBIGUOUS[key]
                    ])
    if key in ALIASES:
        code = ALIASES[key]
        return _ok({"corp_code": code, **UNIVERSE[code], "matched_by": "alias"})
    for code, info in UNIVERSE.items():
        if key == info["corp_name"].lower():
            return _ok({"corp_code": code, **info, "matched_by": "exact"})
    return _err("NOT_IN_UNIVERSE",
                f"'{name}' 은(는) 평가 대상 70개 기업에 포함되지 않습니다.")


def get_doc_matrix(corp_code: str, base_year: int | None = None,
                   doc_type: str | None = None) -> dict[str, Any]:
    if e := _validate_corp(corp_code):
        return e
    rows = []
    for (c, y, t), v in DOC_MATRIX.items():
        if c != corp_code:
            continue
        if base_year is not None and y != base_year:
            continue
        if doc_type is not None and t != doc_type:
            continue
        rows.append({"base_year": y, "doc_type": t, **v})
    return _ok({"rows": rows})


def list_filings(corp_code: str, doc_type: str | None = None,
                 base_year: int | None = None,
                 is_latest: bool = True) -> dict[str, Any]:
    if e := _validate_corp(corp_code):
        return e
    out = []
    for f in FILINGS.values():
        if f["corp_code"] != corp_code:
            continue
        if doc_type and f["doc_type"] != doc_type:
            continue
        if base_year and f["base_year"] != base_year:
            continue
        if is_latest and not f["is_latest"]:
            continue
        out.append({k: v for k, v in f.items() if k != "corp_code"})
    if not out:
        return _err("DOC_NOT_FOUND",
                    "조건에 맞는 문서가 없습니다. get_doc_matrix 로 존재 여부를 확인하세요.")
    return _ok({"filings": out})


def list_sections(doc_id: str) -> dict[str, Any]:
    if doc_id not in SECTIONS:
        return _err("DOC_NOT_FOUND", f"doc_id '{doc_id}' 를 찾을 수 없습니다.")
    return _ok({"sections": SECTIONS[doc_id]})


def read_section(doc_id: str, path: str) -> dict[str, Any]:
    node = SECTION_TEXT.get((doc_id, path))
    if node is None:
        if doc_id not in SECTIONS:
            return _err("DOC_NOT_FOUND", f"doc_id '{doc_id}' 를 찾을 수 없습니다.")
        return _err("SECTION_NOT_FOUND",
                    f"섹션 '{path}' 이(가) 없습니다. list_sections 의 path 를 그대로 사용하세요.")
    out = dict(node)
    if meta := FILINGS.get(doc_id):
        out["doc_title"] = meta["title"]          # 인용용 문서명 (기준월 포함)
        out["base_year"] = meta["base_year"]
        out["base_month"] = meta["base_month"]
    return _ok(out)


def search_chunks(query: str, top_k: int = 8,
                  filters: dict[str, Any] | None = None) -> dict[str, Any]:
    filters = filters or {}
    if cc := filters.get("corp_code"):
        if e := _validate_corp(cc):
            return e
    # mock: 요약재무정보 청크 하나만 반환
    chunk = SECTION_TEXT[("periodic_20260310002820",
                          "III. 재무에 관한 사항 > 1. 요약재무정보")]
    return _ok({"chunks": [{
        "chunk_id": "periodic_20260310002820#012",
        "doc_id": "periodic_20260310002820",
        "path": "III. 재무에 관한 사항 > 1. 요약재무정보",
        "score": 0.91, "text": chunk["text_md"], "rcept_no": chunk["rcept_no"],
    }][: top_k]})


def query_events(filters: dict[str, Any] | None = None,
                 is_latest: bool = True) -> dict[str, Any]:
    filters = filters or {}
    if cc := filters.get("corp_code"):
        if e := _validate_corp(cc):
            return e
    out = []
    for ev in EVENTS:
        if cc and ev["corp_code"] != cc:
            continue
        if (et := filters.get("event_type")):
            # 유형 양방향 부분 매칭 + 제목 매칭: 모델이 계약명을 넣어도 리콜 유지
            if (et not in ev["event_type"] and ev["event_type"] not in et
                    and et not in ev["title"]):
                continue
        if (df := filters.get("date_from")) and ev["rcept_dt"] < df:
            continue
        if (dt := filters.get("date_to")) and ev["rcept_dt"] > dt:
            continue
        if is_latest and not ev["is_latest"]:
            continue
        item = {k: v for k, v in ev.items() if k != "corp_code"}
        # 모델이 금액을 직접 환산하지 않도록 표기 문자열을 서버가 제공 (결정론)
        item["amount_formatted"] = _krw_format(ev["amount"])
        out.append(item)
    return _ok({"events": out})


def get_history(rcept_no: str) -> dict[str, Any]:
    if rcept_no not in HISTORY:
        return _err("DOC_NOT_FOUND", f"접수번호 '{rcept_no}' 의 이력이 없습니다.")
    return _ok(HISTORY[rcept_no])


UNIT_MULT = {"원": 1, "천원": 1_000, "만원": 10_000,
             "백만원": 1_000_000, "억원": 100_000_000, "조원": 1_000_000_000_000}


def _krw_format(won: int) -> str:
    """원 단위 정수 → '333조 6,059억원' 표기. 결정론 변환 (LLM 산술 금지)."""
    parts, r = [], int(won)
    for name, u in (("조", 1_000_000_000_000), ("억", 100_000_000), ("만", 10_000)):
        q, r = divmod(r, u)
        if q:
            parts.append(f"{q:,}{name}")
    if r or not parts:
        parts.append(f"{r:,}")
    return " ".join(parts) + "원"


def calc(op: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    if op == "krw_format":
        out, steps = [], []
        for i in items:
            unit = i.get("unit", "원")
            if unit not in UNIT_MULT:
                return _err("INVALID_ARG",
                            f"unit '{unit}' 미지원. 가능: {list(UNIT_MULT)}")
            try:
                won = int(float(i["value"]) * UNIT_MULT[unit])
            except (KeyError, TypeError, ValueError):
                return _err("INVALID_ARG", "items 는 [{label, value, unit?}] 형식.")
            fmt = _krw_format(won)
            out.append({"label": i.get("label", ""), "won": won, "formatted": fmt})
            steps.append(f"{i.get('label','')}: {i['value']:,} {unit} = {fmt}")
        return _ok({"result": out, "steps": " / ".join(steps)})

    try:
        vals = [(i["label"], float(i["value"])) for i in items]
    except (KeyError, TypeError, ValueError):
        return _err("INVALID_ARG", "items 는 [{label, value}] 형식이어야 합니다.")
    if not vals:
        return _err("INVALID_ARG", "items 가 비어 있습니다.")

    if op == "compare":
        best = max(vals, key=lambda x: x[1])
        others = [v for v in vals if v[0] != best[0]]
        gap = best[1] - max(o[1] for o in others) if others else 0.0
        steps = " vs ".join(f"{l}={v:,.0f}" for l, v in vals)
        return _ok({"result": {"winner": best[0], "gap": gap}, "steps": steps})
    if op == "sum":
        s = sum(v for _, v in vals)
        return _ok({"result": {"sum": s},
                    "steps": " + ".join(f"{v:,.0f}" for _, v in vals) + f" = {s:,.0f}"})
    if op == "diff":
        if len(vals) != 2:
            return _err("INVALID_ARG", "diff 는 items 2개가 필요합니다.")
        d = vals[0][1] - vals[1][1]
        return _ok({"result": {"diff": d},
                    "steps": f"{vals[0][1]:,.0f} - {vals[1][1]:,.0f} = {d:,.0f}"})
    if op == "growth_rate":
        if len(vals) != 2 or vals[1][1] == 0:
            return _err("INVALID_ARG", "growth_rate 는 [현재, 기준] 2개 필요, 기준≠0.")
        g = (vals[0][1] - vals[1][1]) / vals[1][1] * 100
        return _ok({"result": {"growth_pct": round(g, 2)},
                    "steps": f"({vals[0][1]:,.0f} - {vals[1][1]:,.0f}) / {vals[1][1]:,.0f} = {g:.2f}%"})
    if op == "rank":
        ranked = sorted(vals, key=lambda x: -x[1])
        return _ok({"result": {"ranking": [l for l, _ in ranked]},
                    "steps": " > ".join(f"{l}({v:,.0f})" for l, v in ranked)})
    return _err("INVALID_ARG", f"지원하지 않는 op: {op}")


# =================================================================== 레지스트리
REGISTRY = {
    "resolve_company": resolve_company,
    "get_doc_matrix": get_doc_matrix,
    "list_filings": list_filings,
    "list_sections": list_sections,
    "read_section": read_section,
    "search_chunks": search_chunks,
    "query_events": query_events,
    "get_history": get_history,
    "calc": calc,
}


def dispatch(name: str, arguments: str | dict[str, Any]) -> dict[str, Any]:
    """FC 호출을 실제 함수로 라우팅. 인자는 JSON 문자열 또는 dict."""
    fn = REGISTRY.get(name)
    if fn is None:
        return _err("INVALID_ARG", f"알 수 없는 툴: {name}")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return _err("INVALID_ARG", f"인자 JSON 파싱 실패: {arguments[:100]}")
    try:
        return fn(**arguments)
    except TypeError as exc:
        return _err("INVALID_ARG", f"인자 불일치: {exc}")