from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from disclosure_preprocess.final_pipeline import FinalValidationError, assemble_final_dataset


def _line(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _manifest(doc_id: str, receipt: str, date: str, correction: bool) -> dict[str, object]:
    return {
        "doc_id": doc_id,
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "listed_name": "삼성전자",
        "stock_code": "005930",
        "industry": "전기전자",
        "sector": "반도체",
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "report_nm": "[기재정정]사업보고서" if correction else "사업보고서",
        "is_correction": correction,
        "rcept_no": receipt,
        "rcept_dt": date.replace("-", ""),
        "flr_nm": "삼성전자",
        "base_year": 2024,
        "base_month": 12,
    }


def _metadata(manifest: dict[str, object]) -> dict[str, object]:
    metadata = dict(manifest)
    raw_date = str(metadata["rcept_dt"])
    metadata["rcept_date"] = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
    metadata["authority"] = "manifest.jsonl"
    return {
        "schema_version": "3.0.0",
        "content_schema_version": "2.0.0",
        "doc_id": manifest["doc_id"],
        "rcept_no": manifest["rcept_no"],
        "metadata": metadata,
        "metadata_validation": {"status": "valid", "mismatch_count": 0, "warnings": []},
        "parse_status": "success",
    }


def _parsed(manifest: dict[str, object]) -> dict[str, object]:
    doc_id = str(manifest["doc_id"])
    receipt = str(manifest["rcept_no"])
    provenance = {"source_file": f"raw/{doc_id}.xml", "file_role": "main", "dom_path": "/DOCUMENT"}
    return {
        "doc_id": doc_id,
        "rcept_no": receipt,
        "parse_status": "success",
        "source_files": [{"relative_path": f"raw/{doc_id}.xml", "role": "main", "sha256": "a" * 64}],
        "main_document": {
            "warnings": [],
            "content": {
                "title": "사업보고서",
                "body_text": "본문",
                "sections": [],
                "tables": [],
                "block_sequence": [{"block_type": "paragraph", "text": "본문", "items": [], "provenance": provenance}],
                "title_provenance": provenance,
                "provenance": provenance,
            },
        },
        "attachments": [],
        "alternative_representations": [],
        "warnings": [],
    }


class FinalPipelineTests(unittest.TestCase):
    def _fixture(self, root: Path) -> dict[str, Path]:
        source = root / "source"
        source.mkdir()
        original = _manifest("periodic_root", "20250101000001", "2025-01-01", False)
        correction = _manifest("periodic_corr", "20250102000002", "2025-01-02", True)
        (source / "manifest.jsonl").write_bytes(_line(original) + _line(correction))
        processed = root / "processed"
        processed.mkdir()
        metadata_path = processed / "metadata.jsonl"
        parsed_path = processed / "parsed.jsonl"
        lineage_path = processed / "lineage.jsonl"
        chains_path = processed / "chains.jsonl"
        metadata_path.write_bytes(_line(_metadata(original)) + _line(_metadata(correction)))
        parsed_path.write_bytes(_line(_parsed(original)) + _line(_parsed(correction)))
        lineage = {
            "schema_version": "4.0.0",
            "doc_id": "periodic_corr",
            "rcept_no": "20250102000002",
            "is_correction": True,
            "resolution": {"status": "linked_exact", "method": "test", "confidence": 1.0, "target_rcept_no": "20250101000001"},
            "lineage": {"parent_rcept_no": "20250101000001", "root_rcept_no": "20250101000001", "latest_rcept_no": "20250102000002", "chain_id": "chain_test", "sequence": 1},
            "correction_evidence": {"target_submission_date": "2025-01-01", "evidence": [{"field": "original_submission_date"}], "evidence_count": 1},
        }
        chain = {
            "schema_version": "4.0.0",
            "chain_id": "chain_test",
            "root_rcept_no": "20250101000001",
            "root_doc_id": "periodic_root",
            "anchor_rcept_no": "20250101000001",
            "latest_rcept_no": "20250102000002",
            "corp_code": "00126380",
            "doc_group": "periodic",
            "member_count": 2,
            "correction_count": 1,
            "is_complete": True,
            "members": [
                {"sequence": 0, "rcept_no": "20250101000001", "doc_id": "periodic_root", "is_correction": False},
                {"sequence": 1, "rcept_no": "20250102000002", "doc_id": "periodic_corr", "is_correction": True},
            ],
        }
        lineage_path.write_bytes(_line(lineage))
        chains_path.write_bytes(_line(chain))
        reports = root / "reports"
        reports.mkdir()
        (reports / "parser_summary.json").write_text(json.dumps({"extraction": {"provenance_missing_count": 0}}), encoding="utf-8")
        docs = root / "docs"
        docs.mkdir()
        (docs / "data_contract.md").write_text("# contract\n", encoding="utf-8")
        return {
            "source": source,
            "parsed": parsed_path,
            "metadata": metadata_path,
            "lineage": lineage_path,
            "chains": chains_path,
            "reports": reports,
            "contract": docs / "data_contract.md",
        }

    def _run(self, root: Path, paths: dict[str, Path], **overrides: object) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "project_root": root,
            "data_root": paths["source"],
            "parsed_path": paths["parsed"],
            "metadata_path": paths["metadata"],
            "lineage_path": paths["lineage"],
            "chains_path": paths["chains"],
            "output_dir": root / "final",
            "report_dir": paths["reports"],
            "handoff_dir": root / "handoff",
            "data_contract_path": paths["contract"],
            "expected_hashes": None,
            "expected_document_count": 2,
            "expected_correction_count": 1,
            "expected_chain_count": 1,
            "sample_count": 2,
            "strict_corpus_metrics": False,
            "enforce_sample_coverage": False,
            "progress_every": 0,
        }
        kwargs.update(overrides)
        return assemble_final_dataset(**kwargs)

    def test_small_integration_join_and_atomic_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixture(root)
            original_hashes = {name: hashlib.sha256(paths[name].read_bytes()).hexdigest() for name in ("parsed", "metadata", "lineage", "chains")}
            summary = self._run(root, paths)
            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(summary["joins"]["matched_count"], 2)
            index_lines = (root / "final" / "document_index.jsonl").read_bytes().splitlines()
            self.assertEqual(len(index_lines), 2)
            rows = [json.loads(line) for line in index_lines]
            root_row = next(row for row in rows if row["doc_id"] == "periodic_root")
            corr_row = next(row for row in rows if row["doc_id"] == "periodic_corr")
            self.assertTrue(root_row["lineage"]["is_superseded_in_complete_chain"])
            self.assertTrue(corr_row["lineage"]["is_latest_in_complete_chain"])
            self.assertEqual(
                (root / "final" / "document_index.jsonl").read_bytes(),
                (root / "handoff" / "document_index.jsonl").read_bytes(),
            )
            for name, digest in original_hashes.items():
                self.assertEqual(hashlib.sha256(paths[name].read_bytes()).hexdigest(), digest)

    def test_hash_mismatch_fails_without_publishing_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._fixture(root)
            with self.assertRaisesRegex(FinalValidationError, "FINAL_VALIDATION_FAILED"):
                self._run(root, paths, expected_hashes={"parsed_disclosures": "0" * 64})
            self.assertFalse((root / "final" / "document_index.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
