from __future__ import annotations

import hashlib
import unittest
import unicodedata

from disclosure_preprocess.models import ParseStatus, SourceFileRecord
from disclosure_preprocess.parsers.base import BinaryPayload, ParseContext
from disclosure_preprocess.parsers.html_parser import HtmlParser
from disclosure_preprocess.parsers.xml_parser import XmlParser


def _parse(data: bytes, *, format_name: str):
    path = f"raw/test/company/1/document.{ 'xml' if format_name == 'xml' else 'html'}"
    context = ParseContext(
        relative_path=path,
        original_path=path,
        detected_format=format_name,
        role="main",
        size_bytes=len(data),
        is_canonical=True,
    )
    payload = BinaryPayload(data=data, sha256=hashlib.sha256(data).hexdigest())
    source = SourceFileRecord(path, "main", format_name, size_bytes=len(data))
    parser = XmlParser() if format_name == "xml" else HtmlParser()
    return parser.parse(payload, context, source)


class XmlParserTests(unittest.TestCase):
    def test_missing_title_is_null_with_explicit_warning(self) -> None:
        result = _parse(
            b"<DOCUMENT><BODY><P>source body</P></BODY></DOCUMENT>",
            format_name="xml",
        )
        self.assertIsNone(result.content.title)
        self.assertIn("TITLE_NOT_FOUND", {item.code for item in result.warnings})

    def test_malformed_xml_is_recovered_as_partial_not_silently_successful(self) -> None:
        result = _parse(
            b"<DOCUMENT><DOCUMENT-NAME>Report</DOCUMENT-NAME><BODY><P>broken",
            format_name="xml",
        )
        self.assertEqual(result.parse_status, ParseStatus.PARTIAL)
        self.assertIn("broken", result.content.body_text)
        self.assertIn(
            "MARKUP_RECOVERY_APPLIED", {item.code for item in result.warnings}
        )

    def test_preserves_order_numbers_spans_dart_cells_and_bare_ampersand(self) -> None:
        data = """<?xml version="1.0" encoding="euc-kr"?>
        <DOCUMENT><DOCUMENT-NAME>분기보고서</DOCUMENT-NAME><BODY>
          <SECTION-1><TITLE>Ⅰ. 사업의 내용</TITLE>
            <P>Food &amp; Nutrition / 매출 1,234.50억원 △5.2% (123)</P>
            <TABLE><TR><TU ROWSPAN="2">항목</TU><TE COLSPAN="2"> 금액 001,200원 </TE></TR>
              <TR><TD>A</TD><TD>B</TD></TR></TABLE>
          </SECTION-1>
        </BODY></DOCUMENT>""".encode("utf-8")
        result = _parse(data, format_name="xml")

        self.assertEqual(result.parse_status, ParseStatus.SUCCESS)
        self.assertEqual(result.content.title, "분기보고서")
        self.assertEqual(
            [str(block.block_type) for block in result.content.block_sequence],
            ["heading", "paragraph", "table"],
        )
        self.assertEqual(result.content.sections[0].level, 1)
        self.assertIn("1,234.50억원 △5.2% (123)", result.content.body_text)
        table = result.content.tables[0]
        self.assertEqual(table.raw_rows[0][0].raw_text, "항목")
        self.assertEqual(table.raw_rows[0][0].rowspan, 2)
        self.assertFalse(table.raw_rows[0][0].is_header)
        self.assertEqual(table.raw_rows[0][1].raw_text, " 금액 001,200원 ")
        self.assertEqual(table.raw_rows[0][1].colspan, 2)
        self.assertEqual(table.normalized_rows[0], ["항목", "금액 001,200원", None])
        self.assertEqual(
            {warning.code for warning in result.warnings},
            {"ENCODING_DECLARATION_MISMATCH"},
        )

    def test_unescaped_ampersand_is_preserved_not_discarded(self) -> None:
        data = b"<DOCUMENT><DOCUMENT-NAME>Annual</DOCUMENT-NAME><BODY><P>CJ E&amp;C</P><P>Food & Nutrition Tech</P></BODY></DOCUMENT>"
        result = _parse(data, format_name="xml")

        self.assertNotEqual(result.parse_status, ParseStatus.FAILED)
        self.assertIn("CJ E&C", result.content.body_text)
        self.assertIn("Food & Nutrition Tech", result.content.body_text)
        warning = next(
            item for item in result.warnings if item.code == "BARE_AMPERSAND_PRESERVED"
        )
        self.assertEqual(warning.details["replacement_count"], 1)

    def test_cdata_ampersand_and_raw_nfd_are_not_normalized_in_place(self) -> None:
        raw_nfd = unicodedata.normalize("NFD", "매출")
        source = (
            "<DOCUMENT><DOCUMENT-NAME>Report</DOCUMENT-NAME><BODY>"
            f"<P><![CDATA[A & B / {raw_nfd} 001.20원]]></P>"
            f"<TABLE><TR><TD>{raw_nfd}</TD></TR></TABLE>"
            "</BODY></DOCUMENT>"
        ).encode("utf-8")
        result = _parse(source, format_name="xml")

        self.assertIn("A & B", result.content.body_text)
        self.assertFalse(
            any(item.code == "BARE_AMPERSAND_PRESERVED" for item in result.warnings)
        )
        raw_cell = result.content.tables[0].raw_rows[0][0].raw_text
        self.assertEqual(raw_cell, raw_nfd)
        self.assertFalse(unicodedata.is_normalized("NFC", raw_cell))
        self.assertEqual(result.content.tables[0].raw_rows[0][0].text, "매출")

    def test_block_children_in_cell_do_not_merge_into_a_new_number(self) -> None:
        data = b"""<DOCUMENT><DOCUMENT-NAME>Report</DOCUMENT-NAME><BODY>
        <TABLE><TR><TD><P>1</P><P>234</P><P>(1,234)</P><P>12.5%</P><P>-</P><P>N/A</P><P>2025.03.18</P><P>\xeb\xb0\xb1\xeb\xa7\x8c\xec\x9b\x90</P></TD></TR></TABLE>
        </BODY></DOCUMENT>"""
        result = _parse(data, format_name="xml")

        cell = result.content.tables[0].raw_rows[0][0]
        self.assertEqual(
            cell.raw_text,
            "1\n234\n(1,234)\n12.5%\n-\nN/A\n2025.03.18\n백만원",
        )
        self.assertNotIn("1234", cell.raw_text)
        self.assertEqual(cell.text, cell.raw_text)

    def test_source_metadata_keeps_raw_value_and_dom_provenance(self) -> None:
        raw_company = unicodedata.normalize("NFD", "테스트주식회사")
        data = (
            "<DOCUMENT><DOCUMENT-NAME>Report</DOCUMENT-NAME>"
            f"<COMPANY-NAME>{raw_company}</COMPANY-NAME>"
            "<BODY><P>text</P></BODY></DOCUMENT>"
        ).encode("utf-8")
        result = _parse(data, format_name="xml")

        self.assertEqual(result.source_metadata["company_name"], "테스트주식회사")
        self.assertEqual(result.source_metadata["company_name_raw"], raw_company)
        self.assertFalse(
            unicodedata.is_normalized(
                "NFC", result.source_metadata["company_name_raw"]
            )
        )
        self.assertEqual(
            result.source_metadata["company_name_provenance"]["tag"],
            "company-name",
        )

    def test_mixed_text_and_nested_table_keep_order_and_parent_link(self) -> None:
        data = b"""<DOCUMENT><DOCUMENT-NAME>Report</DOCUMENT-NAME><BODY>
        <P>before<TABLE><TR><TD>pre<TABLE><TR><TD>inner</TD></TR></TABLE>post</TD></TR></TABLE>after</P>
        </BODY></DOCUMENT>"""
        result = _parse(data, format_name="xml")

        self.assertEqual(
            [str(block.block_type) for block in result.content.block_sequence],
            ["paragraph", "table", "table", "paragraph"],
        )
        self.assertEqual(result.content.block_sequence[0].text, "before")
        self.assertEqual(result.content.block_sequence[-1].text, "after")
        outer_cell = result.content.tables[0].raw_rows[0][0]
        sequence = outer_cell.attributes["content_sequence"]
        self.assertEqual(
            [item["type"] for item in sequence],
            ["text", "nested_table", "text"],
        )
        self.assertEqual(sequence[0]["raw_text"], "pre")
        self.assertEqual(sequence[2]["raw_text"], "post")
        self.assertEqual(
            result.content.tables[1].attributes["parent_table_id"],
            result.content.tables[0].table_id,
        )

    def test_nested_list_does_not_duplicate_inner_item_in_outer_item(self) -> None:
        data = b"""<DOCUMENT><DOCUMENT-NAME>Report</DOCUMENT-NAME><BODY>
        <UL><LI>outer<UL><LI>inner</LI></UL>tail</LI><LI>second</LI></UL>
        </BODY></DOCUMENT>"""
        result = _parse(data, format_name="xml")
        list_blocks = [
            block
            for block in result.content.block_sequence
            if str(block.block_type) == "list"
        ]
        self.assertEqual(
            [block.items[0].text for block in list_blocks],
            ["outer", "inner", "tail", "second"],
        )
        self.assertEqual(
            result.content.body_text.splitlines(),
            ["outer", "inner", "tail", "second"],
        )
        self.assertTrue(list_blocks[2].attributes["is_continuation"])

    def test_nested_tables_are_separate_and_keep_dom_provenance(self) -> None:
        data = b"""<DOCUMENT><DOCUMENT-NAME>Report</DOCUMENT-NAME><BODY>
        <TABLE><TR><TD>outer<TABLE><TR><TD>inner</TD></TR></TABLE></TD></TR></TABLE>
        </BODY></DOCUMENT>"""
        result = _parse(data, format_name="xml")

        self.assertEqual(len(result.content.tables), 2)
        self.assertEqual(result.content.tables[0].raw_rows[0][0].raw_text.strip(), "outer")
        self.assertEqual(result.content.tables[1].raw_rows[0][0].raw_text, "inner")
        self.assertNotEqual(
            result.content.tables[0].provenance.dom_path,
            result.content.tables[1].provenance.dom_path,
        )


