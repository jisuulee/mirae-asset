"""
ground_truth.py — 실 DB 에서 하네스 정답 라벨(gold)을 뽑아내는 도구

배경: 기존 questions.jsonl 의 기대값은 mock 픽스처(설명회 슬라이드 반올림 수치)
기반이라 실데이터와 불일치한다. 튜닝 전에 정답부터 실데이터로 재라벨링해야 한다.

사용:
  # 1) 기업의 정기공시 목차 보기
  python ground_truth.py sections 삼성전자 2025 사업보고서

  # 2) 특정 섹션 원문 보기 (표 포함) — 정답 수치를 눈으로 확인
  python ground_truth.py read 삼성전자 2025 사업보고서 "III. 재무에 관한 사항 > 1. 요약재무정보"

  # 3) 키워드로 섹션 찾기
  python ground_truth.py find 삼성전자 매출액

  # 4) 계약 이벤트 목록 (정답 접수번호/금액 확보)
  python ground_truth.py events 현대건설 2023

  # 5) 정정 체인이 있는 문서 찾기 (correction_trap 문항용)
  python ground_truth.py corrections 5
"""

from __future__ import annotations

import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import tools_real as t


def _corp(name: str) -> str | None:
    r = t.resolve_company(name)
    if not r.get("ok"):
        print(f"[!] resolve 실패: {r['error']['code']} "
              f"{[c['corp_name'] for c in r.get('candidates', [])]}")
        return None
    print(f"# {r['data']['corp_name']} ({r['data']['corp_code']})")
    return r["data"]["corp_code"]


def cmd_sections(name: str, year: str, doc_type: str = "사업보고서") -> None:
    code = _corp(name)
    if not code:
        return
    f = t.list_filings(code, doc_type=doc_type, base_year=int(year))
    if not f.get("ok"):
        print(f"[!] {f['error']['code']}: {f['error']['message']}")
        return
    doc = f["data"]["filings"][-1]
    print(f"# {doc['title']} | doc_id={doc['doc_id']} | rcept={doc['rcept_no']}")
    s = t.list_sections(doc["doc_id"])
    if not s.get("ok"):
        print(f"[!] {s['error']['code']}")
        return
    for sec in s["data"]["sections"]:
        stub = " (stub)" if sec["is_stub"] else ""
        print(f"  {sec['n_chars']:7d}자 표{sec['n_tables']:2d}  {sec['path']}{stub}")


def cmd_read(name: str, year: str, doc_type: str, path: str) -> None:
    code = _corp(name)
    if not code:
        return
    f = t.list_filings(code, doc_type=doc_type, base_year=int(year))
    if not f.get("ok"):
        print(f"[!] {f['error']['code']}")
        return
    doc = f["data"]["filings"][-1]
    r = t.read_section(doc["doc_id"], path)
    if not r.get("ok"):
        print(f"[!] {r['error']['code']}: {r['error']['message']}")
        return
    d = r["data"]
    print(f"# {d['doc_title']} | rcept={d['rcept_no']} | path={path}")
    print("-" * 70)
    print(d["text_md"][:4000])
    print("-" * 70)
    print("[라벨링 힌트] 위 표의 숫자와 단위를 그대로 읽어 원 단위로 환산하라.")
    print("  예) 333,605,938 (단위: 백만원) → amounts_won: 333605938000000")


def cmd_find(name: str, query: str) -> None:
    code = _corp(name)
    if not code:
        return
    r = t.search_chunks(query, top_k=8, filters={"corp_code": code})
    for c in r["data"]["chunks"]:
        print(f"  score={c['score']:.3f} rcept={c['rcept_no']}  {c['path']}")
        print(f"      {c['text'][:150].replace(chr(10), ' ')}")


def cmd_events(name: str, year: str | None = None) -> None:
    code = _corp(name)
    if not code:
        return
    filters = {"corp_code": code}
    if year:
        filters |= {"date_from": f"{year}-01-01", "date_to": f"{year}-12-31"}
    r = t.query_events(filters)
    for e in r["data"]["events"]:
        print(f"  {e['rcept_dt']} rcept={e['rcept_no']} corr={e['is_correction']}")
        print(f"      {e['title']}")
        if e.get("amount"):
            print(f"      금액={e['amount']:,}원  ({e['amount_formatted']})  "
                  f"상대={e.get('counterparty')}  비중={e.get('ratio')}%")


def cmd_corrections(limit: str = "5") -> None:
    rows = t._q("""SELECT cl.predecessor_receipt_no p, cl.successor_receipt_no s,
                          c.company_name, d.report_name
                   FROM correction_links cl
                   JOIN disclosures d ON d.receipt_no = cl.successor_receipt_no
                   JOIN companies c ON c.company_id = d.company_id
                   LIMIT %s""", (int(limit),))
    for r in rows:
        print(f"  {r['company_name']}: {r['p']} → {r['s']}")
        print(f"      {r['report_name']}")


CMDS = {"sections": cmd_sections, "read": cmd_read, "find": cmd_find,
        "events": cmd_events, "corrections": cmd_corrections}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        sys.exit(1)
    CMDS[sys.argv[1]](*sys.argv[2:])
