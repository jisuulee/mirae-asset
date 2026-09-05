from __future__ import annotations

import csv
import json
import tempfile
import unicodedata
import unittest
import warnings
import zipfile
from pathlib import Path
from typing import Any

from disclosure_preprocess.inventory import analyze_corpus, extract_document_name


def _manifest_record() -> dict[str, Any]:
    return {
        "doc_id": "exchange_20250101800001",
        "corp_code": "00123456",
        "corp_name": "테스트회사",
        "listed_name": "테스트",
        "stock_code": "001230",
        "industry": "fixture",
        "sector": "fixture",
        "doc_group": "exchange",
        "doc_subtype": "contract",
        "report_nm": "단일판매ㆍ공급계약체결",
        "is_correction": False,
        "rcept_no": "20250101800001",
        "rcept_dt": "20250101",
        "flr_nm": "테스트회사",
        "base_year": None,
        "base_month": None,
        "file_path": "raw/exchange/테스트회사/20250101800001",
        "file_format": "xml",
        "n_files": 1,
    }


def _fixture_members() -> dict[str, bytes]:
    company_nfd = unicodedata.normalize("NFD", "테스트회사")
    manifest = (json.dumps(_manifest_record(), ensure_ascii=False) + "\n").encode()
    html_bytes = (
        '<html><head><meta charset="euc-kr"></head><body>한글</body></html>'.encode()
    )
    return {
        "manifest.jsonl": manifest,
        f"raw/exchange/{company_nfd}/20250101800001/20250101800001.xml": html_bytes,
        f"raw/exchange/{company_nfd}/list_I.json": b'{"list": []}',
    }


class InventoryIntegrationTests(unittest.TestCase):
    def test_document_name_survives_truncated_utf8_prefix(self) -> None:
        complete = (
            "<DOCUMENT><DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>".encode("utf-8")
        )
        truncated_next_character = "한".encode("utf-8")[:1]
        self.assertEqual(
            extract_document_name(complete + truncated_next_character, None),
            "사업보고서",
        )

    def _assert_summary(self, summary: dict[str, Any], report_dir: Path) -> None:
        self.assertEqual(summary["manifest"]["document_count"], 1)
        self.assertEqual(summary["manifest"]["company_document_counts"], {"테스트회사": 1})
        self.assertEqual(summary["physical_files"]["raw_file_count"], 2)
        self.assertEqual(summary["physical_files"]["manifest_linked_raw_file_count"], 1)
        self.assertEqual(summary["physical_files"]["manifest_unlinked_raw_file_count"], 1)
        self.assertEqual(summary["physical_files"]["detected_format_counts"], {"html": 1, "json": 1})
        self.assertEqual(summary["physical_files"]["xml_extension_html_count"], 1)
        self.assertEqual(summary["join_quality"]["manifest_file_count_mismatch_count"], 0)
        self.assertEqual(summary["encoding"]["declaration_mismatch_count"], 1)
        self.assertEqual(summary["encoding"]["no_charset_declaration_count"], 1)
        for filename in (
            "inventory_summary.json",
            "file_inventory.csv",
            "inventory_report.md",
        ):
            self.assertTrue((report_dir / filename).is_file())

        with (report_dir / "file_inventory.csv").open(
            encoding="utf-8-sig", newline=""
        ) as csv_file:
            rows = list(csv.DictReader(csv_file))
        payload = next(row for row in rows if row["manifest_doc_id"])
        self.assertEqual(payload["rcept_no"], "20250101800001")
        self.assertIn("테스트회사", payload["relative_path"])

    def test_directory_source_joins_nfd_path_to_nfc_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "corpus"
            report_dir = Path(directory) / "reports"
            for relative_path, content in _fixture_members().items():
                target = root / Path(*relative_path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

            summary = analyze_corpus(root, report_dir)
            self.assertEqual(summary["source"]["kind"], "directory")
            self._assert_summary(summary, report_dir)

    def test_zip_source_matches_directory_semantics_without_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "fixture.zip"
            report_dir = Path(directory) / "reports"
            root_nfd = unicodedata.normalize("NFD", "공시") + "/corpus/"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for relative_path, content in _fixture_members().items():
                    archive.writestr(root_nfd + relative_path, content)

            summary = analyze_corpus(archive_path, report_dir)
            self.assertEqual(summary["source"]["kind"], "zip")
            self._assert_summary(summary, report_dir)

    def test_zip_source_accepts_backslash_member_separators(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "backslash-fixture.zip"
            report_dir = Path(directory) / "reports"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for relative_path, content in _fixture_members().items():
                    member_name = ("wrapper/corpus/" + relative_path).replace(
                        "/", "\\"
                    )
                    archive.writestr(member_name, content)

            summary = analyze_corpus(archive_path, report_dir)

            self.assertEqual(summary["physical_files"]["raw_file_count"], 2)
            self.assertEqual(
                summary["physical_files"]["manifest_linked_raw_file_count"], 1
            )

    def test_missing_manifest_marks_linkage_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "corpus"
            payload = root / "raw" / "exchange" / "회사" / "20250101800001.xml"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"<html><body>notice</body></html>")
            report_dir = Path(directory) / "reports"

            summary = analyze_corpus(root, report_dir, encoding_mode="none")

            self.assertFalse(summary["manifest"]["available"])
            self.assertIsNone(
                summary["physical_files"]["manifest_unlinked_raw_file_count"]
            )
            self.assertIsNone(
                summary["join_quality"]["manifest_file_count_mismatch_count"]
            )
            with (report_dir / "file_inventory.csv").open(
                encoding="utf-8-sig", newline=""
            ) as csv_file:
                row = next(csv.DictReader(csv_file))
            self.assertEqual(row["file_scope"], "manifest_link_unavailable")
            report = (report_dir / "inventory_report.md").read_text(encoding="utf-8")
            self.assertIn("측정 불가", report)

    def test_output_artifact_cannot_replace_zip_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            collision_path = Path(directory) / "inventory_summary.json"
            with zipfile.ZipFile(collision_path, "w") as archive:
                for relative_path, content in _fixture_members().items():
                    archive.writestr("corpus/" + relative_path, content)

            with self.assertRaises(ValueError):
                analyze_corpus(collision_path, Path(directory))

            self.assertTrue(zipfile.is_zipfile(collision_path))

    def test_duplicate_zip_member_is_reported_as_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "duplicate.zip"
            report_dir = Path(directory) / "reports"
            members = _fixture_members()
            payload_path = next(path for path in members if path.endswith(".xml"))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive_path, "w") as archive:
                    for relative_path, content in members.items():
                        archive.writestr("corpus/" + relative_path, content)
                    archive.writestr("corpus/" + payload_path, members[payload_path])

            summary = analyze_corpus(archive_path, report_dir)

            self.assertEqual(
                summary["join_quality"]["canonical_path_collision_count"], 1
            )
            self.assertEqual(summary["issues"]["counts_by_stage"], {"canonical_path_collision": 1})


if __name__ == "__main__":
    unittest.main()
