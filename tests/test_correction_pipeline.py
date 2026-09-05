from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from disclosure_preprocess.correction_pipeline import resolve_correction_corpus


def _line(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _metadata(doc_id: str, receipt: str, date: str, correction: bool) -> dict[str, object]:
    return {
        "schema_version": "3.0.0",
        "doc_id": doc_id,
        "rcept_no": receipt,
        "metadata": {
            "doc_id": doc_id,
            "rcept_no": receipt,
            "corp_code": "00126380",
            "corp_name": "삼성전자",
            "listed_name": "삼성전자",
            "stock_code": "005930",
            "doc_group": "periodic",
            "doc_subtype": "annual",
            "report_nm": "[기재정정]사업보고서 (2024.12)" if correction else "사업보고서 (2024.12)",
            "rcept_dt": date.replace("-", ""),
            "rcept_date": date,
            "is_correction": correction,
        },
    }


def _parsed(metadata: dict[str, object]) -> dict[str, object]:
    correction = bool(metadata["metadata"]["is_correction"])  # type: ignore[index]
    blocks = []
    if correction:
        blocks = [
            {"text": "1. 정정대상 공시서류 : 사업보고서", "provenance": {"dom_path": "/P[1]"}},
            {"text": "2. 정정대상 공시서류의 최초제출일 : 2025년 03월 18일", "provenance": {"dom_path": "/P[2]"}},
            {"text": "3. 정정사항", "provenance": {"dom_path": "/P[3]"}},
        ]
    return {
        "schema_version": "2.0.0",
        "doc_id": metadata["doc_id"],
        "rcept_no": metadata["rcept_no"],
        "main_document": {
            "source_file": {"relative_path": f"raw/{metadata['doc_id']}.xml", "role": "main"},
            "content": {"tables": [], "block_sequence": blocks},
        },
        "attachments": [],
        "parse_status": "success",
        "warnings": [],
    }


class CorrectionPipelineTests(unittest.TestCase):
    def test_stream_join_sidecars_and_input_immutability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata_path = root / "metadata.jsonl"
            parsed_path = root / "parsed.jsonl"
            original = _metadata("periodic_root", "20250318000001", "2025-03-18", False)
            correction = _metadata("periodic_corr", "20250319000002", "2025-03-19", True)
            metadata_bytes = _line(original) + _line(correction)
            parsed_bytes = _line(_parsed(original)) + _line(_parsed(correction))
            metadata_path.write_bytes(metadata_bytes)
            parsed_path.write_bytes(parsed_bytes)

            summary = resolve_correction_corpus(
                metadata_path=metadata_path,
                parsed_path=parsed_path,
                output_dir=root / "output",
                report_dir=root / "reports",
                expected_document_count=2,
                expected_correction_count=1,
                progress_every=0,
            )

            self.assertEqual(summary["resolution"]["status_counts"], {"linked_exact": 1})
            self.assertEqual(summary["resolution"]["chain_count"], 1)
            self.assertTrue(all(summary["quality_gates"].values()))
            self.assertEqual(metadata_path.read_bytes(), metadata_bytes)
            self.assertEqual(parsed_path.read_bytes(), parsed_bytes)
            lineage = json.loads((root / "output" / "correction_lineage.jsonl").read_bytes())
            self.assertEqual(lineage["lineage"]["root_rcept_no"], "20250318000001")
            self.assertEqual(lineage["correction_evidence"]["target_submission_date"], "2025-03-18")
            self.assertTrue((root / "reports" / "correction_unresolved.jsonl").is_file())

    def test_missing_api_key_fails_before_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata_path = root / "metadata.jsonl"
            parsed_path = root / "parsed.jsonl"
            metadata_path.write_bytes(b"")
            parsed_path.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "OPEN_DART_API_KEY"):
                resolve_correction_corpus(
                    metadata_path=metadata_path,
                    parsed_path=parsed_path,
                    output_dir=root / "output",
                    report_dir=root / "reports",
                    expected_document_count=0,
                    expected_correction_count=0,
                    enable_opendart=True,
                )


if __name__ == "__main__":
    unittest.main()
