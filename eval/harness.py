"""
자체 평가 하네스 v0

사용법:
  python eval/harness.py                       # 전체 문항 실행 (로컬 에이전트)
  python eval/harness.py --limit 3             # 앞 3문항만
  python eval/harness.py --only E-004          # 특정 문항만
  python eval/harness.py --tag unit_trap       # 특정 함정 태그만
  python eval/harness.py --api http://IP:8000  # 배포 서버 상대 (4번 통합 테스트용)
  python eval/harness.py --baseline runs/2026....json   # 이전 실행과 비교

채점 차원 (공식 평가지표 역산):
  accuracy   : 금액은 값 기반 파싱 비교(kr_number), 그 외 must_include 류
  citation   : answer 에 기대 접수번호 포함
  grounding  : retrieved_context 에 필수 원문 존재 (근거 완전성)
  abstain    : must_abstain 문항에서 한계 고지 문구 + 지어낸 금액 없음
  safety     : 유출/금지 문자열 부재 (must_not_include)

결과는 runs/<timestamp>.json 에 저장 → --baseline 으로 회귀 비교.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kr_number import contains_amount, extract_amounts  # noqa: E402

ABSTAIN_MARKERS = ["확인할 수 없", "확인되지 않", "데이터에 없", "포함되지 않", "존재하지 않"]


# ------------------------------------------------------------------ 채점기
def grade(q: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    exp = q.get("expect", {})
    answer = result.get("answer", "") or ""
    context = result.get("retrieved_context", "") or ""
    checks: list[tuple[str, str, bool, str]] = []  # (차원, 이름, 통과, 비고)

    # --- accuracy: 값 기반 금액
    for won in exp.get("amounts_won", []):
        ok = contains_amount(answer, won)
        checks.append(("accuracy", f"금액={won:,}", ok,
                       "" if ok else f"답변 금액들: {sorted(extract_amounts(answer))[:5]}"))
    for s in exp.get("must_include", []):
        checks.append(("accuracy", f"포함:'{s}'", s in answer, ""))
    for key in ("must_include_any", "must_include_any2"):
        cands = exp.get(key, [])
        if cands:
            ok = any(s in answer for s in cands)
            checks.append(("accuracy", f"택일:{cands[:3]}", ok, ""))

    # --- citation
    for rc in exp.get("citation_rcept", []):
        checks.append(("citation", f"접수번호 {rc}", rc in answer, ""))

    # --- grounding (근거 완전성)
    for s in exp.get("context_must_include", []):
        checks.append(("grounding", f"근거에 '{s}'", s in context, ""))

    # --- abstain (정보한계 대응)
    if exp.get("must_abstain"):
        marked = any(m in answer for m in ABSTAIN_MARKERS)
        checks.append(("abstain", "한계 고지 문구", marked, ""))
        if exp.get("must_not_amounts"):
            fab = extract_amounts(answer)
            checks.append(("abstain", "지어낸 금액 없음", not fab,
                           "" if not fab else f"발견: {sorted(fab)[:3]}"))

    # --- safety
    for s in exp.get("must_not_include", []):
        checks.append(("safety", f"금지:'{s}'", s not in answer, ""))

    dims: dict[str, list[bool]] = {}
    for dim, _, ok, _ in checks:
        dims.setdefault(dim, []).append(ok)
    dim_pass = {d: all(v) for d, v in dims.items()}
    return {
        "checks": [{"dim": d, "name": n, "ok": o, "note": t} for d, n, o, t in checks],
        "dims": dim_pass,
        "passed": all(dim_pass.values()) if dim_pass else False,
    }


# ------------------------------------------------------------------ 실행기
def run_local(qid: str, question: str, agent: Any) -> dict[str, Any]:
    return agent.answer(qid, question)


def run_api(qid: str, question: str, base_url: str) -> dict[str, Any]:
    import requests

    r = requests.get(f"{base_url.rstrip('/')}/answer",
                     params={"question_id": qid, "question": question}, timeout=120)
    r.raise_for_status()
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--tag", type=str, default="")
    ap.add_argument("--api", type=str, default="")
    ap.add_argument("--baseline", type=str, default="")
    ap.add_argument("--prompt-version", type=str, default="v0")
    ap.add_argument("--sleep", type=float, default=5.0,
                    help="문항 간 대기 (테스트 키 rate limit 429 방지)")
    args = ap.parse_args()

    qfile = Path(__file__).parent / "questions.jsonl"
    questions = [json.loads(l) for l in qfile.read_text(encoding="utf-8").splitlines()
                 if l.strip()]
    if args.only:
        questions = [q for q in questions if q["id"] == args.only]
    if args.tag:
        questions = [q for q in questions if args.tag in q.get("traps", [])]
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        print("문항 없음"); return

    agent = None
    if not args.api:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        from agent import DisclosureAgent
        agent = DisclosureAgent()

    rows = []
    t_start = time.time()
    for i, q in enumerate(questions):
        if i and args.sleep:
            time.sleep(args.sleep)
        print(f"\n{'=' * 66}\n{q['id']} [{q['type']}/{q['difficulty']}/{q['mode']}] "
              f"traps={q.get('traps')}\n  {q['question']}")
        t0 = time.time()
        try:
            result = (run_api(q["id"], q["question"], args.api) if args.api
                      else run_local(q["id"], q["question"], agent))
            g = grade(q, result)
            elapsed = time.time() - t0
        except Exception as exc:  # noqa: BLE001
            result = {"answer": f"(실행 오류: {exc})", "retrieved_context": ""}
            g = {"checks": [{"dim": "run", "name": "실행", "ok": False, "note": str(exc)[:120]}],
                 "dims": {"run": False}, "passed": False}
            elapsed = time.time() - t0

        mark = "PASS" if g["passed"] else "FAIL"
        print(f"  → {mark} ({elapsed:.1f}s)")
        for c in g["checks"]:
            flag = "o" if c["ok"] else "X"
            note = f"  <- {c['note']}" if c["note"] else ""
            print(f"     [{flag}] {c['dim']:9s} {c['name']}{note}")
        rows.append({"id": q["id"], "type": q["type"], "difficulty": q["difficulty"],
                     "mode": q["mode"], "traps": q.get("traps", []),
                     "passed": g["passed"], "dims": g["dims"], "checks": g["checks"],
                     "answer": result.get("answer", ""), "elapsed_s": round(elapsed, 1)})

    # ------------------------------------------------------------- 리포트
    total = len(rows)
    npass = sum(r["passed"] for r in rows)
    print(f"\n{'#' * 66}\n종합: {npass}/{total} PASS  ({time.time() - t_start:.0f}s, "
          f"prompt={args.prompt_version})")

    def axis(key_fn, label: str) -> None:
        agg: dict[str, list[bool]] = {}
        for r in rows:
            for k in key_fn(r):
                agg.setdefault(k, []).append(r["passed"])
        print(f"\n  [{label}]")
        for k, v in sorted(agg.items()):
            print(f"    {k:20s} {sum(v)}/{len(v)}")

    axis(lambda r: [r["type"]], "유형별")
    axis(lambda r: [r["difficulty"]], "난이도별")
    axis(lambda r: r["traps"] or ["(없음)"], "함정 태그별")

    dim_agg: dict[str, list[bool]] = {}
    for r in rows:
        for d, ok in r["dims"].items():
            dim_agg.setdefault(d, []).append(ok)
    print("\n  [평가지표 차원별]")
    for d, v in sorted(dim_agg.items()):
        print(f"    {d:12s} {sum(v)}/{len(v)}")

    # ------------------------------------------------------------- 저장/비교
    runs = Path(__file__).parent / "runs"
    runs.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = runs / f"{stamp}.json"
    out.write_text(json.dumps(
        {"stamp": stamp, "prompt_version": args.prompt_version,
         "pass": npass, "total": total, "rows": rows},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {out}")

    if args.baseline:
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        prev = {r["id"]: r["passed"] for r in base["rows"]}
        print(f"\n[베이스라인 비교: {base['stamp']} ({base['pass']}/{base['total']})]")
        for r in rows:
            if r["id"] not in prev:
                print(f"  NEW  {r['id']}")
            elif prev[r["id"]] and not r["passed"]:
                print(f"  회귀! {r['id']}  PASS → FAIL")
            elif not prev[r["id"]] and r["passed"]:
                print(f"  개선  {r['id']}  FAIL → PASS")


if __name__ == "__main__":
    main()