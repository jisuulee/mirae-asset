from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from contract_core import latest_effective_contract, open_database
from query_contracts import display_row


def validate(db_path: Path, sample_size: int = 10) -> dict:
    connection = open_database(db_path)
    total = connection.execute("SELECT count(*) FROM contracts").fetchone()[0]
    fields = ("contract_amount", "counterparty", "contract_start_date", "contract_end_date", "sales_ratio")
    missing = {}
    for field in fields:
        normalized = connection.execute(f"SELECT count(*) FROM contracts WHERE {field} IS NULL").fetchone()[0]
        raw = connection.execute(f"SELECT count(*) FROM contracts WHERE {field}_raw IS NULL").fetchone()[0]
        missing[field] = {
            "normalized_missing_count": normalized,
            "normalized_missing_rate": round(normalized / total, 6) if total else 0,
            "raw_missing_count": raw,
            "raw_missing_rate": round(raw / total, 6) if total else 0,
        }
    link_count = connection.execute("SELECT count(*) FROM correction_links").fetchone()[0]
    roots = connection.execute(
        """
        SELECT DISTINCT original_receipt_no FROM correction_links
        WHERE original_receipt_no NOT IN (SELECT correction_receipt_no FROM correction_links)
        ORDER BY original_receipt_no LIMIT ?
        """,
        (sample_size,),
    ).fetchall()
    samples = []
    for root in roots:
        effective = latest_effective_contract(connection, root[0])
        if effective:
            item = display_row(effective)
            item["query_receipt_no"] = root[0]
            item["explicit_chain"] = effective["chain"]
            samples.append(item)
    connection.close()
    return {
        "total_loaded_contracts": total,
        "field_missing_rates": missing,
        "explicit_correction_link_count": link_count,
        "latest_value_samples": samples,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Validate the V0 exchange contract ingestion")
    parser.add_argument("--db", type=Path, default=Path("artifacts/v0_contracts.sqlite"))
    parser.add_argument("--sample-size", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(validate(args.db.resolve(), args.sample_size), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
