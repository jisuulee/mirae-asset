"""
integration_check.py — 실 DB 어댑터 무결성 점검 (LLM 호출 없음, 토큰 0)

전제:
  1) 팀 레포의 docker-compose 로 PostgreSQL 기동 + 적재 완료
  2) .env 에 POSTGRES_* 설정
  3) pip install psycopg2-binary

실행: python integration_check.py
"""

from __future__ import annotations

import json

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import tools_real as t

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, status: str, note: str = "") -> None:
    results.append((name, status, note))
    print(f"[{status}] {name}  {note}")


def main() -> None:
    # 0. 연결 + 유니버스
    try:
        t._load_universe()
        n = len(t.UNIVERSE)
        check("DB 연결/유니버스", PASS if n == 70 else WARN, f"기업 {n}개 (기대 70)")
    except Exception as exc:  # noqa: BLE001
        check("DB 연결", FAIL, str(exc)[:150])
        print("\n→ docker compose up -d 및 적재 스크립트 실행 여부, .env 확인")
        return

    # 1. resolve: 별칭/모호/대상외
    for name, expect in (("삼전", "ok"), ("현대차", "ok"), ("네이버", "ok"),
                         ("두산", "AMBIGUOUS_COMPANY"),
                         ("우리", "AMBIGUOUS_COMPANY"),
                         ("배달의민족", "NOT_IN_UNIVERSE")):
        r = t.resolve_company(name)
        got = "ok" if r.get("ok") else r["error"]["code"]
        detail = r["data"]["corp_name"] if r.get("ok") else \
            ",".join(c["corp_name"] for c in r.get("candidates", []))[:40]
        check(f"resolve '{name}'", PASS if got == expect else FAIL,
              f"{got} → {detail}")

    # 2. 화이트리스트: 지어낸 corp_code 차단
    r = t.list_filings("00000000")
    check("corp_code 화이트리스트", PASS if not r["ok"] and
          r["error"]["code"] == "INVALID_ARG" else FAIL)

    # 3. matrix + filings (삼성전자)
    rs = t.resolve_company("삼성전자")
    if rs.get("ok"):
        code = rs["data"]["corp_code"]
        m = t.get_doc_matrix(code, doc_type="사업보고서")
        exist = [row for row in m["data"]["rows"] if row["exists"]]
        check("doc_matrix 삼성전자 사업보고서", PASS if exist else WARN,
              f"존재 {len(exist)}건: {[row['base_year'] for row in exist]}")
        f = t.list_filings(code, doc_type="사업보고서")
        if f.get("ok"):
            first = f["data"]["filings"][-1]
            check("list_filings", PASS,
                  f"{first['title']} rcept={first['rcept_no']} "
                  f"base={first['base_year']}.{first['base_month']}")
            # 4. 섹션 (적재 여부가 갭 ①)
            s = t.list_sections(first["doc_id"])
            if s.get("ok"):
                check("list_sections", PASS, f"{len(s['data']['sections'])}개 섹션")
                path = s["data"]["sections"][0]["path"]
                rd = t.read_section(first["doc_id"], path)
                check("read_section", PASS if rd.get("ok") else FAIL,
                      f"doc_title={rd['data'].get('doc_title') if rd.get('ok') else rd['error']['code']}")
            else:
                check("list_sections", WARN,
                      f"{s['error']['code']} — 갭①(정기공시 섹션 미적재) 상태. "
                      "2번 적재 완료 후 재실행")
        else:
            check("list_filings", FAIL, f["error"]["code"])

    # 5. FTS 검색
    sc = t.search_chunks("매출액", top_k=3)
    nchunks = len(sc["data"]["chunks"]) if sc.get("ok") else -1
    check("search_chunks(FTS)", PASS if nchunks > 0 else WARN,
          f"{nchunks}건 (0이면 섹션 미적재)")

    # 6. 이벤트: 계약 정형 경로 (2번 벤치 기준 현대건설 2023)
    rh = t.resolve_company("현대건설")
    if rh.get("ok"):
        ev = t.query_events({"corp_code": rh["data"]["corp_code"],
                             "event_type": "공급계약",
                             "date_from": "2023-01-01", "date_to": "2023-12-31"})
        evs = ev["data"]["events"] if ev.get("ok") else []
        ok_fmt = evs and evs[0].get("amount_formatted")
        check("query_events 계약(정형)", PASS if evs else FAIL,
              f"{len(evs)}건, amount_formatted={'있음' if ok_fmt else '없음/None'}")
        # 7. degrade 경로: 유상증자류
        ev2 = t.query_events({"corp_code": rh["data"]["corp_code"],
                              "event_type": "유상증자"})
        n2 = len(ev2["data"]["events"]) if ev2.get("ok") else -1
        check("query_events degrade(major)", PASS if n2 >= 0 else FAIL,
              f"{n2}건 (0건이어도 경로 정상)")

    # 8. 정정 체인: 링크 하나 골라 추적
    rows = t._q("SELECT predecessor_receipt_no p, successor_receipt_no s "
                "FROM correction_links LIMIT 1")
    if rows:
        h = t.get_history(rows[0]["p"])
        if h.get("ok"):
            ch = h["data"]["chain"]
            check("get_history 체인", PASS if len(ch) >= 2 else FAIL,
                  f"{len(ch)}노드, latest={h['data'].get('latest_observed')}")
        else:
            check("get_history", FAIL, h["error"]["code"])
    else:
        check("get_history", WARN, "correction_links 비어있음 — 적재 확인")

    # 9. calc (순수 함수)
    c = t.calc("krw_format", [{"label": "x", "value": 333605900, "unit": "백만원"}])
    check("calc krw_format", PASS if c["data"]["result"][0]["formatted"]
          == "333조 6,059억원" else FAIL)

    print("\n" + "=" * 60)
    npass = sum(1 for _, s, _ in results if s == PASS)
    nwarn = sum(1 for _, s, _ in results if s == WARN)
    print(f"결과: PASS {npass} / WARN {nwarn} / FAIL {len(results)-npass-nwarn}")
    print("WARN 항목은 갭(섹션 미적재 등)의 현재 상태 표시이며, 2번 적재 후 재실행.")


if __name__ == "__main__":
    main()
