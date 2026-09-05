"""Apply Retrieval V1 schema and atomically upsert the Role 2 snapshot."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

try:
    from .load_staging import ROOT, build_all, summary
except ImportError:  # Direct script execution: python src/db/load_postgres.py
    from load_staging import ROOT, build_all, summary

SCHEMA = ROOT / "src" / "db" / "schema_v1.sql"


def connect(database_url: str):
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError('Install the driver with: python -m pip install "psycopg[binary]>=3.2,<4"') from exc
    return psycopg.connect(database_url)


def _create_staging(connection: Any) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS staging")
    connection.execute("DROP TABLE IF EXISTS staging.correction_links, staging.contracts, staging.disclosure_files, staging.disclosures, staging.companies")
    connection.execute("""
        CREATE TABLE staging.companies (
            corp_code text PRIMARY KEY, stock_code text, company_name text NOT NULL,
            company_name_normalized text NOT NULL, payload jsonb NOT NULL)
    """)
    connection.execute("""
        CREATE TABLE staging.disclosures (
            receipt_no text PRIMARY KEY, doc_id text UNIQUE NOT NULL, corp_code text NOT NULL,
            receipt_date text NOT NULL, category text NOT NULL, report_name text NOT NULL,
            report_type text, is_correction_name boolean NOT NULL, is_correction_rm boolean NOT NULL,
            metadata jsonb NOT NULL, payload jsonb NOT NULL)
    """)
    connection.execute("""
        CREATE TABLE staging.disclosure_files (
            receipt_no text NOT NULL, relative_path text NOT NULL, file_role text NOT NULL,
            root_tag text, encoding text, byte_size bigint, sha256 text NOT NULL,
            source_order smallint NOT NULL, payload jsonb NOT NULL,
            PRIMARY KEY (receipt_no, relative_path), UNIQUE (receipt_no, source_order))
    """)
    connection.execute("""
        CREATE TABLE staging.contracts (
            receipt_no text PRIMARY KEY, source_relative_path text NOT NULL, payload jsonb NOT NULL)
    """)
    connection.execute("""
        CREATE TABLE staging.correction_links (
            predecessor_receipt_no text NOT NULL, successor_receipt_no text NOT NULL,
            payload jsonb NOT NULL, PRIMARY KEY (predecessor_receipt_no, successor_receipt_no),
            UNIQUE (successor_receipt_no))
    """)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def load(database_url: str) -> dict[str, int]:
    data = build_all()
    with connect(database_url) as connection:
        connection.execute(SCHEMA.read_text(encoding="utf-8"))
        _create_staging(connection)
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO staging.companies VALUES (%s,%s,%s,%s,%s::jsonb)",
                [(r["corp_code"], r["stock_code"], r["company_name"], r["company_name_normalized"], _json(r)) for r in data["companies"]],
            )
            cursor.executemany(
                "INSERT INTO staging.disclosures VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                [(r["receipt_no"], r["doc_id"], r["corp_code"], r["receipt_date"], r["category"], r["report_name"], r["report_type"], r["is_correction_name"], r["is_correction_rm"], _json(r["metadata"]), _json(r)) for r in data["disclosures"]],
            )
            cursor.executemany(
                "INSERT INTO staging.disclosure_files VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                [(r["receipt_no"], r["relative_path"], r["file_role"], r["root_tag"], r["encoding"], r["byte_size"], r["sha256"], r["source_order"], _json(r)) for r in data["disclosure_files"]],
            )
            cursor.executemany(
                "INSERT INTO staging.contracts VALUES (%s,%s,%s::jsonb)",
                [(r["receipt_no"], r["source_relative_path"], _json(r)) for r in data["contracts"]],
            )
            cursor.executemany(
                "INSERT INTO staging.correction_links VALUES (%s,%s,%s::jsonb)",
                [(r["predecessor_receipt_no"], r["successor_receipt_no"], _json(r)) for r in data["correction_links"]],
            )

        connection.execute("""
            INSERT INTO companies(corp_code, stock_code, company_name, company_name_normalized)
            SELECT corp_code, stock_code, company_name, company_name_normalized FROM staging.companies
            ON CONFLICT (corp_code) DO UPDATE SET
              stock_code=excluded.stock_code, company_name=excluded.company_name,
              company_name_normalized=excluded.company_name_normalized
        """)
        connection.execute("""
            INSERT INTO disclosures(receipt_no, company_id, receipt_date, category, report_name,
              report_type, is_correction_name, is_correction_rm, metadata)
            SELECT s.receipt_no, c.company_id, s.receipt_date::date, s.category, s.report_name,
              s.report_type, s.is_correction_name, s.is_correction_rm, s.metadata
            FROM staging.disclosures s JOIN companies c USING (corp_code)
            ON CONFLICT (receipt_no) DO UPDATE SET
              company_id=excluded.company_id, receipt_date=excluded.receipt_date,
              category=excluded.category, report_name=excluded.report_name,
              report_type=excluded.report_type, is_correction_name=excluded.is_correction_name,
              is_correction_rm=excluded.is_correction_rm, metadata=excluded.metadata
        """)
        connection.execute("""
            INSERT INTO disclosure_files(receipt_no, relative_path, file_role, root_tag, encoding,
              byte_size, sha256, source_order)
            SELECT receipt_no, relative_path, file_role, root_tag, encoding, byte_size,
              sha256::char(64), source_order FROM staging.disclosure_files
            ON CONFLICT (receipt_no, relative_path) DO UPDATE SET
              file_role=excluded.file_role, root_tag=excluded.root_tag, encoding=excluded.encoding,
              byte_size=excluded.byte_size, sha256=excluded.sha256, source_order=excluded.source_order
        """)
        connection.execute("""
            INSERT INTO contracts(receipt_no, contract_name_raw, contract_name,
              contract_amount_raw, contract_amount, contract_currency, counterparty_raw,
              counterparty, contract_start_date_raw, contract_start_date,
              contract_end_date_raw, contract_end_date, sales_ratio_raw, sales_ratio,
              source_file_id, field_provenance, parser_version)
            SELECT s.receipt_no, s.payload->>'contract_name_raw', s.payload->>'contract_name',
              s.payload->>'contract_amount_raw', (s.payload->>'contract_amount')::numeric,
              s.payload->>'contract_currency', s.payload->>'counterparty_raw',
              s.payload->>'counterparty', s.payload->>'contract_start_date_raw',
              (s.payload->>'contract_start_date')::date, s.payload->>'contract_end_date_raw',
              (s.payload->>'contract_end_date')::date, s.payload->>'sales_ratio_raw',
              (s.payload->>'sales_ratio')::numeric, f.file_id,
              s.payload->'field_provenance', s.payload->>'parser_version'
            FROM staging.contracts s JOIN disclosure_files f
              ON f.receipt_no=s.receipt_no AND f.relative_path=s.source_relative_path
            ON CONFLICT (receipt_no) DO UPDATE SET
              contract_name_raw=excluded.contract_name_raw, contract_name=excluded.contract_name,
              contract_amount_raw=excluded.contract_amount_raw, contract_amount=excluded.contract_amount,
              contract_currency=excluded.contract_currency, counterparty_raw=excluded.counterparty_raw,
              counterparty=excluded.counterparty, contract_start_date_raw=excluded.contract_start_date_raw,
              contract_start_date=excluded.contract_start_date, contract_end_date_raw=excluded.contract_end_date_raw,
              contract_end_date=excluded.contract_end_date, sales_ratio_raw=excluded.sales_ratio_raw,
              sales_ratio=excluded.sales_ratio, source_file_id=excluded.source_file_id,
              field_provenance=excluded.field_provenance, parser_version=excluded.parser_version,
              parsed_at=now()
        """)
        connection.execute("DELETE FROM correction_links")
        connection.execute("""
            INSERT INTO correction_links(predecessor_receipt_no, successor_receipt_no, link_method,
              confidence, explicit_original_date, evidence, source_analysis_path)
            SELECT payload->>'predecessor_receipt_no', payload->>'successor_receipt_no',
              payload->>'link_method', payload->>'confidence',
              (payload->>'explicit_original_date')::date, payload->'evidence',
              payload->>'source_analysis_path'
            FROM staging.correction_links
        """)
        # Snapshot-owned contract rows are exact; remove legacy/out-of-scope rows only.
        connection.execute("DELETE FROM contracts WHERE receipt_no NOT IN (SELECT receipt_no FROM staging.contracts)")
        counts = {}
        for table in ("companies", "disclosures", "disclosure_files", "contracts", "correction_links"):
            counts[table] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        expected = summary(data)
        if counts != expected:
            raise RuntimeError(f"loaded counts differ: expected={expected}, actual={counts}")
        return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    print(json.dumps(load(args.database_url), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
