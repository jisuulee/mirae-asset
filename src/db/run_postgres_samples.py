"""Run reproducible Retrieval V0 smoke queries and emit JSON evidence."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from db.load_postgres import connect
from retrieval.contracts import ContractSearch, get_contract, get_largest_contract, get_latest_observed_contract, search_contracts
from retrieval.corrections import get_correction_history, get_latest_observed


def compact(row):
    if row is None:
        return None
    keep = ("receipt_no", "company", "receipt_date", "report_name", "contract_amount",
            "contract_amount_raw", "counterparty", "contract_start_date", "contract_end_date",
            "sales_ratio", "source_path", "observation_status", "chain_receipt_nos")
    return {key: row.get(key) for key in keep if key in row}


def run(database_url: str) -> dict:
    with connect(database_url) as connection:
        company = connection.execute(
            "SELECT co.company_name FROM contracts c JOIN disclosures d USING(receipt_no) JOIN companies co USING(company_id) GROUP BY co.company_name ORDER BY count(*) DESC, co.company_name LIMIT 1"
        ).fetchone()[0]
        period_rows = search_contracts(connection, ContractSearch(
            company=company, date_from=date(2025, 1, 1), date_to=date(2025, 12, 31), limit=5
        ))
        receipt_no = period_rows[0]["receipt_no"]
        link_receipts = [row[0] for row in connection.execute("""
            SELECT l.predecessor_receipt_no FROM correction_links l
            JOIN contracts before ON before.receipt_no=l.predecessor_receipt_no
            JOIN contracts after ON after.receipt_no=l.successor_receipt_no
            ORDER BY l.predecessor_receipt_no LIMIT 10
        """).fetchall()]
        latest_samples = []
        for linked_receipt in link_receipts:
            observation = get_latest_observed(connection, linked_receipt)
            contract = get_latest_observed_contract(connection, linked_receipt)
            latest_samples.append({
                "query_receipt_no": linked_receipt,
                "observation": compact(observation),
                "contract": compact(contract),
            })
        correction_receipt = link_receipts[0]
        return {
            "company_period_query": {
                "company": company, "date_from": "2025-01-01", "date_to": "2025-12-31",
                "results": [compact(row) for row in period_rows],
            },
            "receipt_query": compact(get_contract(connection, receipt_no)),
            "largest_contract": compact(get_largest_contract(connection, company)),
            "correction_history": get_correction_history(connection, correction_receipt),
            "latest_observed_samples": latest_samples,
        }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    result = run(args.database_url)
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
