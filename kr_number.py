"""
한국어 금액 표현 → 원 단위 정수 파서.

용도 (두 곳에서 재사용):
  1. 하네스 채점기: 답변의 금액 표현을 값으로 파싱해 정답과 수치 비교
  2. 에이전트 verify 패스 v1: 문자열 대조 대신 값 대조

지원 형태:
  "333조 6,059억원"          → 333_605_900_000_000
  "22조 7,647억 6,416만원"   → 22_764_764_160_000
  "1,234억원"                → 123_400_000_000
  "333,605,900백만원"        → 333_605_900_000_000
  "5,000천원"                → 5_000_000
  "1,008원"                  → 1_008
"""

from __future__ import annotations

import re

_SUFFIX_MULT = {"조원": 10**12, "억원": 10**8, "만원": 10**4,
                "백만원": 10**6, "천원": 10**3, "원": 1}

# "X조 Y억 Z만 W원" 복합 표현 (조/억/만 중 최소 하나)
_COMPOSITE = re.compile(
    r"(?:(\d[\d,]*)\s*조)"
    r"(?:\s*(\d[\d,]*)\s*억)?"
    r"(?:\s*(\d[\d,]*)\s*만)?"
    r"(?:\s*(\d[\d,]*))?\s*원"
    r"|(?:(\d[\d,]*)\s*억)"
    r"(?:\s*(\d[\d,]*)\s*만)?"
    r"(?:\s*(\d[\d,]*))?\s*원"
    r"|(?:(\d[\d,]*)\s*만)\s*원"
)

# "1,234백만원" 단일 단위 표현
_SINGLE = re.compile(r"(\d[\d,]*)\s*(백만원|천원|원)")


def _i(s: str | None) -> int:
    return int(s.replace(",", "")) if s else 0


def extract_amounts(text: str) -> set[int]:
    """텍스트에서 금액 표현을 전부 찾아 원 단위 정수 집합으로 반환."""
    out: set[int] = set()
    consumed: list[tuple[int, int]] = []

    for m in _COMPOSITE.finditer(text):
        g = m.groups()
        if g[0] is not None:      # X조 [Y억] [Z만] [W]원
            won = _i(g[0]) * 10**12 + _i(g[1]) * 10**8 + _i(g[2]) * 10**4 + _i(g[3])
        elif g[4] is not None:    # X억 [Y만] [Z]원
            won = _i(g[4]) * 10**8 + _i(g[5]) * 10**4 + _i(g[6])
        else:                     # X만원
            won = _i(g[7]) * 10**4
        out.add(won)
        consumed.append(m.span())

    for m in _SINGLE.finditer(text):
        # 복합 표현에 이미 포함된 구간은 건너뜀
        if any(a <= m.start() < b for a, b in consumed):
            continue
        num, unit = m.group(1), m.group(2)
        out.add(_i(num) * _SUFFIX_MULT[unit])

    return out


def contains_amount(text: str, expected_won: int,
                    tolerance: float = 0.0) -> bool:
    """텍스트에 expected_won 과 일치하는 금액 표현이 있는지 (tolerance 는 비율)."""
    for v in extract_amounts(text):
        if v == expected_won:
            return True
        if tolerance and expected_won and abs(v - expected_won) / expected_won <= tolerance:
            return True
    return False


# ------------------------------------------------------------------ 자체 테스트
if __name__ == "__main__":
    cases = [
        ("333조 6,059억원", 333_605_900_000_000),
        ("22조 7,647억 6,416만원", 22_764_764_160_000),
        ("매출은 1,234억원이다", 123_400_000_000),
        ("333,605,900백만원", 333_605_900_000_000),
        ("5,000천원", 5_000_000),
        ("영업이익 41,210,500백만원", 41_210_500_000_000),
    ]
    for text, want in cases:
        got = extract_amounts(text)
        assert want in got, f"FAIL: {text} → {got} (want {want})"
        print(f"OK  {text:30s} → {want:,}")

    # 오답 검출 시나리오 (Q2 실사례): 4,160만 ≠ 6,416만
    wrong = "22조 7,647억 4,160만 원"
    assert not contains_amount(wrong, 22_764_764_160_000), "오답을 통과시킴!"
    print(f"OK  오답 검출: {wrong!r} ≠ 22,764,764,160,000")
    print("\n전체 통과")