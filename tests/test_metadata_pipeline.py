from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from disclosure_preprocess.metadata_pipeline import (
    _select_sample,
    normalize_metadata_corpus,
)


def _json_line(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _manifest_record() -> dict[str, object]:
    return {
        "doc_id": "periodic_20250101000001",
        "corp_code": "00123456",
        "corp_name": "테스트회사",
        "listed_name": "테스트회사",
        "stock_code": "001234",
        "industry": "테스트산업",
        "sector": "테스트섹터",
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "report_nm": "사업보고서 (2024.12)",
        "is_correction": False,
        "rcept_no": "20250101000001",
        "rcept_dt": "20250101",
        "flr_nm": "테스트회사",
        "base_year": 2024,
        "base_month": 12,
        "file_path": "raw/periodic/테스트회사/20250101000001",
        "file_format": "xml",
        "n_files": 1,
    }


def _parsed_record(manifest: dict[str, object]) -> dict[str, object]:
    source_path = "raw/periodic/테스트회사/20250101000001/main.xml"
    canonical_fields = (
        "doc_id",
        "corp_code",
        "corp_name",
        "listed_name",
        "stock_code",
        "industry",
        "sector",
        "doc_group",
        "doc_subtype",
        "report_nm",
        "is_correction",
        "rcept_no",
        "rcept_dt",
        "flr_nm",
        "base_year",
        "base_month",
    )
    return {
        "schema_version": "2.0.0",
        "doc_id": manifest["doc_id"],
        "rcept_no": manifest["rcept_no"],
        **{field: manifest[field] for field in canonical_fields},
        "metadata": {"authority": "manifest", "manifest": manifest},
        "main_document": {
            "source_file": {
                "relative_path": source_path,
                "role": "main",
                "detected_format": "xml",
                "sha256": "a" * 64,
            },
            "parse_status": "success",
            "content": {
                "title": "사업보고서",
                "body_text": "검증 가능한 본문",
                "sections": [{"title": "개요"}],
                "tables": [],
                "block_sequence": [],
            },
            "warnings": [],
            "source_metadata": {
                "rcept_no": manifest["rcept_no"],
                "rcept_no_raw": manifest["rcept_no"],
                "company_name": "테스트컴퍼니",
                "company_name_raw": "테스트컴퍼니",
                "document_name": "사업보고서 (2024.12)",
                "document_name_raw": "사업보고서 (2024.12)",
            },
        },
        "attachments": [],
        "alternative_representations": [],
        "source_files": [
            {
                "relative_path": source_path,
                "role": "main",
                "detected_format": "xml",
                "sha256": "a" * 64,
            }
        ],
        "parse_status": "success",
        "warnings": [
            {
                key: value
                for key, value in _mismatch_warning(manifest).items()
                if key not in {"doc_id", "rcept_no", "scope"}
            }
        ],
    }


def _mismatch_warning(manifest: dict[str, object]) -> dict[str, object]:
    return {
        "doc_id": manifest["doc_id"],
        "rcept_no": manifest["rcept_no"],
        "code": "SOURCE_METADATA_MISMATCH",
        "scope": "logical_document",
        "message": "fixture mismatch",
        "severity": "warning",
        "provenance": {
            "source_file": "raw/periodic/테스트회사/20250101000001/main.xml",
            "file_role": "main",
        },
        "details": {
            "manifest_field": "corp_name",
            "source_field": "company_name",
            "manifest_value": "테스트회사",
            "source_value": "테스트컴퍼니",
        },
    }


class MetadataPipelineIntegrationTests(unittest.TestCase):
    def _fixture(self, root: Path) -> dict[str, Path | bytes]:
        corpus = root / "corpus"
        corpus.mkdir()
        manifest = _manifest_record()
        (corpus / "manifest.jsonl").write_bytes(_json_line(manifest))

        parsed_record = _parsed_record(manifest)
        parsed_bytes = _json_line(parsed_record)
        parsed = root / "phase2" / "parsed_disclosures.jsonl"
        parsed.parent.mkdir()
        parsed.write_bytes(parsed_bytes)
        warnings = root / "phase2" / "parse_warnings.jsonl"
        warnings.write_bytes(_json_line(_mismatch_warning(manifest)))
        contract = root / "data_contract.md"
        contract.write_text("# Fixture contract\n", encoding="utf-8", newline="\n")
        return {
            "corpus": corpus,
            "parsed": parsed,
            "warnings": warnings,
            "contract": contract,
            "parsed_bytes": parsed_bytes,
        }

    def test_one_to_one_join_sidecar_and_verbatim_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            output = root / "output"
            reports = root / "reports"
            handoff = root / "handoff"

            summary = normalize_metadata_corpus(
                fixture["corpus"],
                parsed_path=fixture["parsed"],
                parse_warnings_path=fixture["warnings"],
                output_dir=output,
                report_dir=reports,
                handoff_dir=handoff,
                data_contract_path=fixture["contract"],
                expected_document_count=1,
                expected_mismatch_count=1,
                sample_count=1,
                progress_every=0,
            )

            self.assertTrue(summary["ready_for_downstream"])
            self.assertEqual(summary["joins"]["matched_count"], 1)
            self.assertEqual(summary["corpus"]["metadata_record_count"], 1)
            self.assertEqual(summary["mismatches"]["phase2_occurrence_count"], 1)
            self.assertEqual(summary["mismatches"]["document_count"], 1)

            metadata_bytes = (output / "metadata_normalized.jsonl").read_bytes()
            self.assertEqual(
                metadata_bytes, (handoff / "metadata_normalized.jsonl").read_bytes()
            )
            sidecar = json.loads(metadata_bytes)
            self.assertEqual(sidecar["schema_version"], "3.0.0")
            self.assertEqual(sidecar["metadata"]["authority"], "manifest")
            self.assertEqual(sidecar["metadata"]["corp_code"], "00123456")
            self.assertEqual(sidecar["metadata"]["stock_code"], "001234")
            self.assertEqual(sidecar["metadata"]["rcept_date"], "2025-01-01")
            self.assertEqual(
                sidecar["source_metadata"]["observations"][0]["values"]
                ["company_name"]["raw"],
                "테스트컴퍼니",
            )
            self.assertEqual(
                sidecar["metadata_validation"]["status"], "valid_with_warning"
            )
            self.assertNotIn("correction_chain", metadata_bytes.decode("utf-8"))

            self.assertEqual(fixture["parsed"].read_bytes(), fixture["parsed_bytes"])
            self.assertEqual(
                (handoff / "sample_disclosures.jsonl").read_bytes(),
                fixture["parsed_bytes"],
            )
            self.assertEqual(
                (handoff / "data_contract.md").read_bytes(),
                fixture["contract"].read_bytes(),
            )

            mismatch = json.loads(
                (reports / "metadata_mismatches.jsonl").read_bytes()
            )
            self.assertEqual(mismatch["canonical_field"], "corp_name")
            self.assertEqual(
                mismatch["reason_code"], "SOURCE_VALUE_DIFFERENT"
            )
            self.assertEqual(mismatch["severity"], "warning")
            self.assertTrue(mismatch["evidence_verified"])

            selection = json.loads(
                (handoff / "sample_selection.jsonl").read_bytes()
            )
            self.assertIn("SOURCE_METADATA_MISMATCH", selection["reason_codes"])
            self.assertEqual(
                selection["source_line_sha256"],
                hashlib.sha256(fixture["parsed_bytes"]).hexdigest(),
            )

            handoff_manifest = json.loads(
                (handoff / "handoff_manifest.json").read_bytes()
            )
            self.assertTrue(handoff_manifest["ready_for_downstream"])
            self.assertEqual(handoff_manifest["corpus_counts"]["sample"], 1)
            self.assertEqual(
                handoff_manifest["artifacts"]["sample_dataset"]["sha256"],
                hashlib.sha256(fixture["parsed_bytes"]).hexdigest(),
            )
            self.assertTrue((reports / "metadata_report.md").is_file())
            self.assertTrue((reports / "metadata_summary.json").is_file())

    def test_failed_quality_gate_does_not_publish_staged_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            output = root / "output"
            reports = root / "reports"
            handoff = root / "handoff"
            output.mkdir()
            sentinel = output / "metadata_normalized.jsonl"
            sentinel.write_text("previous-generation\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "quality gates failed"):
                normalize_metadata_corpus(
                    fixture["corpus"],
                    parsed_path=fixture["parsed"],
                    parse_warnings_path=fixture["warnings"],
                    output_dir=output,
                    report_dir=reports,
                    handoff_dir=handoff,
                    data_contract_path=fixture["contract"],
                    expected_document_count=1,
                    expected_mismatch_count=0,
                    sample_count=1,
                    progress_every=0,
                )

            self.assertEqual(
                sentinel.read_text(encoding="utf-8"), "previous-generation\n"
            )
            self.assertFalse((handoff / "handoff_manifest.json").exists())
            self.assertEqual(list(output.glob(".phase3-*")), [])

    def test_full_corpus_sample_size_is_bounded(self) -> None:
        descriptors = [{} for _ in range(50)]
        with self.assertRaisesRegex(ValueError, "between 50 and 100"):
            _select_sample(descriptors, 49)
        with self.assertRaisesRegex(ValueError, "between 50 and 100"):
            _select_sample(descriptors, 101)

    def test_rejected_output_inside_corpus_is_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            forbidden_output = fixture["corpus"] / "generated"
            with self.assertRaisesRegex(ValueError, "read-only corpus root"):
                normalize_metadata_corpus(
                    fixture["corpus"],
                    parsed_path=fixture["parsed"],
                    parse_warnings_path=fixture["warnings"],
                    output_dir=forbidden_output,
                    report_dir=root / "reports",
                    handoff_dir=root / "handoff",
                    data_contract_path=fixture["contract"],
                    expected_document_count=1,
                    expected_mismatch_count=1,
                    sample_count=1,
                    progress_every=0,
                )
            self.assertFalse(forbidden_output.exists())

    def test_mismatch_warning_multiset_must_match_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            parsed_record = json.loads(fixture["parsed"].read_bytes())
            parsed_record["warnings"].append(dict(parsed_record["warnings"][0]))
            fixture["parsed"].write_bytes(_json_line(parsed_record))

            with self.assertRaisesRegex(RuntimeError, "quality gates failed"):
                normalize_metadata_corpus(
                    fixture["corpus"],
                    parsed_path=fixture["parsed"],
                    parse_warnings_path=fixture["warnings"],
                    output_dir=root / "output",
                    report_dir=root / "reports",
                    handoff_dir=root / "handoff",
                    data_contract_path=fixture["contract"],
                    expected_document_count=1,
                    expected_mismatch_count=1,
                    sample_count=1,
                    progress_every=0,
                )


if __name__ == "__main__":
    unittest.main()
