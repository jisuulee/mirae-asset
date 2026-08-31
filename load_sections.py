"""
load_sections.py — export_sections.py 산출 JSONL 을 document_sections 에 적재

원칙:
  - 멱등: 문서(receipt_no) 단위로 DELETE 후 INSERT, 재실행 안전
  - file_id 는 disclosure_files 의 main 파일(source_order 최소)로 조회
  - disclosures 에 없는 receipt_no 는 스킵하고 집계 (FK 위반 방지)

사용:
  python load_sections.py sections_sample.jsonl
  python load_sections.py sections_full.jsonl --batch 500
"""

from __future__ import annotations

import argparse
import json
import sys

import psycopg2
import psycopg2.extras

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os


def conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "disclosure"),
        user=os.environ.get("POSTGRES_USER", "disclosure"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


INSERT_SQL = """
INSERT INTO document_sections
  (receipt_no, file_id, section_order, section_path, title_raw,
   title_normalized, concept, body_text, source_locator, metadata)
VALUES (%(receipt_no)s, %(file_id)s, %(section_order)s, %(section_path)s,
        %(title_raw)s, %(title_normalized)s, %(concept)s, %(body_text)s,
        %(source_locator)s, %(metadata)s)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--batch", type=int, default=300, help="커밋 단위 (문서 수)")
    args = ap.parse_args()

    # 문서별로 묶기 (스트리밍: receipt_no 가 파일 내 연속이라고 가정 — export 산출 특성)
    cx = conn()
    cur = cx.cursor()

    # main file_id 매핑 일괄 로드
    cur.execute("""SELECT DISTINCT ON (receipt_no) receipt_no, file_id
                   FROM disclosure_files ORDER BY receipt_no, source_order""")
    file_map = dict(cur.fetchall())
    print(f"file_id 매핑 {len(file_map)}건 로드")

    n_docs = n_rows = n_skip_fk = 0
    pending: list[dict] = []
    cur_rcept: str | None = None
    docs_in_tx = 0

    def flush_doc(rows: list[dict]) -> None:
        nonlocal n_docs, n_rows, n_skip_fk, docs_in_tx
        if not rows:
            return
        rcept = rows[0]["receipt_no"]
        fid = file_map.get(rcept)
        if fid is None:
            n_skip_fk += 1
            return
        cur.execute("DELETE FROM document_sections WHERE receipt_no=%s", (rcept,))
        for r in rows:
            r["file_id"] = fid
            r["metadata"] = json.dumps(r.get("metadata") or {}, ensure_ascii=False)
            cur.execute(INSERT_SQL, r)
        n_docs += 1
        n_rows += len(rows)
        docs_in_tx += 1

    with open(args.src, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if cur_rcept is None:
                cur_rcept = row["receipt_no"]
            if row["receipt_no"] != cur_rcept:
                flush_doc(pending)
                pending, cur_rcept = [], row["receipt_no"]
                if docs_in_tx >= args.batch:
                    cx.commit()
                    docs_in_tx = 0
                    print(f"  ... {n_docs} docs / {n_rows} rows", file=sys.stderr)
            pending.append(row)
        flush_doc(pending)
    cx.commit()

    cur.execute("SELECT count(*) FROM document_sections")
    total = cur.fetchone()[0]
    print(f"적재 완료: 문서 {n_docs} / 행 {n_rows} (FK 스킵 {n_skip_fk})")
    print(f"document_sections 총 행수: {total}")
    cx.close()


if __name__ == "__main__":
    main()
