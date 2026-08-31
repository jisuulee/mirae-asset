"""
export_sections.py — parsed_disclosures.jsonl → document_sections 적재용 JSONL

갭 ①(정기공시 본문 미적재) 해소용. 1번 산출물의 sections/block_sequence/tables 를
2번 스키마(document_sections)에 1:1 매핑되는 행으로 변환한다.

변환 규칙:
  - path: level=1 헤딩("I. 회사의 개요") + level=2("1. 회사의 개요") 조합
          → "I. 회사의 개요 > 1. 회사의 개요"  (우리 계약의 path 형식)
  - body: 섹션 블록 범위의 paragraph 텍스트 + table 은 마크다운 렌더로 병합
  - 섹션 없는 문서(1,472건: exchange/major 대부분):
          전체 블록을 path="본문" 단일 섹션으로 fallback → degrade 경로의
          "본문 read" 가 성립
  - 표 렌더: raw_rows 기반, 셀 텍스트 원문 보존 (숫자·단위 변형 금지 — MUST_KNOW §11)
  - level=1 헤더 전용 섹션(자식 존재)은 자식에 병합되므로 단독 행 생성 안 함

사용:
  python export_sections.py <parsed_disclosures.jsonl> <출력.jsonl> [--group periodic]
  예) 샘플 검증:  python export_sections.py sample_disclosures.jsonl sections_sample.jsonl
      전체(1번):  python export_sections.py data/processed/parsed_disclosures.jsonl \\
                  sections_full.jsonl            # 26.6GB, line-by-line 스트리밍
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterator


# ------------------------------------------------------------------ 표 렌더
def render_table(table: dict[str, Any]) -> str:
    """raw_rows → 마크다운. 셀 원문 보존, colspan 은 빈 칸으로 유지."""
    rows = table.get("raw_rows") or []
    lines: list[str] = []
    for r_i, row in enumerate(rows):
        cells = []
        for cell in row:
            txt = (cell.get("text") or "").replace("\n", " ").replace("|", "／").strip()
            cells.append(txt)
            # colspan 자리 보존 (병합 셀 뒤 빈 칸)
            for _ in range(max(0, int(cell.get("colspan") or 1) - 1)):
                cells.append("")
        lines.append("| " + " | ".join(cells) + " |")
        if r_i == 0:
            lines.append("|" + "---|" * len(cells))
    return "\n".join(lines)


# ------------------------------------------------------------------ 본문 조립
def blocks_to_text(blocks: list[dict[str, Any]],
                   tables_by_id: dict[str, dict[str, Any]]) -> tuple[str, int]:
    parts: list[str] = []
    n_tables = 0
    for b in blocks:
        bt = b.get("block_type")
        if bt in ("paragraph", "heading"):
            txt = (b.get("text") or "").strip()
            if txt:
                parts.append(txt)
        elif bt == "table":
            t = tables_by_id.get(b.get("table_id") or "")
            if t is not None:
                parts.append(render_table(t))
                n_tables += 1
    return "\n\n".join(parts).strip(), n_tables


def _norm_title(s: str) -> str:
    return " ".join((s or "").split())


# ------------------------------------------------------------------ 문서 변환
def doc_to_rows(rec: dict[str, Any]) -> Iterator[dict[str, Any]]:
    md = rec["main_document"]
    content = md.get("content") or {}
    manifest = rec["metadata"]["manifest"]
    rcept = rec["rcept_no"]
    src = (md.get("source_file") or {}).get("relative_path", "")
    seq = content.get("block_sequence") or []
    tables_by_id = {t["table_id"]: t for t in (content.get("tables") or [])}
    sections = content.get("sections") or []

    def base(order: int, path: str, title: str, body: str, n_tab: int,
             sec_id: str | None, extra: dict | None = None) -> dict[str, Any]:
        meta = {"doc_id": rec["doc_id"], "table_count": n_tab,
                "section_id": sec_id, "parse_status": md.get("parse_status"),
                "doc_group": manifest.get("doc_group"),
                "doc_subtype": manifest.get("doc_subtype")}
        if extra:
            meta.update(extra)
        return {"receipt_no": rcept, "section_order": order,
                "section_path": path, "title_raw": title,
                "title_normalized": _norm_title(title), "concept": None,
                "body_text": body,
                "source_locator": f"{src}#{sec_id or 'doc'}",
                "metadata": meta}

    # ---- fallback: 섹션 없는 문서 → 단일 pseudo-section
    if not sections:
        body, n_tab = blocks_to_text(seq, tables_by_id)
        if not body:
            body = (content.get("body_text") or "").strip()
        if body:
            yield base(0, "본문", content.get("title") or manifest.get("report_nm") or "본문",
                       body, n_tab, None, {"pseudo_section": True})
        return

    # ---- 계층 path 조합
    # 각 섹션의 부모 = 자기보다 앞이면서 level 이 더 작은 가장 가까운 헤딩
    has_child = set()
    for i, s in enumerate(sections):
        lv = s.get("level")
        if lv is None:
            continue
        for j in range(i - 1, -1, -1):
            plv = sections[j].get("level")
            if plv is not None and plv < lv:
                has_child.add(j)
                break

    order = 0
    for i, s in enumerate(sections):
        lv = s.get("level")
        title = _norm_title(s.get("title") or "")
        # 표지/목차 등 level=None 섹션과, 자식이 있는 상위 헤더는 단독 행 생략
        if lv is None or i in has_child:
            continue
        # path 조합
        parents: list[str] = []
        cur_lv = lv
        for j in range(i - 1, -1, -1):
            plv = sections[j].get("level")
            if plv is not None and plv < cur_lv:
                parents.append(_norm_title(sections[j].get("title") or ""))
                cur_lv = plv
                if cur_lv == 1:
                    break
        path = " > ".join(reversed(parents + []))
        path = f"{path} > {title}" if path else title

        blocks = seq[s.get("start_block_index", 0): s.get("end_block_index", -1) + 1]
        body, n_tab = blocks_to_text(blocks, tables_by_id)
        if not body:
            continue
        yield base(order, path, title, body, n_tab, s.get("section_id"))
        order += 1


# ------------------------------------------------------------------ 메인
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--group", default="",
                    help="periodic|major|exchange|holding 만 필터 (기본 전체)")
    args = ap.parse_args()

    n_doc = n_row = n_fallback = n_skip = 0
    stats: dict[str, int] = {}
    with open(args.src, encoding="utf-8") as fin, \
         open(args.dst, "w", encoding="utf-8") as fout:
        for line in fin:                      # 26.6GB 대비 line-by-line 스트리밍
            if not line.strip():
                continue
            rec = json.loads(line)
            g = rec["metadata"]["manifest"].get("doc_group")
            if args.group and g != args.group:
                n_skip += 1
                continue
            n_doc += 1
            rows = list(doc_to_rows(rec))
            if rows and rows[0]["metadata"].get("pseudo_section"):
                n_fallback += 1
            for r in rows:
                fout.write(json.dumps(r, ensure_ascii=False) + "\n")
                n_row += 1
            stats[g] = stats.get(g, 0) + len(rows)
            if n_doc % 200 == 0:
                print(f"  ... {n_doc} docs / {n_row} sections", file=sys.stderr)

    print(f"문서 {n_doc}건 → 섹션 {n_row}행 (fallback 단일섹션 {n_fallback}건, "
          f"필터 제외 {n_skip}건)")
    for g, c in sorted(stats.items()):
        print(f"  {g:10s} {c}행")


if __name__ == "__main__":
    main()
