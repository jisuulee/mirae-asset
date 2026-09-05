"""Document-level orchestration for the minimal periodic parser."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .pdf_parser import parse_pdf
from .section_builder import attach_table_sections, build_sections
from .xml_parser import parse_markup, sha256_file


class PeriodicParser:
    def parse(
        self,
        source_path: Path,
        *,
        metadata: dict[str, Any],
        source_file: str | None = None,
        all_source_paths: list[Path] | None = None,
        raw_root: Path | None = None,
    ) -> dict[str, Any]:
        receipt_no = str(metadata.get("rcept_no") or metadata.get("receipt_no") or "")
        if not receipt_no:
            raise ValueError("metadata is missing rcept_no/receipt_no")
        source_file = source_file or source_path.as_posix()
        suffix = source_path.suffix.lower()
        if suffix == ".pdf":
            parsed = parse_pdf(source_path, receipt_no=receipt_no, source_file=source_file)
        elif suffix in {".xml", ".html", ".htm"}:
            parsed = parse_markup(source_path, receipt_no=receipt_no, source_file=source_file)
        else:
            raise ValueError(f"unsupported source format: {suffix}")

        sections, section_warnings = build_sections(parsed["blocks"], receipt_no=receipt_no)
        attach_table_sections(parsed["tables"], sections)
        warnings = sorted(set(parsed["warning_codes"] + section_warnings))
        source_paths = all_source_paths or [source_path]
        source_files = []
        for item in source_paths:
            relative = item.relative_to(raw_root).as_posix() if raw_root and item.is_relative_to(raw_root) else item.as_posix()
            source_files.append(
                {
                    "source_file": relative,
                    "source_sha256": sha256_file(item),
                    "format": item.suffix.lower().lstrip("."),
                    "parsed_as_main": item.resolve() == source_path.resolve(),
                }
            )
        return {
            "receipt_no": receipt_no,
            "corp_code": metadata.get("corp_code"),
            "company_name": metadata.get("corp_name") or metadata.get("company_name"),
            "receipt_date": metadata.get("rcept_date") or metadata.get("receipt_date"),
            "report_name": metadata.get("report_nm") or metadata.get("report_name"),
            "source_file": source_file,
            "source_sha256": parsed["source_sha256"],
            "source_files": source_files,
            "blocks": parsed["blocks"],
            "sections": sections,
            "tables": parsed["tables"],
            "parse_status": "partial" if warnings else parsed["parse_status"],
            "warning_codes": warnings,
        }


def join_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten only authoritative fields needed by retrieval exports."""
    metadata = row.get("metadata", row)
    return {
        "rcept_no": str(metadata.get("rcept_no") or row.get("rcept_no") or ""),
        "corp_code": metadata.get("corp_code"),
        "corp_name": metadata.get("corp_name"),
        "rcept_date": metadata.get("rcept_date") or metadata.get("rcept_dt"),
        "report_nm": metadata.get("report_nm"),
        "doc_group": metadata.get("doc_group"),
    }
