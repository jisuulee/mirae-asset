from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from disclosure_preprocess.parser_reporting import (
    render_parser_report,
    write_parser_reports,
)


def _summary_fixture() -> dict[str, object]:
    return {
        "schema_version": "2.0.0-test",
        "run": {
            "started_at_utc": "2026-08-25T01:02:03+00:00",
            "finished_at_utc": "2026-08-25T01:02:15+00:00",
            "duration_seconds": 12.345,
            "python_version": "3.13.14",
            "python_executable": "python",
            "platform": "test",
            "dependencies": {"lxml": "test", "pypdf": "test"},
            "document_limit": None,
            "complete_corpus_parse": True,
        },
        "source": {
            "kind": "zip",
            "path": "fixture.zip",
            "logical_root": "fixture/corpus",
            "source_issue_count": 1,
            "source_issue_examples": [
                {
                    "stage": "read",
                    "path": "raw/broken.xml",
                    "error": "fixture failure",
                }
            ],
        },
        "logical_documents": {
            "manifest_document_count": 17,
            "expected_document_count": 17,
            "processed_target_count": 17,
            "output_line_count": 17,
            "matches_manifest_count": True,
            "matches_expected_count": True,
            "status_counts": {"success": 14, "partial": 2, "failed": 1},
            "doc_group_counts": {"exchange": 9, "periodic": 8},
            "status_by_doc_group": {
                "exchange": {"success": 8, "partial": 1},
                "periodic": {"success": 6, "partial": 1, "failed": 1},
            },
        },
        "physical_files": {
            "parsed_file_count": 21,
            "sha256_count": 21,
            "detected_format_counts": {"xml": 12, "html": 7, "pdf": 2},
            "parse_status_counts": {"success": 18, "partial": 2, "failed": 1},
            "status_by_detected_format": {
                "xml": {"success": 11, "partial": 1},
                "html": {"success": 6, "partial": 1},
                "pdf": {"success": 1, "failed": 1},
            },
            "role_counts": {
                "main": 15,
                "viewer_html": 2,
                "primary_pdf": 2,
                "audit_report": 2,
            },
            "attachment_count": 4,
        },
        "extraction": {
            "main_title_document_count": 15,
            "main_title_missing_count": 2,
            "main_body_document_count": 16,
            "main_empty_body_count": 1,
            "main_section_document_count": 14,
            "main_no_section_count": 3,
            "main_section_count": 50,
            "main_table_document_count": 12,
            "main_table_count": 71,
            "all_physical_block_count": 200,
            "all_physical_section_count": 80,
            "all_physical_table_count": 91,
        },
        "warnings": {
            "occurrence_count": 6,
            "counts_by_code": {
                "ENCODING_DECLARATION_MISMATCH": 2,
                "PDF_FALLBACK_USED": 1,
                "MALFORMED_TABLE": 1,
                "ATTACHMENT_ROLE_UNKNOWN": 1,
                "SOURCE_METADATA_MISMATCH": 1,
            },
            "counts_by_severity": {"warning": 5, "error": 1},
            "pdf_fallback_count": 1,
            "source_metadata_mismatch_count": 1,
        },
        "suspicious_documents": {
            "title_missing_examples": [
                {
                    "doc_id": "doc-title",
                    "rcept_no": "0001",
                    "doc_group": "exchange",
                    "report_nm": "제목 없음",
                }
            ],
            "empty_body_examples": [
                {
                    "doc_id": "doc-empty",
                    "rcept_no": "0002",
                    "doc_group": "periodic",
                    "report_nm": "본문 없음",
                }
            ],
            "no_section_examples": [],
            "failed_examples": [
                {
                    "doc_id": "doc-failed",
                    "rcept_no": "0003",
                    "doc_group": "periodic",
                    "report_nm": "실패",
                }
            ],
            "partial_examples": [],
            "malformed_table_examples": [],
            "unknown_attachment_role_examples": [],
            "source_metadata_mismatch_examples": [],
        },
        "auxiliary_json": {
            "file_count": 3,
            "parse_status_counts": {"success": 3},
            "root_type_counts": {"list": 3},
            "record_count": 42,
            "included_as_disclosures": False,
        },
        "unmatched_raw_file_count": 2,
        "parsing_examples": [
            {
                "kind": "exchange",
                "doc_id": "doc-exchange",
                "rcept_no": "1001",
                "corp_name": "회사A",
                "report_nm": "계약 | 체결",
                "source_title": "계약",
                "main_format": "html",
                "main_role": "main",
                "attachment_roles": [],
                "section_count": 1,
                "table_count": 2,
                "source_path": "raw/exchange/a.xml",
                "text_preview": "첫 줄\n둘째 줄",
            },
            {
                "kind": "periodic_multi_file",
                "doc_id": "doc-periodic",
                "rcept_no": "1002",
                "corp_name": "회사B",
                "report_nm": "사업보고서",
                "source_title": "사업보고서",
                "main_format": "xml",
                "main_role": "main",
                "attachment_roles": ["audit_report"],
                "section_count": 10,
                "table_count": 20,
                "source_path": "raw/periodic/b.xml",
                "text_preview": "원문 미리보기",
            },
            {
                "kind": "pdf_fallback",
                "doc_id": "doc-pdf",
                "rcept_no": "1003",
                "corp_name": "회사C",
                "report_nm": "분기보고서",
                "source_title": "분기보고서",
                "main_format": "pdf",
                "main_role": "primary_pdf",
                "attachment_roles": ["viewer_html"],
                "section_count": 0,
                "table_count": 0,
                "source_path": "raw/periodic/c.pdf",
                "text_preview": "PDF 원문",
            },
        ],
        "output_files": {},
        "guarantees": {
            "manifest_row_is_logical_unit": True,
            "group_listing_json_excluded": True,
            "dispatch_is_content_based": True,
            "strict_utf8_first": True,
            "silent_decode_ignore_or_replace": False,
            "semantic_inference_or_summarization": False,
            "source_sha256_recorded": True,
            "block_table_provenance_recorded": True,
            "raw_source_mutated": False,
        },
    }


