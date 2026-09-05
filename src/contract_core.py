from __future__ import annotations

import csv
import datetime as dt
import decimal
import html
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MANIFEST = ROOT / "analysis_results" / "document_manifest.csv"
CORRECTION_CHAINS = ROOT / "analysis_results" / "phase2" / "correction_chains.csv"
SCHEMA = ROOT / "src" / "db" / "schema.sql"

ROW_RE = re.compile(r"<TR\b[^>]*>(.*?)</TR\s*>", re.I | re.S)
CELL_RE = re.compile(r"<T[DH]\b[^>]*>(.*?)</T[DH]\s*>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>", re.S)
SPACE_RE = re.compile(r"\s+")

FIELD_PATTERNS = {
    "contract_amount": re.compile(r"계약금액(?:\s*\(원\))?$"),
    "counterparty": re.compile(r"^(?:\d+[.)]?\s*)?계약\s*상대(?:방)?$"),
    "contract_start_date": re.compile(r"^(?:계약기간\s*[-/:]?\s*)?시작일$"),
    "contract_end_date": re.compile(r"^(?:계약기간\s*[-/:]?\s*)?종료일$"),
    "sales_ratio": re.compile(r"매출액\s*대비\s*\(%\)$"),
}
MISSING = {"", "-", "--", "- -", "미정", "해당사항없음", "해당 없음"}


@dataclass
class ExtractedValue:
    normalized: object | None
    raw: str | None
    source: str | None


def decode_xml(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def clean_cell(value: str) -> str:
    value = TAG_RE.sub(" ", value)
    return SPACE_RE.sub(" ", html.unescape(value)).strip()


def table_rows(text: str) -> list[list[str]]:
    return [
        [clean_cell(cell) for cell in CELL_RE.findall(row.group(1)) if clean_cell(cell)]
        for row in ROW_RE.finditer(text)
    ]


def normalize_amount(raw: str) -> int | None:
    value = raw.strip()
    if value in MISSING or any(x in value for x in ("미정", "공시 예정", "유보기간")):
        return None
    match = re.search(r"-?\d[\d,]*", value)
    return int(match.group().replace(",", "")) if match else None


def normalize_ratio(raw: str) -> decimal.Decimal | None:
    value = raw.strip()
    if value in MISSING or "미정" in value:
        return None
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", value)
    return decimal.Decimal(match.group().replace(",", "")) if match else None


def normalize_date(raw: str) -> str | None:
    value = raw.strip()
    if value in MISSING or any(x in value for x in ("미정", "예정")):
        return None
    match = re.search(r"(20\d{2})[.년/-]\s*(\d{1,2})[.월/-]\s*(\d{1,2})", value)
    if not match:
        return None
    try:
        return dt.date(*(int(x) for x in match.groups())).isoformat()
    except ValueError:
        return None


def normalize_text(raw: str) -> str | None:
    value = raw.strip()
    return None if value in MISSING or "공개 예정" in value else value


NORMALIZERS = {
    "contract_amount": normalize_amount,
    "counterparty": normalize_text,
    "contract_start_date": normalize_date,
    "contract_end_date": normalize_date,
    "sales_ratio": normalize_ratio,
}


def _label_key(value: str) -> str:
    value = re.sub(r"^[-ㆍ·]\s*", "", value)
    value = re.sub(r"^\d+[.)]?\s*", "", value)
    return SPACE_RE.sub(" ", value).strip()


def extract_contract_fields(text: str, source_path: str) -> dict[str, ExtractedValue]:
    result = {name: ExtractedValue(None, None, None) for name in FIELD_PATTERNS}
    rows = table_rows(text)
    for row_no, cells in enumerate(rows, 1):
        for cell_no, cell in enumerate(cells):
            label = _label_key(cell)
            for field, pattern in FIELD_PATTERNS.items():
                if not pattern.search(label):
                    continue
                # The value normally follows the label. Correction summary rows can
                # contain before/after values; the last cell is the corrected value.
                candidates = cells[cell_no + 1 :]
                if not candidates:
                    continue
                raw_value = candidates[-1] if len(candidates) > 1 else candidates[0]
                normalized = NORMALIZERS[field](raw_value)
                locator = f"{source_path}#table-row={row_no};cell={cell_no + 1};label={cell}"
                # Later form rows supersede the correction summary at the document top.
                result[field] = ExtractedValue(normalized, raw_value, locator)
    return result


def contract_manifest_rows() -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with MANIFEST.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["category"] != "exchange" or "단일판매ㆍ공급계약" not in row["report_name"]:
                continue
            rows.setdefault(row["accession_no"], row)
    return sorted(rows.values(), key=lambda row: row["accession_no"])


def open_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def correction_chain(connection: sqlite3.Connection, receipt_no: str) -> list[str]:
    """Return the explicit connected component in filing order."""
    edges = connection.execute(
        "SELECT original_receipt_no, correction_receipt_no FROM correction_links"
    ).fetchall()
    graph: dict[str, set[str]] = {}
    for edge in edges:
        graph.setdefault(edge[0], set()).add(edge[1])
        graph.setdefault(edge[1], set()).add(edge[0])
    seen = {receipt_no}
    stack = [receipt_no]
    while stack:
        node = stack.pop()
        for neighbor in graph.get(node, ()):
            if neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    placeholders = ",".join("?" for _ in seen)
    records = connection.execute(
        f"SELECT receipt_no FROM disclosures WHERE receipt_no IN ({placeholders}) "
        "ORDER BY filing_date, receipt_no",
        tuple(seen),
    ).fetchall()
    return [row[0] for row in records]


def latest_effective_contract(connection: sqlite3.Connection, receipt_no: str) -> dict | None:
    chain = correction_chain(connection, receipt_no)
    if not chain:
        return None
    placeholders = ",".join("?" for _ in chain)
    rows = connection.execute(
        f"""
        SELECT d.receipt_no, d.filing_date, d.source_path, co.company_name,
               c.*
        FROM disclosures d
        JOIN companies co ON co.company_id = d.company_id
        JOIN contracts c ON c.receipt_no = d.receipt_no
        WHERE d.receipt_no IN ({placeholders})
        ORDER BY d.filing_date, d.receipt_no
        """,
        tuple(chain),
    ).fetchall()
    if not rows:
        return None
    effective = dict(rows[-1])
    fields = ("contract_amount", "counterparty", "contract_start_date", "contract_end_date", "sales_ratio")
    for field in fields:
        for row in reversed(rows):
            # Raw presence means the filing explicitly carried the field. A normalized
            # null is meaningful (undisclosed/TBD) and must not fall back silently.
            if row[f"{field}_raw"] is not None:
                effective[field] = row[field]
                effective[f"{field}_raw"] = row[f"{field}_raw"]
                effective[f"{field}_source"] = row[f"{field}_source"]
                break
    effective["chain"] = chain
    effective["source"] = effective["source_path"]
    effective["company"] = effective["company_name"]
    return effective

