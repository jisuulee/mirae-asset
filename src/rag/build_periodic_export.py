"""Build bounded periodic section/table exports; full runs require explicit --all."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .periodic_parser import PeriodicParser, join_metadata


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def metadata_map(path: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for row in load_jsonl(path):
        metadata = join_metadata(row)
        if metadata["doc_group"] == "periodic":
            result[metadata["rcept_no"]] = metadata
    return result


def discover_receipts(raw_root: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for path in raw_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".xml", ".pdf", ".html", ".htm"}:
            continue
        receipt = path.name[:14]
        if receipt.isdigit():
            result.setdefault(receipt, []).append(path)
    return {key: sorted(value, key=lambda p: p.name) for key, value in result.items()}


def choose_main_source(receipt_no: str, sources: list[Path]) -> Path:
    for suffix in (".xml", ".pdf", ".html", ".htm"):
        exact = next((p for p in sources if p.name.lower() == receipt_no + suffix), None)
        if exact:
            return exact
    return sources[0]


def select_pilot(index_rows: Iterable[dict[str, Any]], available: set[str], size: int) -> list[str]:
    rows = [row for row in index_rows if (row.get("metadata") or {}).get("doc_group") == "periodic" and str(row.get("rcept_no")) in available]
    chosen: list[str] = []

    def add(candidates: Iterable[dict[str, Any]], count: int) -> None:
        for row in candidates:
            receipt = str(row["rcept_no"])
            if receipt not in chosen:
                chosen.append(receipt)
            if len(chosen) >= size or count <= 1:
                break
            count -= 1

    pdf_rows = [r for r in rows if (r.get("content_summary") or {}).get("has_pdf_fallback")]
    add(pdf_rows, 3)
    add(sorted(rows, key=lambda r: (r.get("content_summary") or {}).get("table_count", 0), reverse=True), 5)
    add(sorted(rows, key=lambda r: (r.get("content_summary") or {}).get("section_count", 0), reverse=True), 5)
    add(sorted(rows, key=lambda r: (r.get("content_summary") or {}).get("section_count", 0)), 5)
    add(sorted(rows, key=lambda r: ((r.get("content_summary") or {}).get("record_size_bytes", 0) / ((r.get("content_summary") or {}).get("table_count", 0) + 1)), reverse=True), 5)
    for source_count in (2, 3, 1):
        add([r for r in rows if (r.get("content_summary") or {}).get("source_file_count") == source_count], 4)
    add(sorted(rows, key=lambda r: str(r["rcept_no"])), size)
    return chosen[:size]


def export_document(document: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    common = {key: document.get(key) for key in ("receipt_no", "corp_code", "company_name", "receipt_date", "report_name", "source_file", "source_sha256", "parse_status", "warning_codes")}
    section_rows = []
    for section in document["sections"]:
        row = {**common, **section}
        row["warning_codes"] = sorted(set(common["warning_codes"] or []) | set(section.get("warning_codes") or []))
        section_rows.append(row)
    return section_rows, document["tables"]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def build(args: argparse.Namespace) -> dict[str, Any]:
    metadata = metadata_map(args.metadata)
    sources = discover_receipts(args.raw_root)
    if args.receipts:
        receipts = [item.strip() for item in args.receipts.split(",") if item.strip()]
    elif args.pilot:
        receipts = select_pilot(load_jsonl(args.document_index), set(sources), args.pilot)
    elif args.all:
        receipts = sorted(set(metadata) & set(sources))
    else:
        raise ValueError("choose --receipts, --pilot, or explicit --all")
    if args.pilot and not 1 <= args.pilot <= 50:
        raise ValueError("pilot size must be between 1 and 50")

    args.output.mkdir(parents=True, exist_ok=True)
    parser = PeriodicParser()
    document_summaries: list[dict[str, Any]] = []
    exceptions: list[dict[str, str]] = []
    section_count = 0
    table_count = 0
    with (args.output / "sections.jsonl").open("w", encoding="utf-8", newline="\n") as section_handle, (args.output / "tables.jsonl").open("w", encoding="utf-8", newline="\n") as table_handle:
        for position, receipt in enumerate(receipts, start=1):
            if receipt not in metadata or receipt not in sources:
                exceptions.append({"receipt_no": receipt, "error": "metadata_or_source_missing"})
                continue
            try:
                main = choose_main_source(receipt, sources[receipt])
                relative = main.relative_to(args.raw_root).as_posix()
                document = parser.parse(main, metadata=metadata[receipt], source_file=relative, all_source_paths=sources[receipt], raw_root=args.raw_root)
                section_rows, table_rows = export_document(document)
                for row in section_rows:
                    section_handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                for row in table_rows:
                    table_handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                section_count += len(section_rows)
                table_count += len(table_rows)
                document_summaries.append({
                    **{key: document.get(key) for key in ("receipt_no", "corp_code", "company_name", "receipt_date", "report_name", "source_file", "source_sha256", "source_files", "parse_status", "warning_codes")},
                    "block_count": len(document["blocks"]),
                    "block_types": [block["block_type"] for block in document["blocks"]],
                    "block_provenance_complete": all(block.get("provenance") for block in document["blocks"]),
                    "section_count": len(section_rows),
                    "section_titles": [section.get("title_normalized") for section in document["sections"]],
                    "table_count": len(table_rows),
                    "body_chars": sum(len(section.get("body_text") or "") for section in document["sections"]),
                })
                print(f"[{position}/{len(receipts)}] {receipt}: parsed")
            except Exception as exc:  # batch isolation is intentional
                exceptions.append({"receipt_no": receipt, "error": f"{type(exc).__name__}: {exc}"})
                print(f"[{position}/{len(receipts)}] {receipt}: failed: {exc}")

    write_jsonl(args.output / "documents.jsonl", document_summaries)
    summary = {"requested_receipts": len(receipts), "parsed_documents": len(document_summaries), "sections": section_count, "tables": table_count, "exceptions": exceptions, "receipt_nos": receipts}
    (args.output / "build_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def default_raw_root() -> Path:
    candidates = [path / "corpus" / "raw" for path in Path("data").iterdir() if path.is_dir()]
    return next(path for path in candidates if path.exists())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=default_raw_root())
    parser.add_argument("--metadata", type=Path, default=Path("role2_final/data/metadata_normalized.jsonl"))
    parser.add_argument("--document-index", type=Path, default=Path("role2_final/data/document_index.jsonl"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--receipts", help="comma-separated receipt numbers")
    group.add_argument("--pilot", type=int, help="stratified pilot size (maximum 50)")
    group.add_argument("--all", action="store_true", help="explicit future full-corpus run")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
