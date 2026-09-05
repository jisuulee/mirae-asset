from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path

from contract_core import (
    CORRECTION_CHAINS,
    DATA,
    SCHEMA,
    contract_manifest_rows,
    decode_xml,
    extract_contract_fields,
    open_database,
)


def ingest(db_path: Path) -> dict[str, int]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = open_database(db_path)
    connection.executescript(SCHEMA.read_text(encoding="utf-8"))
    rows = contract_manifest_rows()
    with connection:
        # Deterministic rebuild of this V0 database. It never touches source data.
        connection.execute("DELETE FROM correction_links")
        connection.execute("DELETE FROM contracts")
        connection.execute("DELETE FROM disclosures")
        connection.execute("DELETE FROM companies")
        for row in rows:
            corp_code = row["corp_code"] or None
            connection.execute(
                "INSERT INTO companies(corp_code, company_name) VALUES (?, ?) "
                "ON CONFLICT(corp_code) DO UPDATE SET company_name=excluded.company_name",
                (corp_code, row["company_name"]),
            )
            company_id = connection.execute(
                "SELECT company_id FROM companies WHERE corp_code IS ?", (corp_code,)
            ).fetchone()[0]
            source_path = row["relative_path"]
            raw = (DATA / source_path).read_bytes()
            filing_date = f"{row['disclosure_date'][:4]}-{row['disclosure_date'][4:6]}-{row['disclosure_date'][6:8]}"
            connection.execute(
                "INSERT INTO disclosures(receipt_no, company_id, filing_date, report_name, is_correction, source_path, source_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["accession_no"], company_id, filing_date, row["report_name"],
                    int(row["is_correction"]), source_path, hashlib.sha256(raw).hexdigest(),
                ),
            )
            values = extract_contract_fields(decode_xml(raw), source_path)
            params: list[object] = [row["accession_no"]]
            for field in ("contract_amount", "counterparty", "contract_start_date", "contract_end_date", "sales_ratio"):
                item = values[field]
                params.extend((str(item.normalized) if item.normalized is not None else None, item.raw, item.source))
            connection.execute(
                """INSERT INTO contracts VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                params,
            )

        disclosure_ids = {row[0] for row in connection.execute("SELECT receipt_no FROM disclosures")}
        links = 0
        with CORRECTION_CHAINS.open(encoding="utf-8-sig", newline="") as handle:
            for link in csv.DictReader(handle):
                if link["link_method"] != "explicit_original_filing_date" or link["link_confidence"] != "high":
                    continue
                correction = link["accession_no"]
                for original in filter(None, link["parent_accessions"].split("|")):
                    if original not in disclosure_ids or correction not in disclosure_ids:
                        continue
                    connection.execute(
                        "INSERT OR IGNORE INTO correction_links VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            original, correction, "explicit_original_filing_date", "high",
                            link["explicit_original_date"],
                            str(CORRECTION_CHAINS.relative_to(CORRECTION_CHAINS.parents[2])),
                        ),
                    )
                    links += 1
    stats = {
        "companies": connection.execute("SELECT count(*) FROM companies").fetchone()[0],
        "disclosures": connection.execute("SELECT count(*) FROM disclosures").fetchone()[0],
        "contracts": connection.execute("SELECT count(*) FROM contracts").fetchone()[0],
        "explicit_correction_links": connection.execute("SELECT count(*) FROM correction_links").fetchone()[0],
    }
    connection.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest the exchange contract V0 slice into SQLite")
    parser.add_argument("--db", type=Path, default=Path("artifacts/v0_contracts.sqlite"))
    args = parser.parse_args()
    print(ingest(args.db.resolve()))


if __name__ == "__main__":
    main()
