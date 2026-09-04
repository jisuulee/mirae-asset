"""
server.py — 평가용 API 서버 (대회 스키마 준수)

평가 요청:  GET /answer?question_id={id}&question={평가 질의}
응답(JSON): {question_id, question, retrieved_context, think_trace, answer}

설계 원칙:
  - 5xx 를 내지 않는다: 내부 오류도 200 + 한계고지 answer 로 응답
    (평가 규정상 5xx 는 재시도 2회 후 실패 처리되므로, 구조화된 실패가 항상 낫다)
  - 동시성 1: HCX 테스트 키의 분당 호출 제한(429 실측) 보호를 위해
    질의를 직렬 처리한다. 평가 타임아웃 300초 내 여유 충분
  - 멱등 캐시: 같은 question_id 재요청(평가측 재시도) 시 캐시 응답 반환
  - 전 요청을 logs/requests.jsonl 에 기록 (평가 기간 사후 분석용)

실행:
  TOOLS_BACKEND=real 이 기본. .env 에 CLOVASTUDIO_API_KEY / POSTGRES_* 필요.
  uvicorn server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from pathlib import Path

os.environ.setdefault("TOOLS_BACKEND", "real")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from agent import DisclosureAgent

app = FastAPI(title="Disclosure Analyst API")

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "requests.jsonl"

_agent = None
_agent_err = None
_lock = threading.Lock()          # 동시성 1 (HCX rate limit 보호)
_cache: dict[str, dict] = {}      # question_id -> 응답 (멱등)


def _get_agent():
    global _agent, _agent_err
    if _agent is None:
        try:
            _agent = DisclosureAgent()
            _agent_err = None
        except Exception as exc:  # noqa: BLE001
            _agent_err = f"{type(exc).__name__}: {exc}"
            raise
    return _agent


@app.on_event("startup")
def warmup() -> None:
    try:
        _get_agent()
        print("[startup] agent ready (backend=%s)" % os.environ.get("TOOLS_BACKEND"))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("[startup] agent init FAILED - /health 확인. 요청 시 재시도됩니다.")


@app.get("/health")
def health() -> dict:
    db_ok = False
    try:
        import tools_real
        tools_real._load_universe()
        db_ok = len(tools_real.UNIVERSE) == 70
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "db": str(exc)[:200],
                "agent_error": _agent_err}
    return {"status": "ok" if db_ok and _agent_err is None else "degraded",
            "db_universe": 70 if db_ok else "mismatch",
            "agent_error": _agent_err,
            "cached_answers": len(_cache)}


def _fallback(question_id: str, question: str, reason: str) -> dict:
    """내부 오류 시에도 스키마를 지키는 구조화된 실패 응답."""
    return {
        "question_id": question_id,
        "question": question,
        "retrieved_context": "",
        "think_trace": f"[internal] 처리 실패: {reason}",
        "answer": ("일시적인 내부 오류로 이 질의를 처리하지 못했습니다. "
                   "제공된 공시 데이터 기반 답변을 생성할 수 없어 한계를 고지합니다."),
    }


@app.get("/answer")
def answer(question_id: str = Query(...), question: str = Query(...)) -> JSONResponse:
    started = time.time()

    if question_id in _cache:      # 멱등: 평가측 재시도 대응
        return JSONResponse(_cache[question_id])

    with _lock:                    # 직렬 처리
        if question_id in _cache:
            return JSONResponse(_cache[question_id])
        try:
            result = _get_agent().answer(question_id, question)
        except Exception as exc:   # noqa: BLE001
            traceback.print_exc()
            result = _fallback(question_id, question, f"{type(exc).__name__}: {exc}")

    result.setdefault("question_id", question_id)
    result.setdefault("question", question)
    result.setdefault("retrieved_context", "")
    result.setdefault("think_trace", "")
    result.setdefault("answer", "")

    _cache[question_id] = result
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(),
                                "elapsed_s": round(time.time() - started, 1),
                                **result}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return JSONResponse(result)
