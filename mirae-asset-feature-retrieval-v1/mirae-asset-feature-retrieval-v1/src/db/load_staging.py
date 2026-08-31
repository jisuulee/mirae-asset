"""Read-only transformation from the Role 2 handoff and raw XML to staging rows."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from contract_core import DATA, decode_xml, extract_contract_fields

ROLE2 = ROOT / "role2_final" / "data"
DOCUMENT_INDEX = ROLE2 / "document_index.jsonl"
METADATA = ROLE2 / "metadata_normalized.jsonl"
LINEAGE = ROLE2 / "correction_lineage.jsonl"
FILE_MANIFEST = ROOT / "analysis_results" / "document_manifest.csv"
CONTRACT_SUBTYPE = "단일판매공급계약체결"
PARSER_VERSION = "contract-v1-role2-2026-08-28"


class StagingError(ValueError):
    pass


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise StagingError(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def normalize_company_name(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _metadata_by_doc_id() -> dict[str, dict[str, Any]]:
    result = {}
    for row in read_jsonl(METADATA):
        result[row["doc_id"]] = row
    return result


def build_documents() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sidecars = _metadata_by_doc_id()
    companies: dict[str, dict[str, Any]] = {}
    disclosures: list[dict[str, Any]] = []
    seen_receipts: set[str] = set()
    for index in read_jsonl(DOCUMENT_INDEX):
        doc_id = index["doc_id"]
        sidecar = sidecars.get(doc_id)
        if sidecar is None or sidecar["rcept_no"] != index["rcept_no"]:
            raise StagingError(f"metadata/index join mismatch: {doc_id}")
        metadata = index["metadata"]
        receipt_no = metadata["rcept_no"]
        if receipt_no in seen_receipts:
            raise StagingError(f"duplicate receipt_no: {receipt_no}")
        seen_receipts.add(receipt_no)
        corp_code = metadata["corp_code"]
        company = {
            "corp_code": corp_code,
            "stock_code": metadata.get("stock_code"),
            "company_name": metadata["corp_name"],
            "company_name_normalized": normalize_company_name(metadata["corp_name"]),
        }
        previous = companies.setdefault(corp_code, company)
        if previous != company:
            raise StagingError(f"inconsistent company identity for {corp_code}")
        disclosures.append({
            "doc_id": doc_id,
            "receipt_no": receipt_no,
            "corp_code": corp_code,
            "receipt_date": metadata["rcept_date"],
            "category": metadata["doc_group"],
            "report_name": metadata["report_nm"],
            "report_type": metadata.get("doc_subtype"),
            "is_correction_name": bool(metadata.get("is_correction")),
            "is_correction_rm": False,
            "metadata": {
                "canonical": metadata,
                "content_summary": index["content_summary"],
                "lineage": index["lineage"],
                "quality": index["quality"],
                "metadata_validation": sidecar["metadata_validation"],
                "parse_status": sidecar["parse_status"],
            },
        })
    if len(sidecars) != len(disclosures):
        raise StagingError("metadata/index record counts differ")
    return sorted(companies.values(), key=lambda x: x["corp_code"]), disclosures


def build_files() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    order: dict[str, int] = {}
    with FILE_MANIFEST.open(encoding="utf-8-sig", newline="") as handle:
        for source in csv.DictReader(handle):
            receipt_no = source["accession_no"]
            order[receipt_no] = order.get(receipt_no, 0) + 1
            relative_path = source["relative_path"]
            path = DATA / relative_path
            if not path.is_file():
                raise StagingError(f"source file missing: {relative_path}")
            raw = path.read_bytes()
            rows.append({
                "receipt_no": receipt_no,
                "relative_path": relative_path,
                "file_role": "main" if order[receipt_no] == 1 else "attachment",
                "root_tag": source.get("root_tag") or None,
                "encoding": source.get("encoding_used") or None,
                "byte_size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "source_order": order[receipt_no],
            })
    return rows


def build_contracts(disclosures: Iterable[dict[str, Any]], files: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    contract_receipts = {
        row["receipt_no"] for row in disclosures
        if row["category"] == "exchange" and row["report_type"] == CONTRACT_SUBTYPE
    }
    source_by_receipt: dict[str, list[dict[str, Any]]] = {}
    for source in files:
        if source["receipt_no"] in contract_receipts:
            source_by_receipt.setdefault(source["receipt_no"], []).append(source)
    result = []
    for receipt_no in sorted(contract_receipts):
        sources = sorted(source_by_receipt.get(receipt_no, []), key=lambda x: x["source_order"])
        if not sources:
            raise StagingError(f"contract has no source XML: {receipt_no}")
        selected = sources[0]
        extracted = extract_contract_fields(
            decode_xml((DATA / selected["relative_path"]).read_bytes()), selected["relative_path"]
        )
        provenance = {
            field: {"raw": value.raw, "locator": value.source, "source_file": selected["relative_path"]}
            for field, value in extracted.items()
        }
        result.append({
            "receipt_no": receipt_no,
            "source_relative_path": selected["relative_path"],
            "contract_name_raw": None,
            "contract_name": None,
            "contract_amount_raw": extracted["contract_amount"].raw,
            "contract_amount": extracted["contract_amount"].normalized,
            "contract_currency": "KRW",
            "counterparty_raw": extracted["counterparty"].raw,
            "counterparty": extracted["counterparty"].normalized,
            "contract_start_date_raw": extracted["contract_start_date"].raw,
            "contract_start_date": extracted["contract_start_date"].normalized,
            "contract_end_date_raw": extracted["contract_end_date"].raw,
            "contract_end_date": extracted["contract_end_date"].normalized,
            "sales_ratio_raw": extracted["sales_ratio"].raw,
            "sales_ratio": extracted["sales_ratio"].normalized,
            "field_provenance": provenance,
            "parser_version": PARSER_VERSION,
        })
    return result


def build_correction_links() -> list[dict[str, Any]]:
    links = []
    for row in read_jsonl(LINEAGE):
        resolution = row["resolution"]
        if not (
            resolution.get("status") == "linked_exact"
            and resolution.get("method") == "company_date_report"
            and resolution.get("confidence") == 1.0
        ):
            continue
        predecessor = row["lineage"].get("parent_rcept_no")
        successor = row["rcept_no"]
        evidence = row.get("correction_evidence") or {}
        explicit_date = evidence.get("target_submission_date")
        if predecessor is None or explicit_date is None:
            raise StagingError(f"exact correction lacks parent/date: {successor}")
        links.append({
            "predecessor_receipt_no": predecessor,
            "successor_receipt_no": successor,
            "link_method": "explicit_original_filing_date",
            "confidence": "high",
            "explicit_original_date": explicit_date,
            "evidence": {
                "role2_resolution": resolution,
                "role2_correction_evidence": evidence,
                "transformation": "linked_exact/company_date_report -> explicit_original_filing_date/high",
            },
            "source_analysis_path": "role2_final/data/correction_lineage.jsonl",
        })
    return links


def build_all() -> dict[str, list[dict[str, Any]]]:
    companies, disclosures = build_documents()
    files = build_files()
    return {
        "companies": companies,
        "disclosures": disclosures,
        "disclosure_files": files,
        "contracts": build_contracts(disclosures, files),
        "correction_links": build_correction_links(),
    }


def summary(data: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {name: len(rows) for name, rows in data.items()}


if __name__ == "__main__":
    print(json.dumps(summary(build_all()), ensure_ascii=False, indent=2))