class HtmlParserTests(unittest.TestCase):
    def test_parent_text_inline_span_and_tail_are_kept_in_order(self) -> None:
        data = b"<html><head><title>T</title></head><body>prefix<div>A<span>B</span>C</div>suffix</body></html>"
        result = _parse(data, format_name="html")
        self.assertEqual(
            [block.text for block in result.content.block_sequence],
            ["prefix", "ABC", "suffix"],
        )

    def test_tolerates_malformed_html_and_excludes_hidden_content(self) -> None:
        data = """<!doctype html><html><head><meta charset="utf-8">
        <title>단일판매ㆍ공급계약체결</title><style>.x{}</style></head><body>
        <h2>계약 내용</h2><p>문장<br>둘째 줄
        <span style="display:none">비밀 숫자 999</span></p>
        <script>var fabricated = 777;</script>
        <table><tr><th colspan="2">금액</th></tr><tr><td>97,000,000,000</td><td>5.37</td></tr></table>
        </body></html>""".encode("utf-8")
        result = _parse(data, format_name="html")

        self.assertNotEqual(result.parse_status, ParseStatus.FAILED)
        self.assertEqual(result.content.title, "단일판매ㆍ공급계약체결")
        self.assertIn("문장\n둘째 줄", result.content.body_text)
        self.assertNotIn("999", result.content.body_text)
        self.assertNotIn("777", result.content.body_text)
        self.assertEqual(result.content.tables[0].raw_rows[1][0].raw_text, "97,000,000,000")
        self.assertEqual(result.content.tables[0].raw_rows[1][1].raw_text, "5.37")
        self.assertEqual(
            [str(block.block_type) for block in result.content.block_sequence],
            ["heading", "paragraph", "table"],
        )


if __name__ == "__main__":
    unittest.main()
