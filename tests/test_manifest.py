from __future__ import annotations

import io
import json
import unicodedata
import unittest
from contextlib import AbstractContextManager
from typing import BinaryIO

from disclosure_preprocess.manifest import load_manifest, preserve_identifier_strings


class MemoryManifest:
    relative_path = "manifest.jsonl"

    def __init__(self, records: list[dict[str, object]]) -> None:
        self._content = "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        ).encode()

    def open(self) -> AbstractContextManager[BinaryIO]:
        return io.BytesIO(self._content)


class ManifestIdentifierTests(unittest.TestCase):
    def test_identifier_strings_preserve_leading_zeroes(self) -> None:
        record = {
            "doc_id": "periodic_20250101000001",
            "corp_code": "00126380",
            "stock_code": "005930",
            "rcept_no": "20250101000001",
        }
        preserved = preserve_identifier_strings(record)
        self.assertEqual(preserved["corp_code"], "00126380")
        self.assertEqual(preserved["stock_code"], "005930")
        self.assertEqual(preserved["rcept_no"], "20250101000001")

    def test_company_aggregation_normalizes_nfc(self) -> None:
        company = "미래에셋증권"
        records = [
            {
                "doc_id": f"doc_{index}",
                "corp_name": name,
                "doc_group": "periodic",
                "doc_subtype": "annual",
                "is_correction": False,
                "file_format": "xml",
                "n_files": 1,
                "rcept_no": f"2025010100000{index}",
                "rcept_dt": "20250101",
                "file_path": f"raw/periodic/{name}/doc_{index}",
            }
            for index, name in enumerate(
                (company, unicodedata.normalize("NFD", company)), start=1
            )
        ]

        inventory = load_manifest(MemoryManifest(records))  # type: ignore[arg-type]

        self.assertEqual(inventory.summary["company_count"], 1)
        self.assertEqual(inventory.summary["company_document_counts"], {company: 2})

    def test_unsafe_manifest_path_is_counted_and_not_indexed(self) -> None:
        record = {
            "doc_id": "doc_unsafe",
            "corp_name": "회사",
            "doc_group": "periodic",
            "doc_subtype": "annual",
            "is_correction": False,
            "file_format": "xml",
            "n_files": 1,
            "rcept_no": "20250101000001",
            "rcept_dt": "20250101",
            "file_path": "../outside",
        }

        inventory = load_manifest(MemoryManifest([record]))  # type: ignore[arg-type]

        self.assertEqual(inventory.summary["invalid_file_path_count"], 1)
        self.assertEqual(inventory.by_file_path, {})

    def test_numeric_identifiers_are_coerced_without_touching_strings(self) -> None:
        record = {
            "doc_id": 123,
            "corp_code": "00000001",
            "stock_code": None,
            "rcept_no": 20250101000001,
        }
        preserved = preserve_identifier_strings(record)
        self.assertEqual(preserved["doc_id"], "123")
        self.assertEqual(preserved["corp_code"], "00000001")
        self.assertIsNone(preserved["stock_code"])
        self.assertEqual(preserved["rcept_no"], "20250101000001")


if __name__ == "__main__":
    unittest.main()
