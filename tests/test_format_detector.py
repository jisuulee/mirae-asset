from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from disclosure_preprocess.format_detector import (
    detect_declared_charset,
    detect_format,
    detect_format_from_bytes,
    inspect_encoding,
    is_utf8_decodable,
)


class FormatDetectorTests(unittest.TestCase):
    def test_content_first_detection(self) -> None:
        cases: list[tuple[bytes, str | None, str]] = [
            (b'<?xml version="1.0"?><DOCUMENT><A/></DOCUMENT>', ".xml", "xml"),
            (b"<DOCUMENT><DOCUMENT-NAME>main</DOCUMENT-NAME></DOCUMENT>", None, "xml"),
            (b"<DOCUMENT><BODY><html>embedded</html></BODY></DOCUMENT>", ".xml", "xml"),
            (b"<!doctype html><html><body>x</body></html>", ".xml", "html"),
            (b'<?xml version="1.0"?><html><body>x</body></html>', ".xml", "html"),
            (b'{"doc_id": "0001"}', ".txt", "json"),
            (b"%PDF-1.7\n%test", ".bin", "pdf"),
            (b"company,year\nA,2025\nB,2024\n", None, "csv"),
            (b"# Inventory\n\n- XML\n- HTML\n", None, "markdown"),
            (b"[OpenAI](https://openai.com)", None, "markdown"),
            (b"plain, undelimited prose", ".bin", "unknown"),
            (b"not really xml", ".xml", "unknown"),
        ]
        for content, extension, expected in cases:
            with self.subTest(extension=extension, expected=expected):
                self.assertEqual(
                    detect_format_from_bytes(content, extension=extension), expected
                )

    def test_detect_format_ignores_misleading_xml_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            disclosure = Path(directory) / "exchange.xml"
            disclosure.write_bytes(
                b'<html><head><meta charset="euc-kr"></head><body>notice</body></html>'
            )
            self.assertEqual(detect_format(disclosure), "html")

    def test_xlsx_requires_a_zip_container(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workbook = Path(directory) / "sample.xlsx"
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr(
                    "[Content_Types].xml",
                    "<Types><Override ContentType="
                    '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"'
                    "/></Types>",
                )
                archive.writestr("xl/workbook.xml", "<workbook/>")
            self.assertEqual(detect_format(workbook), "xlsx")
            self.assertEqual(
                detect_format_from_bytes(b"not a zip", extension=".xlsx"), "unknown"
            )
            ordinary_zip = Path(directory) / "renamed.xlsx"
            with zipfile.ZipFile(ordinary_zip, "w") as archive:
                archive.writestr("notes.txt", "not a workbook")
            self.assertEqual(detect_format(ordinary_zip), "unknown")

    def test_declared_charset(self) -> None:
        cases: list[tuple[bytes, str | None]] = [
            (b'<?xml version="1.0" encoding="UTF-8"?><root/>', "utf-8"),
            (b"<html><meta charset='EUC-KR'></html>", "euc-kr"),
            (
                b'<meta http-equiv="Content-Type" content="text/html; charset=cp949">',
                "cp949",
            ),
            (b"<html><body>none</body></html>", None),
        ]
        for content, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(detect_declared_charset(content), expected)

    def test_utf8_validation_streams_across_multibyte_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "valid.html"
            valid.write_bytes('<meta charset="euc-kr">한글'.encode())
            invalid = Path(directory) / "invalid.html"
            invalid.write_bytes(b'<meta charset="utf-8">\xff')

            self.assertTrue(is_utf8_decodable(valid, chunk_size=2))
            self.assertFalse(is_utf8_decodable(invalid, chunk_size=2))
            result = inspect_encoding(valid, chunk_size=2)
            self.assertEqual(result.declared_charset, "euc-kr")
            self.assertTrue(result.utf8_decodable)
            self.assertTrue(result.declaration_mismatch)

    def test_incomplete_utf8_sequence_is_rejected(self) -> None:
        self.assertFalse(is_utf8_decodable(b"ok\xe2\x82"))


if __name__ == "__main__":
    unittest.main()
