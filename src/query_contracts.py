from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from contract_core import latest_effective_contract, open_database


OUTPUT_FIELDS = ("company", "contract_amount", "counterparty", "filing_date", "receipt_no", "source")


def display_row(row: dict) -> dict:
    source = row.get("contract_amount_source") or row.get("counterparty_source") or row.get("source_path") or row.get("source")
    return {
        "company": row.get("company") or row.get("company_name"),
        "contract_amount": row.get("contract_amount"),
        "counterparty": row.get("counterparty"),
        "filing_date": row.get("filing_date"),
        "receipt_no": row.get("receipt_no"),
        "source": source,
    }


def list_contracts(db_path: Path, company: str, year: int | None = None) -> list[dict]:
    connection = open_database(db_path)
    sql = """
        SELECT co.company_name, d.filing_date, d.receipt_no, d.source_path,
               c.contract_amount, c.contract_amount_source, c.counterparty, c.counterparty_source
        FROM disclosures d
        JOIN companies co ON co.company_id=d.company_id
        JOIN contracts c ON c.receipt_no=d.receipt_no
        WHERE co.company_name = ?
    """
    params: list[object] = [company]
    if year is not None:
        sql += " AND substr(d.filing_date, 1, 4) = ?"
        params.append(str(year))
    sql += " ORDER BY d.filing_date, d.receipt_no"
    rows = [display_row(dict(row)) for row in connection.execute(sql, params)]
    connection.close()
    return rows


def latest_contract(db_path: Path, company: str, receipt_no: str) -> dict | None:
    connection = open_database(db_path)
    owner = connection.execute(
        "SELECT co.company_name FROM disclosures d JOIN companies co ON co.company_id=d.company_id "
        "WHERE d.receipt_no=?",
        (receipt_no,),
    ).fetchone()
    if owner is None or owner[0] != company:
        connection.close()
        return None
    effective = latest_effective_contract(connection, receipt_no)
    connection.close()
    return display_row(effective) if effective else None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Query the V0 exchange contract database")
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list", help="List a company's contract filings")
    list_parser.add_argument("--db", type=Path, default=Path("artifacts/v0_contracts.sqlite"))
    list_parser.add_argument("--company", required=True)
    list_parser.add_argument("--year", type=int)
    latest_parser = sub.add_parser("latest", help="Resolve explicit corrections for one receipt")
    latest_parser.add_argument("--db", type=Path, default=Path("artifacts/v0_contracts.sqlite"))
    latest_parser.add_argument("--company", required=True)
    latest_parser.add_argument("--receipt-no", required=True)
    args = parser.parse_args()
    if args.command == "list":
        result = list_contracts(args.db.resolve(), args.company, args.year)
    else:
        result = latest_contract(args.db.resolve(), args.company, args.receipt_no)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
