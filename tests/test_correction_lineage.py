from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from disclosure_preprocess.correction_lineage import (
    CorrectionKind,
    MetadataIndex,
    ResolutionStatus,
    apply_chain_backtrack,
    build_chains,
    classify_correction_kind,
    extract_correction_evidence,
    guard_resolution_cycles,
    normalize_report_key,
    normalize_source_date,
    resolve_one,
    stable_chain_id,
    validate_lineage,
)
from disclosure_preprocess.opendart import OpenDartClient, OpenDartError


def _metadata(
    doc_id: str,
    rcept_no: str,
    rcept_date: str,
    *,
    correction: bool,
    report_nm: str = "사업보고서 (2024.12)",
    group: str = "periodic",
    subtype: str | None = "annual",
) -> dict[str, object]:
    return {
        "schema_version": "3.0.0",
        "doc_id": doc_id,
        "rcept_no": rcept_no,
        "metadata": {
            "doc_id": doc_id,
            "rcept_no": rcept_no,
            "corp_code": "00126380",
            "corp_name": "삼성전자",
            "listed_name": "삼성전자",
            "stock_code": "005930",
            "doc_group": group,
            "doc_subtype": subtype,
            "report_nm": report_nm,
            "rcept_dt": rcept_date.replace("-", ""),
            "rcept_date": rcept_date,
            "is_correction": correction,
        },
    }


def _parsed_with_table() -> dict[str, object]:
    def cell(text: str, column: int) -> dict[str, object]:
        return {
            "text": text,
            "raw_text": text,
            "provenance": {"dom_path": f"/TABLE/TR[1]/TD[{column}]"},
        }

    rows = [
        [cell("1. 정정대상 공시서류 :", 1), cell("사업보고서", 2)],
        [cell("2. 정정대상 공시서류의 최초제출일 :", 1), cell("2025년 3월 18일", 2)],
        [cell("항목", 1), cell("정정사유", 2), cell("정 정 전", 3), cell("정 정 후", 4)],
        [cell("정정사유", 1), cell("단순 기재오류", 2)],
    ]
    return {
        "main_document": {
            "source_file": {"relative_path": "raw/periodic/test/main.xml", "role": "main"},
            "content": {"tables": [{"raw_rows": rows}], "block_sequence": []},
        },
        "attachments": [],
    }


class CorrectionNormalizationTests(unittest.TestCase):
    def test_dates_are_strict_and_preserve_unknown(self) -> None:
        self.assertEqual(normalize_source_date("2025년 3월 8일"), "2025-03-08")
        self.assertEqual(normalize_source_date("2025-03-08"), "2025-03-08")
        self.assertEqual(normalize_source_date("2025. 03. 08"), "2025-03-08")
        self.assertEqual(normalize_source_date("20250308"), "2025-03-08")
        self.assertIsNone(normalize_source_date("2025-02-30"))
        self.assertIsNone(normalize_source_date("날짜 없음"))

    def test_report_and_kind_normalization(self) -> None:
        self.assertEqual(normalize_report_key("[기재정정] 사업보고서 (2024.12)"), "사업보고서202412")
        self.assertEqual(classify_correction_kind("[기재정정]사업보고서"), CorrectionKind.STATEMENT)
        self.assertEqual(classify_correction_kind("[첨부정정]보고서"), CorrectionKind.ATTACHMENT)
        self.assertEqual(classify_correction_kind("[정정명령]보고서"), CorrectionKind.ORDER)
        self.assertEqual(classify_correction_kind("[철회]보고서"), CorrectionKind.WITHDRAWAL)

    def test_table_evidence_is_raw_normalized_and_located(self) -> None:
        result = extract_correction_evidence(_parsed_with_table())
        self.assertEqual(result["target_report_name_raw"], "사업보고서")
        self.assertEqual(result["target_submission_date"], "2025-03-18")
        self.assertEqual(result["target_date_semantics"], "original_submission_date")
        self.assertEqual(result["correction_reason"], "단순 기재오류")
        self.assertTrue(result["before_present"])
        self.assertTrue(result["after_present"])
        date_evidence = next(item for item in result["evidence"] if item["field"] == "original_submission_date")
        self.assertEqual(date_evidence["raw_value"], "2025년 3월 18일")
        self.assertEqual(date_evidence["provenance"]["dom_path"], "/TABLE/TR[1]/TD[2]")

    def test_block_evidence_and_label_precedence(self) -> None:
        parsed = {
            "main_document": {
                "source_file": {"relative_path": "x.xml", "role": "main"},
                "content": {
                    "tables": [],
                    "block_sequence": [
                        {"text": "1. 정정대상 공시서류 : 주요사항보고서(유상증자결정)", "provenance": {"dom_path": "/P[1]"}},
                        {"text": "2. 정정대상 공시서류의 최초제출일 : 2024년 11월 18일", "provenance": {"dom_path": "/P[2]"}},
                    ],
                },
            }
        }
        result = extract_correction_evidence(parsed)
        self.assertEqual(result["target_report_name_raw"], "주요사항보고서(유상증자결정)")
        self.assertEqual(result["target_submission_date"], "2024-11-18")
        self.assertEqual(sum(item["field"] == "target_report_name" for item in result["evidence"]), 1)


class ResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _metadata("periodic_root", "20250318000001", "2025-03-18", correction=False)
        self.correction = _metadata("periodic_corr", "20250319000002", "2025-03-19", correction=True, report_nm="[기재정정]사업보고서 (2024.12)")
        self.index = MetadataIndex.build([self.root, self.correction])
        self.evidence = extract_correction_evidence(_parsed_with_table())

    def test_exact_company_date_report_link(self) -> None:
        result = resolve_one(self.correction, self.evidence, self.index)
        self.assertEqual(result["resolution"]["status"], ResolutionStatus.LINKED_EXACT)
        self.assertEqual(result["resolution"]["target_rcept_no"], "20250318000001")

    def test_inferred_classification_link(self) -> None:
        evidence = dict(self.evidence)
        evidence["target_report_name_raw"] = "표현이 다른 보고서"
        result = resolve_one(self.correction, evidence, self.index)
        self.assertEqual(result["resolution"]["status"], ResolutionStatus.LINKED_INFERRED)

    def test_missing_source_report_cannot_be_exact(self) -> None:
        evidence = dict(self.evidence)
        evidence["target_report_name_raw"] = None
        evidence["target_report_key"] = None
        result = resolve_one(self.correction, evidence, self.index)
        self.assertEqual(result["resolution"]["status"], ResolutionStatus.LINKED_INFERRED)
        report_stage = next(stage for stage in result["resolution"]["stages"] if stage["stage"] == "company_date_report")
        self.assertEqual(report_stage["skipped_reason"], "TARGET_REPORT_MISSING")

    def test_invalid_original_date_keeps_raw_top_level_value(self) -> None:
        parsed = {
            "main_document": {
                "source_file": {"relative_path": "x.xml", "role": "main"},
                "content": {"tables": [], "block_sequence": [{"text": "2. 정정대상 공시서류의 최초제출일 : 2025년 08년 28일"}]},
            }
        }
        result = extract_correction_evidence(parsed)
        self.assertEqual(result["target_submission_date_raw"], "2025년 08년 28일")
        self.assertIsNone(result["target_submission_date"])
        self.assertEqual(result["target_date_semantics"], "original_submission_date")

    def test_ambiguous_is_not_broken_by_proximity(self) -> None:
        second = _metadata("periodic_root_2", "20250318000009", "2025-03-18", correction=False)
        index = MetadataIndex.build([self.root, second, self.correction])
        result = resolve_one(self.correction, self.evidence, index)
        self.assertEqual(result["resolution"]["status"], ResolutionStatus.AMBIGUOUS)
        self.assertIsNone(result["resolution"]["target_rcept_no"])
        self.assertEqual(result["resolution"]["candidate_count"], 2)

    def test_unresolved_missing_date(self) -> None:
        evidence = dict(self.evidence)
        evidence["target_submission_date"] = None
        result = resolve_one(self.correction, evidence, self.index)
        self.assertEqual(result["resolution"]["status"], ResolutionStatus.UNRESOLVED)

    def test_chain_semantics_and_invariants(self) -> None:
        first = resolve_one(self.correction, self.evidence, self.index)
        second_row = _metadata("periodic_corr_2", "20250320000003", "2025-03-20", correction=True, report_nm="[기재정정]사업보고서 (2024.12)")
        expanded = MetadataIndex.build([self.root, self.correction, second_row])
        second = resolve_one(second_row, self.evidence, expanded)
        chains = build_chains([first, second], expanded)
        self.assertEqual(len(chains), 1)
        self.assertEqual(first["lineage"]["parent_rcept_no"], "20250318000001")
        self.assertEqual(second["lineage"]["parent_rcept_no"], "20250319000002")
        self.assertEqual(second["lineage"]["latest_rcept_no"], "20250320000003")
        self.assertEqual(chains[0]["chain_id"], stable_chain_id("20250318000001"))
        self.assertTrue(validate_lineage([first, second], chains, expanded)["passed"])

    def test_partial_chain_preserves_known_edge_without_inventing_root(self) -> None:
        unresolved_parent = resolve_one(self.correction, {**self.evidence, "target_submission_date": None}, self.index)
        child_row = _metadata("periodic_child", "20250320000003", "2025-03-20", correction=True, report_nm="[기재정정]사업보고서 (2024.12)")
        expanded = MetadataIndex.build([self.root, self.correction, child_row])
        child_evidence = {**self.evidence, "target_submission_date": "2025-03-19"}
        child = resolve_one(child_row, child_evidence, expanded)
        records = [unresolved_parent, child]
        apply_chain_backtrack(records, expanded)
        chains = build_chains(records, expanded)
        self.assertEqual(len(chains), 1)
        self.assertFalse(chains[0]["is_complete"])
        self.assertIsNone(chains[0]["root_rcept_no"])
        self.assertIsNone(child["lineage"]["sequence"])
        self.assertTrue(validate_lineage(records, chains, expanded)["passed"])

    def test_cycle_is_demoted_without_receipt_order_guess(self) -> None:
        first = resolve_one(self.correction, self.evidence, self.index)
        second_row = _metadata("periodic_second", "20250318000001", "2025-03-18", correction=True, report_nm="[기재정정]사업보고서 (2024.12)")
        # Construct the global contradiction directly: A -> B and B -> A.
        first["resolution"]["target_rcept_no"] = "20250318000001"
        first["resolution"]["target_doc_id"] = "periodic_second"
        second = resolve_one(second_row, {**self.evidence, "target_submission_date": "2025-03-19"}, MetadataIndex.build([self.correction, second_row]))
        cycles = guard_resolution_cycles([first, second])
        self.assertEqual(len(cycles), 1)
        self.assertEqual(first["resolution"]["status"], ResolutionStatus.AMBIGUOUS)
        self.assertEqual(second["resolution"]["status"], ResolutionStatus.AMBIGUOUS)
        self.assertIsNone(first["resolution"]["target_rcept_no"])


