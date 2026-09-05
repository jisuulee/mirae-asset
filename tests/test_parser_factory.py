from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from disclosure_preprocess.models import ParseStatus
from disclosure_preprocess.parsers.base import ParseContext, read_binary_stream
from disclosure_preprocess.parsers.parser_factory import (
    PARSER_UNHANDLED_EXCEPTION,
    parse_payload,
)


class ParserFactoryTests(unittest.TestCase):
    def test_unsupported_content_is_a_structured_failure(self) -> None:
        payload = read_binary_stream(io.BytesIO(b"\x00\x01\x02\x03"))
        result = parse_payload(
            payload,
            ParseContext("raw/unknown/data.bin", "unknown", role="main"),
        )
        self.assertEqual(result.parse_status, ParseStatus.FAILED)
        self.assertEqual(
            {item.code for item in result.warnings}, {"UNSUPPORTED_FORMAT"}
        )

    def test_content_detection_overrides_xml_suffix_and_context_hint(self) -> None:
        data = b"<!doctype html><html><head><title>Source</title></head><body><p>97,000</p></body></html>"
        payload = read_binary_stream(io.BytesIO(data))
        context = ParseContext(
            relative_path="raw/exchange/company/1/1.xml",
            detected_format="xml",
            role="main",
        )

        result = parse_payload(payload, context)

        self.assertEqual(result.parse_status, ParseStatus.SUCCESS)
        self.assertEqual(result.parser_name, "lxml-html")
        self.assertEqual(result.source_file.detected_format, "html")
        self.assertEqual(result.content.body_text, "97,000")

    def test_non_utf8_bytes_use_explicit_strict_fallback(self) -> None:
        source = '<?xml version="1.0" encoding="euc-kr"?><DOCUMENT><DOCUMENT-NAME>보고서</DOCUMENT-NAME><BODY><P>금액 1,234원</P></BODY></DOCUMENT>'
        data = source.encode("cp949")
        payload = read_binary_stream(io.BytesIO(data))
        context = ParseContext(
            relative_path="raw/major/company/1/1.xml",
            detected_format="unknown",
            role="main",
        )

        result = parse_payload(payload, context)

        self.assertNotEqual(result.parse_status, ParseStatus.FAILED)
        self.assertIn("금액 1,234원", result.content.body_text)
        self.assertIn("ENCODING_FALLBACK_USED", {item.code for item in result.warnings})
        self.assertIn(result.source_file.encoding, {"cp949", "euc_kr"})

    def test_unhandled_parser_exception_isolated_as_one_file_failure(self) -> None:
        data = b"<html><body><p>x</p></body></html>"
        payload = read_binary_stream(io.BytesIO(data))
        context = ParseContext("raw/x.html", "html", role="main")
        with patch(
            "disclosure_preprocess.parsers.html_parser.HtmlParser.parse",
            side_effect=RuntimeError("fixture failure"),
        ):
            result = parse_payload(payload, context)

        self.assertEqual(result.parse_status, ParseStatus.FAILED)
        self.assertEqual(
            {warning.code for warning in result.warnings},
            {PARSER_UNHANDLED_EXCEPTION},
        )


if __name__ == "__main__":
    unittest.main()