class ParserReportingTests(unittest.TestCase):
    def test_atomic_writer_round_trips_json_and_renders_required_sections(self) -> None:
        summary = _summary_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            report_dir = Path(temporary) / "nested" / "reports"
            with patch(
                "disclosure_preprocess.parser_reporting.os.replace",
                wraps=os.replace,
            ) as replace:
                outputs = write_parser_reports(summary, report_dir)

            self.assertEqual(
                outputs,
                {
                    "parser_summary": report_dir / "parser_summary.json",
                    "parser_report": report_dir / "parser_report.md",
                },
            )
            self.assertEqual(replace.call_count, 2)
            self.assertEqual(
                json.loads(outputs["parser_summary"].read_text(encoding="utf-8")),
                summary,
            )
            report = outputs["parser_report"].read_text(encoding="utf-8")
            self.assertIn("manifest 논리 공시는 **17건**", report)
            self.assertIn("success | 14", report)
            self.assertIn("main title 추출 성공률 | 88.24%", report)
            self.assertIn("main non-empty body 비율 | 94.12%", report)
            self.assertIn("ENCODING_DECLARATION_MISMATCH | 2", report)
            self.assertIn("PDF fallback", report)
            self.assertIn("기업별 수집 목록 JSON은 **3개**", report)
            self.assertIn("최종 공시 JSONL에는 포함되지 않는다", report)
            self.assertIn("대표 예시 **3건**", report)
            self.assertIn("계약 \\| 체결", report)
            self.assertIn("첫 줄 둘째 줄", report)
            self.assertIn("Phase 3 전 확인할 이슈", report)
            self.assertIn("partial/failed 원인 신호 분류", report)
            self.assertIn("표는 행·열/cell 구조로 역추적할 수 없으며", report)
            self.assertIn("소스에 없는 정보가 생성될 가능성이 있는가?", report)
            self.assertIn("**아니오.** parser는 원문 추출", report)
            self.assertIn(
                "추출된 숫자·표·문장을 물리 소스까지 역추적할 수 있는가?",
                report,
            )
            self.assertIn("**구조화 항목은 예.**", report)
            self.assertIn("개별 문자 범위(char offset) 역매핑은", report)
            self.assertEqual(
                sorted(path.name for path in report_dir.iterdir()),
                ["parser_report.md", "parser_summary.json"],
            )

    def test_render_is_data_driven_for_limited_run(self) -> None:
        summary = _summary_fixture()
        run = summary["run"]
        logical = summary["logical_documents"]
        assert isinstance(run, dict)
        assert isinstance(logical, dict)
        run["complete_corpus_parse"] = False
        run["document_limit"] = 7
        logical["processed_target_count"] = 7
        logical["output_line_count"] = 7
        logical["matches_manifest_count"] = False

        report = render_parser_report(summary)

        self.assertIn("이번 실행 대상은 **7건**", report)
        self.assertIn("document_limit=7인 제한 실행", report)
        self.assertNotIn("요구 기준은 **4,204건**", report)

    def test_traceability_answer_requires_complete_sha_coverage(self) -> None:
        summary = _summary_fixture()
        physical = summary["physical_files"]
        assert isinstance(physical, dict)
        physical["sha256_count"] = 20

        report = render_parser_report(summary)

        self.assertIn("**완전하게 보증할 수 없다.**", report)

    def test_failed_replace_keeps_existing_file_and_cleans_temp(self) -> None:
        summary = _summary_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            report_dir = Path(temporary)
            summary_path = report_dir / "parser_summary.json"
            report_path = report_dir / "parser_report.md"
            summary_path.write_text("old-summary", encoding="utf-8")
            report_path.write_text("old-report", encoding="utf-8")

            with patch(
                "disclosure_preprocess.parser_reporting.os.replace",
                side_effect=OSError("publish failed"),
            ):
                with self.assertRaisesRegex(OSError, "publish failed"):
                    write_parser_reports(summary, report_dir)

            self.assertEqual(summary_path.read_text(encoding="utf-8"), "old-summary")
            self.assertEqual(report_path.read_text(encoding="utf-8"), "old-report")
            self.assertEqual(
                sorted(path.name for path in report_dir.iterdir()),
                ["parser_report.md", "parser_summary.json"],
            )

    def test_second_artifact_publish_failure_rolls_back_both_reports(self) -> None:
        summary = _summary_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            report_dir = Path(temporary)
            summary_path = report_dir / "parser_summary.json"
            report_path = report_dir / "parser_report.md"
            summary_path.write_text("old-summary", encoding="utf-8")
            report_path.write_text("old-report", encoding="utf-8")
            real_replace = os.replace
            calls = 0

            def fail_fourth(source: object, target: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise OSError("second artifact publish failed")
                real_replace(source, target)

            with patch(
                "disclosure_preprocess.parser_reporting.os.replace",
                side_effect=fail_fourth,
            ):
                with self.assertRaisesRegex(OSError, "second artifact"):
                    write_parser_reports(summary, report_dir)

            self.assertEqual(summary_path.read_text(encoding="utf-8"), "old-summary")
            self.assertEqual(report_path.read_text(encoding="utf-8"), "old-report")
            self.assertEqual(
                sorted(path.name for path in report_dir.iterdir()),
                ["parser_report.md", "parser_summary.json"],
            )


if __name__ == "__main__":
    unittest.main()
