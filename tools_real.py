"""
tools_real.py — 팀 PostgreSQL(V1 스키마) 위에 우리 9-툴 계약을 구현하는 어댑터.

원칙:
  - tools_mock.py 와 완전히 동일한 함수 시그니처/응답 스키마 (드롭인 교체)
  - mock 에서 보장하던 안전장치를 어댑터 층에서 유지:
      corp_code 화이트리스트 검증 / amount_formatted 서버 생성 / krw_format
  - 팀 데이터 의미론 준수:
      * is_latest = "successor 없는 관측본" (latest_observed_in_current_dataset)
      * null lineage 를 false 로 해석하지 않음
      * partial parse 문서 제외하지 않음 (caution 만 전달)

환경변수 (.env):
  POSTGRES_HOST=localhost  POSTGRES_PORT=5432
  POSTGRES_DB=disclosure   POSTGRES_USER=disclosure  POSTGRES_PASSWORD=...
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import psycopg2
import psycopg2.extras

from tools_mock import _krw_format, calc  # 순수 함수 재사용 (픽스처 무관)

# ================================================================= 연결
_CONN = None


def _conn():
    global _CONN
    if _CONN is None or _CONN.closed:
        _CONN = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB", "disclosure"),
            user=os.environ.get("POSTGRES_USER", "disclosure"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
        )
        _CONN.autocommit = True
    return _CONN


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with _conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


# ================================================================= 공통
def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    out = {"ok": False, "error": {"code": code, "message": message}}
    out.update(extra)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[\s\.\(\)주식회사㈜]+", "", s).lower()


# ================================================================= 유니버스
# DB companies 테이블에서 기동 시 로드. UNIVERSE/ALIASES/AMBIGUOUS 는
# agent.resolve_deterministic 이 그대로 사용 (mock 과 동일 인터페이스).
UNIVERSE: dict[str, dict[str, Any]] = {}
_NAME2CODE: dict[str, str] = {}

# 별칭 → 정식명 후보 리스트 (DB 표기가 확실치 않은 것은 후보를 복수로)
_ALIAS_CANDS: dict[str, list[str]] = {
    "삼전": ["삼성전자"], "하닉": ["SK하이닉스"], "에스케이하이닉스": ["SK하이닉스"],
    "현차": ["현대자동차", "현대차"], "현대차": ["현대자동차", "현대차"],
    "엘지엔솔": ["LG에너지솔루션"], "lg엔솔": ["LG에너지솔루션"], "엔솔": ["LG에너지솔루션"],
    "포스코": ["POSCO홀딩스"], "포스코홀딩스": ["POSCO홀딩스"],
    "네이버": ["NAVER"], "케이티": ["KT"], "엘지유플러스": ["LG유플러스"],
    "엔씨": ["엔씨소프트"], "sm": ["에스엠"], "에스엠엔터": ["에스엠"],
    "jyp": ["JYP Ent", "JYP Ent."], "제이와이피": ["JYP Ent", "JYP Ent."],
    "와이지": ["와이지엔터테인먼트"], "yg": ["와이지엔터테인먼트"],
    "kai": ["한국항공우주", "한국항공우주산업"], "카이": ["한국항공우주", "한국항공우주산업"],
    "lig": ["LIG넥스원"], "엘에스일렉트릭": ["LS ELECTRIC", "LS일렉트릭"],
    "미래에셋": ["미래에셋증권"], "케이비금융": ["KB금융"], "신한": ["신한지주"],
    "하나금융": ["하나금융지주"], "메리츠": ["메리츠금융지주"],
    "삼바": ["삼성바이오로직스"], "삼성바이오": ["삼성바이오로직스"],
    "에코프로": ["에코프로비엠"], "레인보우": ["레인보우로보틱스"],
    "두산에너": ["두산에너빌리티"], "한화에어로": ["한화에어로스페이스"],
    "hd현대중공업": ["HD현대중공업"], "지에스": [],  # 예: 대상 외 확인용
}

AMBIGUOUS: dict[str, list[str]] = {}   # 별칭 → corp_code 후보 (로드 시 구성)
_AMBIG_PREFIX = ["두산", "한화", "삼성", "현대", "hd현대", "lg", "sk", "우리"]

ALIASES: dict[str, str] = {}           # 별칭 → corp_code (로드 시 구성)


def _load_universe() -> None:
    if UNIVERSE:
        return
    rows = _q("""
        SELECT c.corp_code, c.stock_code, c.company_name,
               max(d.metadata->'canonical'->>'sector')   AS sector,
               max(d.metadata->'canonical'->>'industry') AS industry
        FROM companies c LEFT JOIN disclosures d ON d.company_id = c.company_id
        GROUP BY c.corp_code, c.stock_code, c.company_name""")
    for r in rows:
        code = r["corp_code"]
        UNIVERSE[code] = {"corp_name": r["company_name"],
                          "stock_code": r["stock_code"],
                          "sector": r["sector"], "market": None}
        _NAME2CODE[_norm(r["company_name"])] = code
    # 별칭 → corp_code 확정
    for alias, cands in _ALIAS_CANDS.items():
        for cand in cands:
            if (code := _NAME2CODE.get(_norm(cand))):
                ALIASES[_norm(alias)] = code
                break
    # 모호 접두 별칭: 해당 접두로 시작하는 기업이 2개 이상이면 AMBIGUOUS 등록
    for prefix in _AMBIG_PREFIX:
        hits = [c for n, c in _NAME2CODE.items() if n.startswith(_norm(prefix))]
        if len(hits) >= 2:
            AMBIGUOUS[_norm(prefix)] = sorted(set(hits))


def _validate_corp(corp_code: str) -> dict[str, Any] | None:
    _load_universe()
    if corp_code not in UNIVERSE:
        return _err("INVALID_ARG",
                    f"corp_code '{corp_code}' 는 대상 기업 목록에 없습니다. "
                    "resolve_company 출력의 corp_code 만 사용하세요.")
    return None


# ================================================================= 1. resolve
def resolve_company(name: str) -> dict[str, Any]:
    _load_universe()
    key = _norm(name)
    if key in AMBIGUOUS:
        return _err("AMBIGUOUS_COMPANY", f"'{name}' 에 해당하는 기업이 여러 개입니다.",
                    candidates=[{"corp_code": c, "corp_name": UNIVERSE[c]["corp_name"]}
                                for c in AMBIGUOUS[key]])
    if key in ALIASES:
        code = ALIASES[key]
        return _ok({"corp_code": code, **UNIVERSE[code], "matched_by": "alias"})
    if key in _NAME2CODE:
        code = _NAME2CODE[key]
        return _ok({"corp_code": code, **UNIVERSE[code], "matched_by": "exact"})
    # trigram 유사도 폴백 (DB)
    rows = _q("""SELECT corp_code, company_name,
                        similarity(company_name_normalized, %s) AS sim
                 FROM companies
                 WHERE similarity(company_name_normalized, %s) > 0.3
                 ORDER BY sim DESC LIMIT 3""", (key, key))
    if len(rows) == 1 or (rows and rows[0]["sim"] > 0.6):
        code = rows[0]["corp_code"]
        return _ok({"corp_code": code, **UNIVERSE[code], "matched_by": "fuzzy"})
    if rows:
        return _err("AMBIGUOUS_COMPANY", f"'{name}' 후보가 여러 개입니다.",
                    candidates=[{"corp_code": r["corp_code"],
                                 "corp_name": r["company_name"]} for r in rows])
    return _err("NOT_IN_UNIVERSE",
                f"'{name}' 은(는) 평가 대상 70개 기업에 포함되지 않습니다.")


# ================================================================= 내부 헬퍼
_DOCTYPE_MAP = {   # 한국어 doc_type → (report_type 후보, report_name LIKE)
    "사업보고서": (["사업보고서", "annual"], "사업보고서%"),
    "반기보고서": (["반기보고서", "half", "semiannual"], "반기보고서%"),
    "분기보고서": (["분기보고서", "quarter", "quarterly"], "분기보고서%"),
}


def _base_year_of(row: dict[str, Any]) -> tuple[int | None, int | None]:
    """canonical base_year/base_month 우선, 없으면 report_name 의 (YYYY.MM) 파싱."""
    can = (row.get("canonical") or {})
    by, bm = can.get("base_year"), can.get("base_month")
    if by:
        return int(by), (int(bm) if bm else None)
    m = re.search(r"\((\d{4})\.(\d{2})\)", row.get("report_name") or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _has_successor(rcept_no: str) -> bool:
    rows = _q("SELECT 1 FROM correction_links WHERE predecessor_receipt_no=%s LIMIT 1",
              (rcept_no,))
    return bool(rows)


def _periodic_rows(corp_code: str, doc_type: str | None,
                   base_year: int | None) -> list[dict[str, Any]]:
    sql = """SELECT d.receipt_no, d.receipt_date, d.report_name, d.report_type,
                    d.is_correction_name,
                    d.metadata->'canonical' AS canonical
             FROM disclosures d JOIN companies c ON c.company_id=d.company_id
             WHERE c.corp_code=%s AND d.category='periodic'"""
    params: list[Any] = [corp_code]
    if doc_type and doc_type in _DOCTYPE_MAP:
        types, like = _DOCTYPE_MAP[doc_type]
        sql += " AND (d.report_type = ANY(%s) OR d.report_name LIKE %s)"
        params += [types, like]
    rows = _q(sql + " ORDER BY d.receipt_date", tuple(params))
    out = []
    for r in rows:
        by, bm = _base_year_of(r)
        if base_year and by != base_year:
            continue
        r["base_year"], r["base_month"] = by, bm
        out.append(r)
    return out


def _doc_id_to_rcept(doc_id: str) -> str | None:
    if re.fullmatch(r"\d{14}", doc_id):
        return doc_id
    rows = _q("SELECT receipt_no FROM disclosures "
              "WHERE metadata->'canonical'->>'doc_id'=%s LIMIT 1", (doc_id,))
    return rows[0]["receipt_no"] if rows else None


# ================================================================= 2. matrix
def get_doc_matrix(corp_code: str, base_year: int | None = None,
                   doc_type: str | None = None) -> dict[str, Any]:
    if e := _validate_corp(corp_code):
        return e
    types = [doc_type] if doc_type else list(_DOCTYPE_MAP)
    years = [base_year] if base_year else [2023, 2024, 2025, 2026]
    have: dict[tuple[int, str], str] = {}
    for t in types:
        for r in _periodic_rows(corp_code, t, None):
            if r["base_year"]:
                have[(r["base_year"], t)] = (r.get("canonical") or {}).get(
                    "doc_id") or r["receipt_no"]
    rows = []
    for y in years:
        for t in types:
            if (y, t) in have:
                rows.append({"base_year": y, "doc_type": t, "exists": True,
                             "doc_id": have[(y, t)]})
            else:
                rows.append({"base_year": y, "doc_type": t, "exists": False,
                             "reason": "제공 데이터셋(2023.01~2026.03)에 해당 공시 없음"})
    return _ok({"rows": rows})


# ================================================================= 3. filings
def list_filings(corp_code: str, doc_type: str | None = None,
                 base_year: int | None = None,
                 is_latest: bool = True) -> dict[str, Any]:
    if e := _validate_corp(corp_code):
        return e
    out = []
    for r in _periodic_rows(corp_code, doc_type, base_year):
        latest = not _has_successor(r["receipt_no"])
        if is_latest and not latest:
            continue
        can = r.get("canonical") or {}
        out.append({"doc_id": can.get("doc_id") or r["receipt_no"],
                    "rcept_no": r["receipt_no"],
                    "rcept_dt": str(r["receipt_date"]),
                    "doc_type": doc_type or r.get("report_type") or "정기공시",
                    "base_year": r["base_year"], "base_month": r["base_month"],
                    "is_correction": bool(r["is_correction_name"]),
                    "is_latest": latest, "title": r["report_name"]})
    if not out:
        return _err("DOC_NOT_FOUND",
                    "조건에 맞는 문서가 없습니다. get_doc_matrix 로 존재 여부를 확인하세요.")
    return _ok({"filings": out})


# ================================================================= 4~5. 섹션
def list_sections(doc_id: str) -> dict[str, Any]:
    rcept = _doc_id_to_rcept(doc_id)
    if not rcept:
        return _err("DOC_NOT_FOUND", f"doc_id '{doc_id}' 를 찾을 수 없습니다.")
    rows = _q("""SELECT section_path, length(body_text) AS n_chars,
                        coalesce((metadata->>'table_count')::int, 0) AS n_tables
                 FROM document_sections WHERE receipt_no=%s
                 ORDER BY section_order""", (rcept,))
    if not rows:
        return _err("SECTION_NOT_FOUND",
                    "본문 섹션이 아직 적재되지 않았거나 섹션 없는 문서입니다. "
                    "search_chunks 를 사용하세요.")
    return _ok({"sections": [
        {"path": r["section_path"], "n_chars": r["n_chars"],
         "n_tables": r["n_tables"], "is_stub": r["n_chars"] < 100} for r in rows]})


def read_section(doc_id: str, path: str) -> dict[str, Any]:
    rcept = _doc_id_to_rcept(doc_id)
    if not rcept:
        return _err("DOC_NOT_FOUND", f"doc_id '{doc_id}' 를 찾을 수 없습니다.")
    rows = _q("""SELECT s.body_text, s.section_path, s.source_locator,
                        d.report_name,
                        d.metadata->'canonical' AS canonical
                 FROM document_sections s JOIN disclosures d USING (receipt_no)
                 WHERE s.receipt_no=%s AND s.section_path=%s LIMIT 1""",
              (rcept, path))
    if not rows:
        return _err("SECTION_NOT_FOUND",
                    f"섹션 '{path}' 이(가) 없습니다. list_sections 의 path 를 그대로 사용하세요.")
    r = rows[0]
    by, bm = _base_year_of({"canonical": r["canonical"],
                            "report_name": r["report_name"]})
    return _ok({"text_md": r["body_text"], "rcept_no": rcept,
                "doc_title": r["report_name"], "base_year": by, "base_month": bm,
                "unit_hints": [], "fs_scope_hints": [],   # v1 미지원 (원문에 단위 표기 보존)
                "source_locator": r["source_locator"]})


# ================================================================= 6. 검색
def search_chunks(query: str, top_k: int = 8,
                  filters: dict[str, Any] | None = None) -> dict[str, Any]:
    filters = filters or {}
    if cc := filters.get("corp_code"):
        if e := _validate_corp(cc):
            return e
    sql = """SELECT s.section_id, s.receipt_no, s.section_path,
                    ts_rank(s.search_tsv, plainto_tsquery('simple', %s)) AS score,
                    left(s.body_text, 1500) AS text,
                    d.metadata->'canonical'->>'doc_id' AS doc_id
             FROM document_sections s
             JOIN disclosures d USING (receipt_no)
             JOIN companies c ON c.company_id=d.company_id
             WHERE s.search_tsv @@ plainto_tsquery('simple', %s)"""
    params: list[Any] = [query, query]
    if cc:
        sql += " AND c.corp_code=%s"; params.append(cc)
    if dt := filters.get("doc_type"):
        types, like = _DOCTYPE_MAP.get(dt, ([dt], f"{dt}%"))
        sql += " AND (d.report_type = ANY(%s) OR d.report_name LIKE %s)"
        params += [types, like]
    sql += " ORDER BY score DESC LIMIT %s"; params.append(min(top_k, 20))
    rows = _q(sql, tuple(params))
    return _ok({"chunks": [
        {"chunk_id": f"{r['doc_id'] or r['receipt_no']}#{r['section_id']}",
         "doc_id": r["doc_id"] or r["receipt_no"], "path": r["section_path"],
         "score": float(r["score"]), "text": r["text"],
         "rcept_no": r["receipt_no"]} for r in rows]})


# ================================================================= 7. 이벤트
def query_events(filters: dict[str, Any] | None = None,
                 is_latest: bool = True) -> dict[str, Any]:
    filters = filters or {}
    cc = filters.get("corp_code")
    if cc and (e := _validate_corp(cc)):
        return e
    et = (filters.get("event_type") or "").strip()

    # 정형 경로: 계약 (contracts 테이블, 1,106건)
    if not et or "계약" in et or et in "단일판매공급계약":
        sql = """SELECT ct.receipt_no, d.receipt_date, d.report_name,
                        d.is_correction_name,
                        ct.contract_name, ct.contract_amount, ct.contract_amount_raw,
                        ct.counterparty, ct.sales_ratio,
                        ct.contract_start_date, ct.contract_end_date
                 FROM contracts ct
                 JOIN disclosures d ON d.receipt_no = ct.receipt_no
                 JOIN companies c ON c.company_id = d.company_id
                 WHERE TRUE"""
        params: list[Any] = []
        if cc:
            sql += " AND c.corp_code=%s"; params.append(cc)
        if df := filters.get("date_from"):
            sql += " AND d.receipt_date >= %s"; params.append(df)
        if dt_ := filters.get("date_to"):
            sql += " AND d.receipt_date <= %s"; params.append(dt_)
        sql += " ORDER BY d.receipt_date"
        events = []
        for r in _q(sql, tuple(params)):
            latest = not _has_successor(r["receipt_no"])
            if is_latest and not latest:
                continue
            amount = int(r["contract_amount"]) if r["contract_amount"] is not None else None
            events.append({
                "event_id": f"exchange_{r['receipt_no']}",
                "rcept_no": r["receipt_no"], "rcept_dt": str(r["receipt_date"]),
                "event_type": "단일판매공급계약",
                "title": r["contract_name"] or r["report_name"],
                "amount": amount, "unit": "원",
                "amount_formatted": _krw_format(amount) if amount is not None else None,
                "amount_raw": r["contract_amount_raw"],
                "ratio": float(r["sales_ratio"]) if r["sales_ratio"] is not None else None,
                "ratio_base": "매출액",
                "counterparty": r["counterparty"],
                "period_start": str(r["contract_start_date"]) if r["contract_start_date"] else None,
                "period_end": str(r["contract_end_date"]) if r["contract_end_date"] else None,
                "is_correction": bool(r["is_correction_name"]),
                "is_latest": latest,
                "status": "해지" if "해지" in (r["report_name"] or "") else "체결"})
        if "계약" in et or events or et in "단일판매공급계약":
            return _ok({"events": events})

    # degrade 경로: 정형 미추출 유형 → 공시 메타 목록 + 본문 read 안내
    sql = """SELECT d.receipt_no, d.receipt_date, d.report_name, d.report_type,
                    d.is_correction_name, d.category,
                    d.metadata->'canonical'->>'doc_id' AS doc_id
             FROM disclosures d JOIN companies c ON c.company_id=d.company_id
             WHERE d.category IN ('major','exchange','holding')"""
    params = []
    if cc:
        sql += " AND c.corp_code=%s"; params.append(cc)
    if et:
        sql += " AND (d.report_name ILIKE %s OR d.report_type ILIKE %s)"
        params += [f"%{et}%", f"%{et}%"]
    if df := filters.get("date_from"):
        sql += " AND d.receipt_date >= %s"; params.append(df)
    if dt_ := filters.get("date_to"):
        sql += " AND d.receipt_date <= %s"; params.append(dt_)
    sql += " ORDER BY d.receipt_date LIMIT 50"
    events = []
    for r in _q(sql, tuple(params)):
        latest = not _has_successor(r["receipt_no"])
        if is_latest and not latest:
            continue
        events.append({
            "event_id": f"{r['category']}_{r['receipt_no']}",
            "rcept_no": r["receipt_no"], "rcept_dt": str(r["receipt_date"]),
            "event_type": r["report_type"] or r["report_name"],
            "title": r["report_name"], "amount": None, "unit": None,
            "amount_formatted": None, "ratio": None, "counterparty": None,
            "period_start": None, "period_end": None,
            "is_correction": bool(r["is_correction_name"]), "is_latest": latest,
            "status": "해지" if "해지" in (r["report_name"] or "") else None,
            "note": "정형 필드 미추출 유형입니다. 금액 등 세부값은 이 문서를 "
                    "read(search_chunks 또는 doc_id 로 접근)해서 원문에서 확인하세요."})
    return _ok({"events": events})


# ================================================================= 8. 이력
def get_history(rcept_no: str) -> dict[str, Any]:
    # 양방향 BFS 로 connected component 수집
    seen, frontier = {rcept_no}, [rcept_no]
    while frontier:
        node = frontier.pop()
        for r in _q("""SELECT predecessor_receipt_no p, successor_receipt_no s
                       FROM correction_links
                       WHERE predecessor_receipt_no=%s OR successor_receipt_no=%s""",
                    (node, node)):
            for nxt in (r["p"], r["s"]):
                if nxt not in seen:
                    seen.add(nxt); frontier.append(nxt)
    if len(seen) == 1:
        exists = _q("SELECT 1 FROM disclosures WHERE receipt_no=%s", (rcept_no,))
        if not exists:
            return _err("DOC_NOT_FOUND", f"접수번호 '{rcept_no}' 가 데이터셋에 없습니다.")
        return _ok({"chain": [{"rcept_no": rcept_no, "kind": "원본",
                               "note": "확인된 정정 이력 없음 (explicit 링크 기준)"}],
                    "diffs": [],
                    "note": "정정 전후 상세 비교(diff)는 정정공시 원문의 "
                            "'정정전/정정후' 대비표를 read 해서 확인하세요."})
    rows = _q("""SELECT receipt_no, receipt_date, report_name, is_correction_name
                 FROM disclosures WHERE receipt_no = ANY(%s)
                 ORDER BY receipt_date, receipt_no""", (list(seen),))
    chain = [{"rcept_no": r["receipt_no"], "rcept_dt": str(r["receipt_date"]),
              "kind": "기재정정" if r["is_correction_name"] else "원본",
              "title": r["report_name"]} for r in rows]
    return _ok({"chain": chain, "diffs": [],
                "latest_observed": chain[-1]["rcept_no"],
                "note": "정정 전후 상세 비교는 최신 정정 문서 원문의 "
                        "'정정전/정정후' 대비표를 read 해서 인용하세요."})


# ================================================================= 레지스트리
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
    except psycopg2.Error as exc:
        return _err("DB_ERROR", f"DB 오류: {str(exc)[:200]}")
    except TypeError as exc:
        return _err("INVALID_ARG", f"인자 불일치: {exc}")