class OpenDartTests(unittest.TestCase):
    def test_mocked_lookup_is_cached_without_storing_api_key(self) -> None:
        calls: list[str] = []

        def transport(url: str, timeout: float) -> bytes:
            calls.append(url)
            return json.dumps({"status": "000", "list": [{"rcept_no": "20250101000001"}]}).encode()

        with tempfile.TemporaryDirectory() as directory:
            client = OpenDartClient("secret-key", directory, transport=transport)
            first = client.list_filings(corp_code="00126380", begin_date="2025-01-01", end_date="2025-01-01")
            second = client.list_filings(corp_code="00126380", begin_date="2025-01-01", end_date="2025-01-01")
            self.assertEqual(first["source"], "network")
            self.assertEqual(second["source"], "cache")
            self.assertEqual(len(calls), 1)
            cache_text = next(Path(directory).glob("*.json")).read_text(encoding="utf-8")
            self.assertNotIn("secret-key", cache_text)

    def test_api_status_is_classified(self) -> None:
        def transport(url: str, timeout: float) -> bytes:
            return b'{"status":"020","message":"limit"}'

        with tempfile.TemporaryDirectory() as directory:
            client = OpenDartClient("key", directory, transport=transport)
            with self.assertRaises(OpenDartError) as caught:
                client.list_filings(corp_code="00126380", begin_date="20250101", end_date="20250101")
            self.assertEqual(caught.exception.code, "RATE_LIMIT")


if __name__ == "__main__":
    unittest.main()
