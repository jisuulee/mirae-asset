from __future__ import annotations

import unittest

from disclosure_preprocess.final_index import (
    build_chain_membership,
    build_document_index_row,
    count_provenance,
    lineage_projection,
    quality_projection,
)


class FinalIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.complete_chain = {
            "chain_id": "chain_complete",
            "root_rcept_no": "20250101000001",
            "latest_rcept_no": "20250103000003",
            "is_complete": True,
            "members": [
                {"sequence": 0, "doc_id": "root", "rcept_no": "20250101000001", "is_correction": False},
                {"sequence": 1, "doc_id": "middle", "rcept_no": "20250102000002", "is_correction": True},
                {"sequence": 2, "doc_id": "latest", "rcept_no": "20250103000003", "is_correction": True},
            ],
        }

    def test_complete_chain_flags_cover_root_intermediate_and_latest(self) -> None:
        membership = build_chain_membership([self.complete_chain])
        self.assertEqual(membership["root"]["is_latest_in_complete_chain"], False)
        self.assertEqual(membership["root"]["is_superseded_in_complete_chain"], True)
        self.assertEqual(membership["middle"]["is_latest_in_complete_chain"], False)
        self.assertEqual(membership["middle"]["is_superseded_in_complete_chain"], True)
        self.assertEqual(membership["latest"]["is_latest_in_complete_chain"], True)
        self.assertEqual(membership["latest"]["is_superseded_in_complete_chain"], False)

    def test_partial_ambiguous_and_unresolved_never_get_boolean_flags(self) -> None:
        partial = {
            "chain_id": "chain_partial",
            "root_rcept_no": None,
            "latest_rcept_no": "20250102000002",
            "is_complete": False,
            "members": [
                {"sequence": None, "doc_id": "partial", "rcept_no": "20250102000002", "is_correction": True}
            ],
        }
        membership = build_chain_membership([partial])["partial"]
        for status in ("linked_exact", "ambiguous", "unresolved"):
            record = {
                "resolution": {"status": status, "confidence": 1.0},
                "lineage": {"chain_id": "chain_partial"},
            }
            projected = lineage_projection({"is_correction": True}, record, membership)
            self.assertIsNone(projected["is_latest_in_complete_chain"])
            self.assertIsNone(projected["is_superseded_in_complete_chain"])

    def test_quality_flags_are_deterministic(self) -> None:
        lineage = {
            "is_correction": True,
            "status": "ambiguous",
            "is_complete_chain": None,
        }
        result = quality_projection(
            parse_status="partial",
            metadata_status="valid_with_warning",
            lineage=lineage,
        )
        self.assertTrue(result["requires_parse_caution"])
        self.assertTrue(result["requires_metadata_caution"])
        self.assertTrue(result["requires_lineage_guard"])
        self.assertFalse(result["safe_for_automatic_lineage_dedup"])

    def test_index_preserves_leading_zero_identifiers(self) -> None:
        metadata_row = {
            "doc_id": "root",
            "rcept_no": "20250101000001",
            "metadata": {
                "doc_id": "root",
                "rcept_no": "20250101000001",
                "corp_code": "00126380",
                "stock_code": "005930",
                "is_correction": False,
            },
            "metadata_validation": {"status": "valid"},
        }
        parsed = {
            "doc_id": "root",
            "rcept_no": "20250101000001",
            "parse_status": "success",
            "source_files": [{"sha256": "a" * 64}],
            "main_document": {
                "content": {
                    "title": "title",
                    "body_text": "body",
                    "sections": [],
                    "tables": [],
                }
            },
            "attachments": [],
            "warnings": [],
        }
        membership = build_chain_membership([self.complete_chain])["root"]
        row = build_document_index_row(metadata_row, parsed, None, membership)
        self.assertEqual(row["metadata"]["corp_code"], "00126380")
        self.assertEqual(row["metadata"]["stock_code"], "005930")
        self.assertEqual(row["rcept_no"], "20250101000001")

    def test_duplicate_chain_membership_is_rejected(self) -> None:
        duplicate = dict(self.complete_chain)
        duplicate["chain_id"] = "other"
        with self.assertRaisesRegex(ValueError, "multiple chains"):
            build_chain_membership([self.complete_chain, duplicate])

    def test_elided_alternative_content_is_not_false_missing_provenance(self) -> None:
        provenance = {"source_file": "raw/main.xml", "file_role": "main"}
        record = {
            "main_document": {"content": {"provenance": provenance, "sections": [], "block_sequence": [], "tables": []}},
            "attachments": [],
            "alternative_representations": [
                {"content_included": False, "source_file": {"sha256": "a" * 64}}
            ],
        }
        counts, missing = count_provenance(record)
        self.assertEqual(counts, {"document": 1})
        self.assertEqual(missing, {})


if __name__ == "__main__":
    unittest.main()
