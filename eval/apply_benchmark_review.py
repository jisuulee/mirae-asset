"""Validate and optionally apply human benchmark review exports.

The default is always dry-run. ``--apply`` is required to write a new benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from eval.build_benchmark_human_review import REVIEW_DECISIONS
from eval.evaluate_full_retrieval import read_jsonl, validate_benchmark
from eval.validate_full_benchmark import validate_database
from src.db.load_postgres import connect


LIST_FIELDS = ("accepted_gold_receipts", "accepted_gold_sections", "accepted_gold_tables", "accepted_pdf_pages")


def read_review(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    value = json.loads(text)
    if isinstance(value, dict) and "cards" in value:
        value = value["cards"]
    if not isinstance(value, list):
        raise ValueError("review export must be a JSON array or JSONL rows")
    return value


def validate_review_export(rows: list[dict[str, Any]], benchmark: list[dict[str, Any]], *, require_complete: bool = False) -> dict[str, Any]:
    by_id = {row["question_id"]: row for row in benchmark}
    eligible = {qid for qid, row in by_id.items() if row["human_review_status"] in {"needs_review", "ambiguous"}}
    seen: set[str] = set()
    pending = []
    for index, row in enumerate(rows, 1):
        qid = row.get("question_id")
        if not isinstance(qid, str) or qid not in eligible:
            raise ValueError(f"row {index}: unknown or non-reviewable question_id {qid!r}")
        if qid in seen:
            raise ValueError(f"duplicate review question_id: {qid}")
        seen.add(qid)
        decision = row.get("review_decision")
        if decision in (None, ""):
            pending.append(qid)
        elif decision not in REVIEW_DECISIONS:
            raise ValueError(f"{qid}: invalid review_decision {decision!r}")
        for field in LIST_FIELDS:
            value = row.get(field, [])
            if value is None:
                value = []
            if not isinstance(value, list):
                raise ValueError(f"{qid}: {field} must be a list")
            if field in {"accepted_gold_sections", "accepted_pdf_pages"} and any(type(item) is not int or item < 0 for item in value):
                raise ValueError(f"{qid}: {field} must contain non-negative integers")
            if field in {"accepted_gold_receipts", "accepted_gold_tables"} and any(not isinstance(item, str) or not item for item in value):
                raise ValueError(f"{qid}: {field} must contain non-empty strings")
        if decision == "REWRITE_QUESTION" and not str(row.get("proposed_question") or "").strip():
            raise ValueError(f"{qid}: proposed_question is required")
        if decision == "CHANGE_TARGET" and row.get("proposed_retrieval_target") not in {"section", "table", "either", "pdf_page"}:
            raise ValueError(f"{qid}: valid proposed_retrieval_target is required")
        if decision in {"ACCEPT_MULTI_GOLD", "CHANGE_GOLD"} and not any(row.get(field) for field in LIST_FIELDS):
            raise ValueError(f"{qid}: accepted gold is required for {decision}")
    if require_complete and (seen != eligible or pending):
        raise ValueError(f"complete review required: rows={len(seen)}/84 pending={len(pending)}")
    return {"rows": len(rows), "unique": len(seen), "pending": len(pending), "pending_question_ids": pending,
            "missing_question_ids": sorted(eligible - seen)}


def validate_accepted_gold(connection: Any, rows: list[dict[str, Any]], benchmark: list[dict[str, Any]]) -> None:
    by_id = {row["question_id"]: row for row in benchmark}
    for review in rows:
        qid = review["question_id"]
        namespace = by_id[qid]["loader_namespace"]
        section_ids = review.get("accepted_gold_sections") or []
        table_ids = review.get("accepted_gold_tables") or []
        receipts = set(review.get("accepted_gold_receipts") or [])
        if section_ids:
            found = connection.execute("""SELECT section_id,receipt_no FROM document_sections
                WHERE section_id=ANY(%s) AND metadata->>'loader_namespace'=%s""", (section_ids, namespace)).fetchall()
            if {int(row[0]) for row in found} != set(section_ids):
                raise ValueError(f"{qid}: accepted section does not exist in the benchmark namespace")
            if receipts and not {str(row[1]) for row in found}.issubset(receipts):
                raise ValueError(f"{qid}: accepted section receipt is not accepted")
        if table_ids:
            found = connection.execute("""SELECT table_id,receipt_no FROM retrieval_tables
                WHERE table_id=ANY(%s) AND metadata->>'loader_namespace'=%s""", (table_ids, namespace)).fetchall()
            if {str(row[0]) for row in found} != set(table_ids):
                raise ValueError(f"{qid}: accepted table does not exist in the benchmark namespace")
            if receipts and not {str(row[1]) for row in found}.issubset(receipts):
                raise ValueError(f"{qid}: accepted table receipt is not accepted")
        pages = review.get("accepted_pdf_pages") or []
        if pages and not receipts:
            raise ValueError(f"{qid}: accepted PDF pages require accepted receipt(s)")
        for page in pages:
            exists = connection.execute("""SELECT 1 FROM document_sections WHERE receipt_no=ANY(%s)
                AND source_locator=%s AND metadata->>'loader_namespace'=%s AND metadata->>'source_type'='pdf' LIMIT 1""",
                (list(receipts), f"page:{page}", namespace)).fetchone()
            if not exists:
                raise ValueError(f"{qid}: accepted PDF page does not exist")


def proposed_changes(rows: list[dict[str, Any]], benchmark: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {row["question_id"]: row for row in benchmark}
    changes = []
    for review in rows:
        decision = review.get("review_decision")
        if not decision:
            continue
        original = by_id[review["question_id"]]
        change: dict[str, Any] = {"question_id": review["question_id"], "decision": decision}
        if decision in {"ACCEPT_MULTI_GOLD", "CHANGE_GOLD"}:
            for source, target in (("accepted_gold_receipts", "gold_receipt_nos"),
                                   ("accepted_gold_sections", "gold_section_ids"),
                                   ("accepted_gold_tables", "gold_table_ids")):
                if review.get(source):
                    change[target] = {"from": original[target], "to": review[source]}
            if review.get("accepted_pdf_pages"):
                change["gold_source_locators"] = {"from": original["gold_source_locators"],
                                                  "to": [f"page:{page}" for page in review["accepted_pdf_pages"]]}
        if decision == "REWRITE_QUESTION":
            change["question"] = {"from": original["question"], "to": review["proposed_question"]}
        if decision == "CHANGE_TARGET":
            change["retrieval_target"] = {"from": original["retrieval_target"], "to": review["proposed_retrieval_target"]}
        changes.append(change)
    return changes


def _reconstruct_gold(connection: Any, updated: dict[str, Any], review: dict[str, Any]) -> None:
    section_ids = list(review.get("accepted_gold_sections") or updated["gold_section_ids"])
    table_ids = list(review.get("accepted_gold_tables") or updated["gold_table_ids"])
    receipts = list(review.get("accepted_gold_receipts") or updated["gold_receipt_nos"])
    pages = list(review.get("accepted_pdf_pages") or [])
    namespace = updated["loader_namespace"]
    if pages:
        page_rows = connection.execute("""SELECT section_id,receipt_no FROM document_sections
            WHERE receipt_no=ANY(%s) AND source_locator=ANY(%s) AND metadata->>'loader_namespace'=%s
              AND metadata->>'source_type'='pdf' ORDER BY section_id""",
            (receipts, [f"page:{page}" for page in pages], namespace)).fetchall()
        section_ids = sorted({*section_ids, *(int(row[0]) for row in page_rows)})
    table_rows = connection.execute("""SELECT table_id,section_id,receipt_no,source_locator,archive_identifier
        FROM retrieval_tables WHERE table_id=ANY(%s) AND metadata->>'loader_namespace'=%s ORDER BY table_id""",
        (table_ids, namespace)).fetchall() if table_ids else []
    section_ids = sorted({*section_ids, *(int(row[1]) for row in table_rows)})
    section_rows = connection.execute("""
        SELECT s.section_id,s.receipt_no,s.file_id,f.relative_path,f.sha256,s.source_locator,
               s.section_order,s.metadata->>'section_key',s.section_path
        FROM document_sections s JOIN disclosure_files f ON f.file_id=s.file_id
        WHERE s.section_id=ANY(%s) AND s.metadata->>'loader_namespace'=%s ORDER BY s.section_id
    """, (section_ids, namespace)).fetchall()
    if {int(row[0]) for row in section_rows} != set(section_ids):
        raise ValueError(f"{updated['question_id']}: cannot reconstruct all accepted section provenance")
    base_by_section = {}
    for section_id, receipt, file_id, source_file, sha256, locator, order, section_key, path in section_rows:
        base_by_section[int(section_id)] = {
            "receipt_no": str(receipt), "file_id": int(file_id), "source_file": str(source_file),
            "source_sha256": str(sha256), "source_locator": str(locator),
            "section_source_locator": str(locator), "section_id": int(section_id),
            "section_order": int(order), "section_key": str(section_key), "section_path": str(path),
        }
    provenance = []
    table_sections = set()
    for table_id, section_id, receipt, locator, archive_identifier in table_rows:
        table_sections.add(int(section_id))
        value = dict(base_by_section[int(section_id)])
        value.update(receipt_no=str(receipt), source_locator=str(locator), table_id=str(table_id),
                     table_archive_identifier=str(archive_identifier or ""))
        provenance.append(value)
    if updated["retrieval_target"] != "table":
        provenance.extend(value for section_id, value in base_by_section.items() if section_id not in table_sections)
    updated["gold_section_ids"] = section_ids
    updated["gold_table_ids"] = table_ids
    updated["gold_receipt_nos"] = sorted({*(str(value) for value in receipts),
                                           *(value["receipt_no"] for value in base_by_section.values())})
    if updated["retrieval_target"] == "pdf_page" and pages:
        updated["gold_source_locators"] = [f"page:{page}" for page in pages]
    elif updated["retrieval_target"] == "table" and table_rows:
        updated["gold_source_locators"] = [str(row[3]) for row in table_rows]
    else:
        updated["gold_source_locators"] = [value["source_locator"] for value in base_by_section.values()]
    updated["gold_provenance"] = provenance
    updated["gold_file_id"] = provenance[0]["file_id"] if provenance else None
    updated["gold_type"] = updated["retrieval_target"]


def apply_changes(connection: Any, rows: list[dict[str, Any]], benchmark: list[dict[str, Any]]) -> list[dict[str, Any]]:
    review_by_id = {row["question_id"]: row for row in rows}
    output = []
    for original in benchmark:
        review = review_by_id.get(original["question_id"])
        if not review or not review.get("review_decision"):
            output.append(original)
            continue
        decision = review["review_decision"]
        if decision == "DROP":
            continue
        updated = dict(original)
        if decision == "REWRITE_QUESTION":
            updated["question"] = review["proposed_question"].strip()
        if decision == "CHANGE_TARGET":
            updated["retrieval_target"] = review["proposed_retrieval_target"]
        if decision in {"ACCEPT_MULTI_GOLD", "CHANGE_GOLD"}:
            for source, target in (("accepted_gold_receipts", "gold_receipt_nos"),
                                   ("accepted_gold_sections", "gold_section_ids"),
                                   ("accepted_gold_tables", "gold_table_ids")):
                if review.get(source):
                    updated[target] = review[source]
            if review.get("accepted_pdf_pages"):
                updated["gold_source_locators"] = [f"page:{page}" for page in review["accepted_pdf_pages"]]
            _reconstruct_gold(connection, updated, review)
        elif decision == "CHANGE_TARGET":
            _reconstruct_gold(connection, updated, review)
        updated["human_review_status"] = "ambiguous" if decision == "AMBIGUOUS_KEEP" else "reviewed"
        updated["review_notes"] = review.get("reviewer_notes") or updated.get("review_notes")
        output.append(updated)
    validate_benchmark(output)
    provenance = validate_database(connection, output)
    if provenance["mismatch_count"]:
        raise ValueError("applied output failed stable provenance validation")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("review", type=Path)
    parser.add_argument("--benchmark", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--diff-output", type=Path, default=Path("reports/human_review/review_dry_run_diff.json"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    benchmark_hash = hashlib.sha256(args.benchmark.read_bytes()).hexdigest()
    benchmark, reviews = read_jsonl(args.benchmark), read_review(args.review)
    validation = validate_review_export(reviews, benchmark, require_complete=args.apply)
    provenance = None
    if args.database_url:
        with connect(args.database_url) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            target = [row for row in benchmark if row["human_review_status"] in {"needs_review", "ambiguous"}]
            provenance = validate_database(connection, target)
            if provenance["mismatch_count"]:
                raise ValueError("current benchmark stable provenance validation failed")
            validate_accepted_gold(connection, reviews, benchmark)
    changes = proposed_changes(reviews, benchmark)
    result = {"mode": "apply" if args.apply else "dry-run", "benchmark_sha256": benchmark_hash,
              "validation": validation, "stable_provenance": provenance, "proposed_changes": changes}
    args.diff_output.parent.mkdir(parents=True, exist_ok=True)
    args.diff_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.apply:
        if args.output is None:
            parser.error("--apply requires --output; the source benchmark is never overwritten implicitly")
        if not args.database_url:
            parser.error("--apply requires DATABASE_URL for stable provenance reconstruction")
        with connect(args.database_url) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            applied = apply_changes(connection, reviews, benchmark)
        with args.output.open("w", encoding="utf-8", newline="\n") as handle:
            for row in applied:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    if hashlib.sha256(args.benchmark.read_bytes()).hexdigest() != benchmark_hash:
        raise RuntimeError("source benchmark changed")
    print(json.dumps({"mode": result["mode"], "rows": validation["rows"], "pending": validation["pending"],
                      "proposed_changes": len(changes)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
