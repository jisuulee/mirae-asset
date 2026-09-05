from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from disclosure_preprocess.models import BlockType, ParseStatus
from disclosure_preprocess.parsers.base import (
    ParseContext,
    make_source_file_record,
    read_binary_stream,
)
from disclosure_preprocess.parsers.json_parser import (
    JSON_PARSE_FAILED,
    JSON_ROOT_TYPE_UNSUPPORTED,
    JsonParser,
)
from disclosure_preprocess.parsers.pdf_parser import (
    EMPTY_BODY,
    PDF_ENCRYPTED,
    PDF_PAGE_EXTRACTION_FAILED,
    PDF_PARSE_FAILED,
    PdfParser,
)


def _parse_bytes(parser: object, data: bytes, detected_format: str):
    context = ParseContext(
        relative_path=f"raw/fixture/source.{detected_format}",
        detected_format=detected_format,
        role="main",
        is_canonical=True,
    )
    payload = read_binary_stream(io.BytesIO(data), chunk_size=7)
    source_file = make_source_file_record(context, payload)
    return parser.parse(payload, context, source_file)  # type: ignore[attr-defined]


def _pdf_bytes(page_texts: list[str], *, title: str | None = None) -> bytes:
    writer = PdfWriter()
    for page_text in page_texts:
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
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        escaped = page_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        stream.set_data(
            f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    if title is not None:
        writer.add_metadata({"/Title": title})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class JsonParserTests(unittest.TestCase):
    def test_auxiliary_list_is_validated_without_disclosure_content(self) -> None:
        records = [
            {"corp_code": "00123456", "rcept_no": "20250101000001"},
            {"corp_code": "00999999", "rcept_no": "20250102000002"},
        ]
        result = _parse_bytes(
            JsonParser(), json.dumps(records, ensure_ascii=False).encode(), "json"
        )

        self.assertEqual(result.parse_status, ParseStatus.SUCCESS)
        self.assertIsNone(result.content)
        self.assertTrue(result.source_metadata["auxiliary_only"])
        self.assertEqual(result.source_metadata["root_type"], "array")
        self.assertEqual(result.source_metadata["record_count"], 2)
        hints = result.source_metadata["schema_hints"]
        self.assertEqual(hints["record_container"], "$")
        self.assertEqual(
            hints["record_field_intersection"], ["corp_code", "rcept_no"]
        )
        # Values and complete records are intentionally not copied into IR.
        self.assertNotIn("00123456", json.dumps(result.source_metadata))

    def test_object_wrapper_uses_explicit_list_container(self) -> None:
        result = _parse_bytes(
            JsonParser(), b'{"status":"ok","list":[{"id":"001"}]}', "json"
        )
        self.assertEqual(result.parse_status, ParseStatus.SUCCESS)
        self.assertEqual(result.source_metadata["record_count"], 1)
        self.assertEqual(
            result.source_metadata["schema_hints"]["record_container"], "$.list"
        )

    def test_invalid_json_and_scalar_root_fail_independently(self) -> None:
        malformed = _parse_bytes(JsonParser(), b"[{", "json")
        scalar = _parse_bytes(JsonParser(), b"42", "json")
        self.assertEqual(malformed.parse_status, ParseStatus.FAILED)
        self.assertIn(JSON_PARSE_FAILED, {item.code for item in malformed.warnings})
        self.assertEqual(scalar.parse_status, ParseStatus.FAILED)
        self.assertIn(
            JSON_ROOT_TYPE_UNSUPPORTED, {item.code for item in scalar.warnings}
        )


class PdfParserTests(unittest.TestCase):
    def test_page_text_boundaries_metadata_and_provenance_are_preserved(self) -> None:
        result = _parse_bytes(
            PdfParser(),
            _pdf_bytes(
                ["Revenue 001,234.00 KRW", "Change -3.5 percent"],
                title="Source PDF Title",
            ),
            "pdf",
        )

        self.assertEqual(result.parse_status, ParseStatus.SUCCESS)
        self.assertEqual(result.content.title, "Source PDF Title")
        self.assertEqual(
            [str(block.block_type) for block in result.content.block_sequence],
            ["paragraph", "page_break", "paragraph"],
        )
        first, page_break, second = result.content.block_sequence
        self.assertIn("001,234.00", first.raw_text)
        self.assertIn("-3.5", second.raw_text)
        self.assertEqual(first.provenance.page_number, 1)
        self.assertEqual(second.provenance.page_number, 2)
        self.assertEqual(page_break.block_type, BlockType.PAGE_BREAK)
        self.assertEqual(result.source_metadata["page_count"], 2)
        self.assertEqual(
            result.source_metadata["pdf_metadata"]["/Title"], "Source PDF Title"
        )
        self.assertFalse(result.source_metadata["table_structure_preserved"])
        self.assertFalse(result.source_metadata["ocr_used"])

    def test_blank_pdf_is_partial_and_does_not_claim_ocr_text(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        output = io.BytesIO()
        writer.write(output)
        result = _parse_bytes(PdfParser(), output.getvalue(), "pdf")

        self.assertEqual(result.parse_status, ParseStatus.PARTIAL)
        self.assertEqual(result.content.body_text, "")
        self.assertIn(EMPTY_BODY, {item.code for item in result.warnings})
        self.assertFalse(result.content.attributes["ocr_used"])

    def test_corrupt_and_encrypted_pdfs_are_structured_failures(self) -> None:
        corrupt = _parse_bytes(PdfParser(), b"%PDF-1.7\nnot-a-real-pdf", "pdf")
        self.assertEqual(corrupt.parse_status, ParseStatus.FAILED)
        self.assertIn(PDF_PARSE_FAILED, {item.code for item in corrupt.warnings})

        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("secret")
        output = io.BytesIO()
        writer.write(output)
        encrypted = _parse_bytes(PdfParser(), output.getvalue(), "pdf")
        self.assertEqual(encrypted.parse_status, ParseStatus.FAILED)
        self.assertIn(PDF_ENCRYPTED, {item.code for item in encrypted.warnings})

    def test_one_page_failure_does_not_hide_other_page_text(self) -> None:
        class GoodPage:
            def extract_text(self) -> str:
                return "source sentence 100.00"

        class BadPage:
            def extract_text(self) -> str:
                raise ValueError("fixture page failure")

        class FakeReader:
            is_encrypted = False
            pages = [GoodPage(), BadPage()]
            metadata = {"/Title": "Fixture"}

            def __init__(self, *_args, **_kwargs) -> None:
                pass

        with patch(
            "disclosure_preprocess.parsers.pdf_parser.PdfReader", FakeReader
        ):
            result = _parse_bytes(PdfParser(), b"%PDF fake", "pdf")

        self.assertEqual(result.parse_status, ParseStatus.PARTIAL)
        self.assertIn("100.00", result.content.body_text)
        page_warnings = [
            warning
            for warning in result.warnings
            if warning.code == PDF_PAGE_EXTRACTION_FAILED
        ]
        self.assertEqual(len(page_warnings), 1)
        self.assertEqual(page_warnings[0].provenance.page_number, 2)


if __name__ == "__main__":
    unittest.main()
