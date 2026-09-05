from __future__ import annotations

import os
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "src"))

from evaluate_retrieval import evaluate, read_jsonl


class RetrievalBenchmarkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gold = read_jsonl(ROOT / "eval" / "retrieval_questions.jsonl")

    def test_balanced_42_question_contract(self):
        self.assertEqual(42, len(self.gold))
        self.assertEqual(42, len({row["question_id"] for row in self.gold}))
        self.assertEqual(7, len(Counter(row["query_type"] for row in self.gold)))
        self.assertTrue(all(count == 6 for count in Counter(row["query_type"] for row in self.gold).values()))
        for row in self.gold:
            self.assertTrue(row["question"])
            self.assertTrue(row["expected_receipt_nos"])
            self.assertTrue(row["validation_source"])

    def test_saved_warm_predictions_score(self):
        predictions = read_jsonl(ROOT / "reports" / "retrieval_predictions_warm.jsonl")
        result = evaluate(self.gold, predictions)
        self.assertEqual(1.0, result["top_1_accuracy"])
        self.assertEqual(1.0, result["recall_at_5"])
        self.assertFalse(result["failures"])


@unittest.skipUnless(os.getenv("DATABASE_URL"), "DATABASE_URL is required for DB gold validation")
class RetrievalBenchmarkDatabaseTest(unittest.TestCase):
    def test_every_gold_receipt_exists(self):
        from db.load_postgres import connect
        gold = read_jsonl(ROOT / "eval" / "retrieval_questions.jsonl")
        expected = {receipt for row in gold for receipt in row["expected_receipt_nos"]}
        with connect(os.environ["DATABASE_URL"]) as connection:
            actual = {row[0] for row in connection.execute(
                "SELECT receipt_no FROM disclosures WHERE receipt_no = ANY(%s)", (list(expected),)
            ).fetchall()}
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
