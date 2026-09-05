from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from disclosure_preprocess.models import (
    BlockType,
    ContentBlock,
    DocumentContent,
    ParseResult,
    ParseStatus,
    ParseWarning,
    Provenance,
    SourceFileRecord,
)
from disclosure_preprocess.parsers.base import ParseContext, read_binary_stream
from disclosure_preprocess.parsers.parser_factory import parse_payload
from disclosure_preprocess.pipeline import (
    SOURCE_METADATA_MISMATCH,
    _append_metadata_mismatch_warnings,
    _assemble_disclosure,
    _publish_stage,
    _stage_files,
    parse_corpus,
)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _xml(document_name: str, body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f"<DOCUMENT><DOCUMENT-NAME>{document_name}</DOCUMENT-NAME>"
        f"<BODY>{body}</BODY></DOCUMENT>"
    ).encode("utf-8")


def _pdf_with_text(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _record(
    doc_id: str,
    rcept_no: str,
    group: str,
    file_path: str,
    *,
    n_files: int,
    file_format: str = "xml",
) -> dict[str, object]:
    return {
        "doc_id": doc_id,
        "corp_code": "00123456",
        "corp_name": "테스트회사",
        "listed_name": "테스트회사",
        "stock_code": "001234",
        "doc_group": group,
        "doc_subtype": "fixture",
        "report_nm": "테스트보고서 (2025.01)",
        "is_correction": False,
        "rcept_no": rcept_no,
        "rcept_dt": "20250101",
        "file_path": file_path,
        "file_format": file_format,
        "n_files": n_files,
    }


class PipelineIntegrationTests(unittest.TestCase):
    def test_metadata_comparison_ignores_legal_suffix_and_attachment_title(self) -> None:
        main_source = SourceFileRecord("raw/main.xml", "main", "xml")
        attachment_source = SourceFileRecord(
            "raw/audit.xml", "audit_report", "xml"
        )
        main = ParseResult(
            source_file=main_source,
            parse_status=ParseStatus.SUCCESS,
            source_metadata={
                "rcept_no": "20250101000001",
                "company_name": "테스트회사주식회사",
                "document_name": "사업보고서",
            },
        )
        attachment = ParseResult(
            source_file=attachment_source,
            parse_status=ParseStatus.SUCCESS,
            source_metadata={
                "rcept_no": "20250101000001",
                "company_name": "(주)테스트회사",
                "document_name": "감사보고서",
            },
        )
        record = {
            "rcept_no": "20250101000001",
            "corp_name": "테스트회사",
            "report_nm": "사업보고서 (2024.12)",
        }
        warnings: list[ParseWarning] = []
        _append_metadata_mismatch_warnings([main, attachment], record, warnings)
        self.assertEqual(warnings, [])

        main.source_metadata["company_name"] = "다른회사"
        _append_metadata_mismatch_warnings([main], record, warnings)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].code, SOURCE_METADATA_MISMATCH)

    def test_processed_generation_publish_rolls_back_all_three_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            names = (
                "parsed_disclosures.jsonl",
                "parse_warnings.jsonl",
                "parse_failures.jsonl",
            )
            for name in names:
                (output_dir / name).write_text(f"old-{name}", encoding="utf-8")
            stage, staged = _stage_files(output_dir)
            for path in staged.values():
                path.write_text(f"new-{path.name}", encoding="utf-8")

            real_replace = os.replace
            calls = 0

            def fail_fifth(source: object, target: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 5:
                    raise OSError("second processed artifact failed")
                real_replace(source, target)

            with patch(
                "disclosure_preprocess.pipeline.os.replace",
                side_effect=fail_fifth,
            ):
                with self.assertRaisesRegex(OSError, "second processed"):
                    _publish_stage(stage, output_dir, staged)

            for name in names:
                self.assertEqual(
                    (output_dir / name).read_text(encoding="utf-8"),
                    f"old-{name}",
                )

    def test_empty_table_is_not_substantive_and_audit_is_not_promoted(self) -> None:
        data = b"<DOCUMENT><DOCUMENT-NAME>Report</DOCUMENT-NAME><BODY><TABLE/></BODY></DOCUMENT>"
        empty_result = parse_payload(
            read_binary_stream(io.BytesIO(data)),
            ParseContext("raw/major/corp/1/1.xml", "xml", role="main"),
        )
        empty_disclosure = _assemble_disclosure(
            _record("major_empty", "1", "major", "raw/major/corp/1", n_files=1),
            [empty_result],
        )
        self.assertEqual(empty_disclosure.parse_status, ParseStatus.FAILED)
        self.assertIn(
            "EMPTY_BODY", {warning.code for warning in empty_result.warnings}
        )

        audit_source = SourceFileRecord(
            "raw/periodic/corp/2/2_00760.xml",
            "audit_report",
            "xml",
        )
        audit_result = ParseResult(
            source_file=audit_source,
            parse_status=ParseStatus.SUCCESS,
            content=DocumentContent(
                title="감사보고서",
                body_text="감사 근거",
                block_sequence=[
                    ContentBlock(
                        BlockType.PARAGRAPH,
                        raw_text="감사 근거",
                        text="감사 근거",
                        provenance=Provenance(
                            audit_source.relative_path, file_role="audit_report"
                        ),
                    )
                ],
            ),
        )
        audit_disclosure = _assemble_disclosure(
            _record(
                "periodic_missing_main",
                "2",
                "periodic",
                "raw/periodic/corp/2",
                n_files=1,
            ),
            [audit_result],
        )
        self.assertIsNone(audit_disclosure.main_document)
        self.assertEqual(audit_disclosure.attachments, [audit_result])
        self.assertEqual(audit_disclosure.parse_status, ParseStatus.FAILED)

    def test_viewer_pre_is_substantive_and_output_inside_corpus_is_never_created(self) -> None:
        viewer_data = b"<html><head><title>Viewer</title></head><body><pre>source text 001,234</pre></body></html>"
        viewer = parse_payload(
            read_binary_stream(io.BytesIO(viewer_data)),
            ParseContext(
                "raw/periodic/corp/3/3_viewer.html",
                "html",
                role="viewer_html",
            ),
        )
        pdf = parse_payload(
            read_binary_stream(io.BytesIO(_pdf_with_text("PDF alternative"))),
            ParseContext(
                "raw/periodic/corp/3/3.pdf",
                "pdf",
                role="primary_pdf",
            ),
        )
        disclosure = _assemble_disclosure(
            _record(
                "periodic_viewer_pre",
                "3",
                "periodic",
                "raw/periodic/corp/3",
                n_files=2,
                file_format="pdf+html",
            ),
            [viewer, pdf],
        )
        self.assertIs(disclosure.main_document, viewer)
        self.assertNotIn(
            "PDF_FALLBACK_USED", {warning.code for warning in disclosure.warnings}
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "manifest.jsonl").write_text("", encoding="utf-8")
            forbidden = corpus / "processed"
            with self.assertRaisesRegex(ValueError, "read-only corpus root"):
                parse_corpus(
                    corpus,
                    output_dir=forbidden,
                    report_dir=root / "reports",
                    expected_document_count=None,
                    progress_every=0,
                )
            self.assertFalse(forbidden.exists())

    def test_manifest_unit_roles_pdf_fallback_aux_exclusion_and_failure_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus"
            corpus.mkdir()
            records = [
                _record(
                    "exchange_1",
                    "20250101800001",
                    "exchange",
                    "raw/exchange/테스트회사/20250101800001",
                    n_files=1,
                ),
                _record(
                    "periodic_multi_1",
                    "20250102000002",
                    "periodic",
                    "raw/periodic/테스트회사/20250102000002_annual_2024_12",
                    n_files=3,
                ),
                _record(
                    "periodic_pdf_1",
                    "20250103000003",
                    "periodic",
                    "raw/periodic/테스트회사/20250103000003_annual_2024_12",
                    n_files=2,
                    file_format="pdf+html",
                ),
                _record(
                    "major_bad_1",
                    "20250104000004",
                    "major",
                    "raw/major/테스트회사/20250104000004",
                    n_files=1,
                ),
            ]
            manifest = "".join(
                json.dumps(record, ensure_ascii=False) + "\n" for record in records
            )
            (corpus / "manifest.jsonl").write_text(manifest, encoding="utf-8")

            _write(
                corpus
                / "raw/exchange/테스트회사/20250101800001/20250101800001.xml",
                """<!doctype html><html><head><meta charset="euc-kr"><title>계약 체결</title></head>
                <body><table><tr><td>97,000,000,000</td></tr></table></body></html>""".encode(
                    "utf-8"
                ),
            )
            multi = corpus / "raw/periodic/테스트회사/20250102000002_annual_2024_12"
            _write(
                multi / "20250102000002.xml",
                _xml("사업보고서", "<SECTION-1><TITLE>사업</TITLE><P>본문 1,234원</P></SECTION-1>"),
            )
            _write(
                multi / "20250102000002_00760.xml",
                _xml("감사보고서", "<P>감사 근거 문장</P>"),
            )
            _write(
                multi / "20250102000002_00761.xml",
                _xml("연결감사보고서", "<P>연결 감사 근거 문장</P>"),
            )

            pdf_pair = corpus / "raw/periodic/테스트회사/20250103000003_annual_2024_12"
            _write(
                pdf_pair / "20250103000003_viewer.html",
                b"<html><head><title>DART Viewer</title></head><body><div>loading viewer shell</div><script>load()</script></body></html>",
            )
            _write(
                pdf_pair / "20250103000003.pdf",
                _pdf_with_text("PDF source revenue 001,234.50"),
            )
            _write(
                corpus / "raw/major/테스트회사/20250104000004/20250104000004.xml",
                b"\xff\xfe\x00broken",
            )
            _write(
                corpus / "raw/exchange/테스트회사/list_I.json",
                json.dumps([{"rcept_no": "20250101800001"}]).encode(),
            )

            output_dir = root / "processed"
            report_dir = root / "reports"
            summary = parse_corpus(
                corpus,
                output_dir=output_dir,
                report_dir=report_dir,
                expected_document_count=4,
                progress_every=0,
            )

            with (output_dir / "parsed_disclosures.jsonl").open(
                encoding="utf-8"
            ) as stream:
                parsed = [json.loads(line) for line in stream]
            self.assertEqual(len(parsed), 4)
            self.assertEqual({item["doc_id"] for item in parsed}, {r["doc_id"] for r in records})
            self.assertFalse(
                any(item["doc_id"].startswith("list_") for item in parsed)
            )

            exchange = next(item for item in parsed if item["doc_id"] == "exchange_1")
            self.assertEqual(exchange["main_document"]["source_file"]["detected_format"], "html")
            self.assertEqual(
                exchange["main_document"]["content"]["tables"][0]["raw_rows"][0][0]["raw_text"],
                "97,000,000,000",
            )
            self.assertIn(
                "ENCODING_DECLARATION_MISMATCH",
                {item["code"] for item in exchange["main_document"]["warnings"]},
            )

            multi_row = next(item for item in parsed if item["doc_id"] == "periodic_multi_1")
            self.assertEqual(multi_row["main_document"]["source_file"]["role"], "main")
            self.assertEqual(
                {item["source_file"]["role"] for item in multi_row["attachments"]},
                {"audit_report", "consolidated_audit_report"},
            )

            pdf_row = next(item for item in parsed if item["doc_id"] == "periodic_pdf_1")
            self.assertEqual(pdf_row["parse_status"], "success")
            self.assertEqual(pdf_row["main_document"]["source_file"]["role"], "primary_pdf")
            self.assertIn("001,234.50", pdf_row["main_document"]["content"]["body_text"])
            self.assertIn("PDF_FALLBACK_USED", {item["code"] for item in pdf_row["warnings"]})
            self.assertEqual(pdf_row["attachments"], [])
            self.assertEqual(
                pdf_row["alternative_representations"][0]["source_file"]["role"],
                "viewer_html",
            )
            self.assertFalse(
                pdf_row["alternative_representations"][0]["content_included"]
            )
            self.assertNotIn(
                "content", pdf_row["alternative_representations"][0]
            )

            bad = next(item for item in parsed if item["doc_id"] == "major_bad_1")
            self.assertEqual(bad["parse_status"], "failed")
            with (output_dir / "parse_failures.jsonl").open(
                encoding="utf-8"
            ) as failure_stream:
                self.assertEqual(len(list(failure_stream)), 1)
            self.assertEqual(summary["logical_documents"]["output_line_count"], 4)
            self.assertTrue(summary["logical_documents"]["matches_manifest_count"])
            self.assertEqual(summary["auxiliary_json"]["file_count"], 1)
            self.assertFalse(summary["auxiliary_json"]["included_as_disclosures"])
            self.assertEqual(summary["physical_files"]["parsed_file_count"], 7)
            self.assertEqual(summary["physical_files"]["sha256_count"], 7)
            self.assertTrue((report_dir / "parser_summary.json").is_file())
            report = (report_dir / "parser_report.md").read_text(encoding="utf-8")
            self.assertIn("원문에 없는 정보를 생성", report)
            self.assertIn("물리 파일", report)


if __name__ == "__main__":
    unittest.main()
