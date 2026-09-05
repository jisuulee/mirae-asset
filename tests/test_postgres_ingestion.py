from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "db"))

from db.validate_postgres import validate
from retrieval.contracts import ContractSearch, get_contract, get_largest_contract, get_latest_observed_contract, search_contracts
from retrieval.corrections import get_correction_history


@unittest.skipUnless(os.getenv("DATABASE_URL"), "DATABASE_URL is required for PostgreSQL integration tests")
class PostgresIngestionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from db.load_postgres import connect
        cls.connection = connect(os.environ["DATABASE_URL"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_validation_passes(self) -> None:
        result = validate(os.environ["DATABASE_URL"])
        self.assertTrue(result["passed"], result)
        self.assertEqual(1106, result["counts"]["contracts"])

    def test_contract_queries(self) -> None:
        sample = self.connection.execute(
            "SELECT co.company_name, c.receipt_no FROM contracts c JOIN disclosures d USING(receipt_no) JOIN companies co USING(company_id) ORDER BY c.receipt_no LIMIT 1"
        ).fetchone()
        self.assertTrue(search_contracts(self.connection, ContractSearch(company=sample[0], limit=5)))
        self.assertIsNotNone(get_contract(self.connection, sample[1]))
        self.assertIsNotNone(get_largest_contract(self.connection, sample[0]))

    def test_correction_queries(self) -> None:
        receipt = self.connection.execute(
            "SELECT l.predecessor_receipt_no FROM correction_links l "
            "JOIN contracts b ON b.receipt_no=l.predecessor_receipt_no "
            "JOIN contracts a ON a.receipt_no=l.successor_receipt_no "
            "ORDER BY l.predecessor_receipt_no LIMIT 1"
        ).fetchone()[0]
        history = get_correction_history(self.connection, receipt)
        self.assertGreaterEqual(len(history), 2)
        latest = get_latest_observed_contract(self.connection, receipt)
        self.assertIsNotNone(latest)
        self.assertEqual("latest_observed_in_current_dataset", latest["observation_status"])


if __name__ == "__main__":
    unittest.main()
