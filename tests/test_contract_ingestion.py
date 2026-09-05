from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from contract_core import CORRECTION_CHAINS, DATA, contract_manifest_rows, latest_effective_contract, open_database
from ingest_contracts import ingest
from query_contracts import latest_contract, list_contracts


class ContractIngestionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.temp.name) / "contracts.sqlite"
        cls.stats = ingest(cls.db)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_ingests_exact_contract_scope(self) -> None:
        expected = len(contract_manifest_rows())
        self.assertEqual(expected, self.stats["disclosures"])
        self.assertEqual(expected, self.stats["contracts"])
        self.assertGreater(expected, 1_000)

    def test_raw_and_source_are_preserved(self) -> None:
        connection = open_database(self.db)
        row = connection.execute(
            "SELECT d.source_path, c.contract_amount_raw, c.contract_amount_source "
            "FROM disclosures d JOIN contracts c USING(receipt_no) "
            "WHERE c.contract_amount_raw IS NOT NULL LIMIT 1"
        ).fetchone()
        connection.close()
        self.assertIsNotNone(row)
        self.assertTrue((DATA / row["source_path"]).exists())
        self.assertIn("#table-row=", row["contract_amount_source"])

    def test_only_explicit_high_links_are_loaded(self) -> None:
        allowed = set()
        with CORRECTION_CHAINS.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row["link_method"] == "explicit_original_filing_date" and row["link_confidence"] == "high":
                    for parent in filter(None, row["parent_accessions"].split("|")):
                        allowed.add((parent, row["accession_no"]))
        connection = open_database(self.db)
        loaded = {tuple(row) for row in connection.execute(
            "SELECT original_receipt_no, correction_receipt_no FROM correction_links"
        ).fetchall()}
        connection.close()
        self.assertTrue(loaded)
        self.assertTrue(loaded.issubset(allowed))

    def test_latest_uses_explicit_chain(self) -> None:
        connection = open_database(self.db)
        edge = connection.execute(
            "SELECT original_receipt_no, correction_receipt_no FROM correction_links LIMIT 1"
        ).fetchone()
        result = latest_effective_contract(connection, edge[0])
        connection.close()
        self.assertIn(edge[0], result["chain"])
        self.assertIn(edge[1], result["chain"])
        self.assertEqual(result["receipt_no"], result["chain"][-1])

    def test_query_result_has_required_fields(self) -> None:
        connection = open_database(self.db)
        row = connection.execute(
            "SELECT co.company_name, d.receipt_no FROM disclosures d "
            "JOIN companies co ON co.company_id=d.company_id ORDER BY d.receipt_no LIMIT 1"
        ).fetchone()
        connection.close()
        result = latest_contract(self.db, row[0], row[1])
        self.assertEqual(
            {"company", "contract_amount", "counterparty", "filing_date", "receipt_no", "source"},
            set(result),
        )
        self.assertTrue(list_contracts(self.db, row[0]))


if __name__ == "__main__":
    unittest.main()
