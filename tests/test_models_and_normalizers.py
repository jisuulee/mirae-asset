from __future__ import annotations

import json
import unittest

from disclosure_preprocess.models import (
    BlockType,
    ContentBlock,
    DisclosureDocument,
    DocumentContent,
    ListItem,
    ParseResult,
    ParseStatus,
    Provenance,
    Section,
    SourceFileRecord,
    Table,
    TableCell,
)
from disclosure_preprocess.normalizers import (
    TABLE_SPAN_NORMALIZATION_FAILED,
    expand_table_grid,
    normalize_table,
    normalize_text,
)


class ModelTests(unittest.TestCase):
    def test_ordered_blocks_nullable_section_and_body_derivation(self) -> None:
        provenance = Provenance(
            source_file="raw/major/company/1/1.xml",
            file_role="main",
            source_index=4,
            dom_path="/DOCUMENT/BODY/P[2]",
        )
        blocks = [
            ContentBlock(
                BlockType.HEADING,
                block_id="b0",
                raw_text="제1장",
                text="제1장",
                level=1,
                provenance=provenance,
            ),
            ContentBlock(
                BlockType.TABLE, block_id="b1", table_id="t0", provenance=provenance
            ),
            ContentBlock(
                BlockType.LIST,
                block_id="b2",
                items=[ListItem(raw_text="  원문  ", text="원문")],
            ),
            ContentBlock(
                BlockType.PARAGRAPH,
                block_id="b3",
                raw_text="금액 1,234.00억원",
            ),
        ]
        content = DocumentContent(
            title=None,
            sections=[Section(section_id="s0", title=None, level=None)],
            block_sequence=blocks,
        )

        self.assertEqual(content.derive_body_text(), "제1장\n원문\n금액 1,234.00억원")
        self.assertIsNone(content.sections[0].title)
        self.assertIsNone(content.sections[0].level)
        self.assertEqual(
            [str(block.block_type) for block in content.block_sequence],
            ["heading", "table", "list", "paragraph"],
        )

    def test_full_ir_is_json_serializable_and_keeps_provenance(self) -> None:
        source = SourceFileRecord(
            relative_path="raw/holding/회사/1/1.xml",
            role="main",
            detected_format="xml",
            size_bytes=42,
            sha256="a" * 64,
            encoding="utf-8",
            is_canonical=True,
        )
        provenance = Provenance(
            source_file=source.relative_path, source_index=7, tag="P"
        )
        result = ParseResult(
            source_file=source,
            parse_status=ParseStatus.SUCCESS,
            content=DocumentContent(
                title="보고서",
                block_sequence=[
                    ContentBlock(
                        BlockType.PARAGRAPH,
                        raw_text="근거 문장",
                        text="근거 문장",
                        provenance=provenance,
                    )
                ],
            ),
        )
        disclosure = DisclosureDocument(
            doc_id="holding_1",
            rcept_no="1",
            metadata={"corp_name": "회사"},
            main_document=result,
            source_files=[source],
        )
        disclosure.refresh_parse_status()

        serialized = disclosure.to_dict()
        json.dumps(serialized, ensure_ascii=False)
        self.assertEqual(serialized["parse_status"], "success")
        self.assertEqual(
            serialized["main_document"]["content"]["block_sequence"][0][
                "provenance"
            ]["source_index"],
            7,
        )
        self.assertEqual(serialized["source_files"][0]["sha256"], "a" * 64)
        self.assertEqual(serialized["source_files"][0]["encoding"], "utf-8")

    def test_mixed_physical_results_make_logical_result_partial(self) -> None:
        successful = ParseResult(
            SourceFileRecord("a.xml", "main", "xml"), ParseStatus.SUCCESS
        )
        failed = ParseResult(
            SourceFileRecord("b.xml", "attachment", "xml"), ParseStatus.FAILED
        )
        disclosure = DisclosureDocument(
            "periodic_1",
            "1",
            main_document=successful,
            attachments=[failed],
        )
        self.assertEqual(disclosure.refresh_parse_status(), ParseStatus.PARTIAL)


class TextNormalizerTests(unittest.TestCase):
    def test_only_unicode_whitespace_and_line_breaks_change(self) -> None:
        decomposed = "매출\t  1,234.00억원\r\n 전년대비 +3.5% "
        self.assertEqual(
            normalize_text(decomposed),
            "매출 1,234.00억원\n전년대비 +3.5%",
        )

    def test_preserves_blank_lines_and_exact_tokens(self) -> None:
        source = "A  001.230kg\n\n\nB -12.0% / (1,234) / 12.5% / - / N/A / 2025.03.18 / 백만원"
        normalized = normalize_text(source)
        self.assertEqual(
            normalized,
            "A 001.230kg\n\n\nB -12.0% / (1,234) / 12.5% / - / N/A / 2025.03.18 / 백만원",
        )
        self.assertIn("001.230kg", normalized)
        for token in (
            "-12.0%",
            "(1,234)",
            "12.5%",
            "-",
            "N/A",
            "2025.03.18",
            "백만원",
        ):
            self.assertIn(token, normalized)


class TableNormalizerTests(unittest.TestCase):
    def test_rowspan_colspan_grid_retains_origin_and_raw_text(self) -> None:
        source = " 금액 001,200원 "
        provenance = Provenance(
            "raw/exchange/company/1/1.xml", source_index=10, dom_path="/table/tr[1]/td[1]"
        )
        table = Table(
            table_id="t0",
            raw_rows=[
                [
                    TableCell(
                        source,
                        rowspan=2,
                        source_row_index=0,
                        source_cell_index=0,
                        provenance=provenance,
                    ),
                    TableCell("B", colspan=2, source_row_index=0, source_cell_index=1),
                ],
                [
                    TableCell("C", source_row_index=1, source_cell_index=0),
                    TableCell("D", source_row_index=1, source_cell_index=1),
                ],
            ],
        )

        result = normalize_table(table)
        self.assertTrue(result.success)
        self.assertEqual(len(result.grid), 2)
        self.assertEqual(len(result.grid[0]), 3)
        self.assertEqual(result.grid[0][0].raw_text, source)
        self.assertEqual(result.grid[0][0].text, "금액 001,200원")
        self.assertFalse(result.grid[0][0].is_span_continuation)
        self.assertTrue(result.grid[1][0].is_span_continuation)
        self.assertEqual(result.grid[1][0].origin_row, 0)
        self.assertEqual(result.grid[1][0].provenance.source_index, 10)
        self.assertEqual(
            result.to_text_rows(),
            [["금액 001,200원", "B", None], [None, "C", "D"]],
        )
        self.assertEqual(
            result.to_text_rows(repeat_spans=True),
            [["금액 001,200원", "B", "B"], ["금액 001,200원", "C", "D"]],
        )
        self.assertEqual(table.raw_rows[0][0].raw_text, source)
        self.assertIsNone(table.normalized_rows)

    def test_invalid_span_returns_structured_warning_and_recovers(self) -> None:
        result = expand_table_grid([[TableCell("A", rowspan=0, colspan=-2)]])
        self.assertFalse(result.success)
        self.assertEqual(len(result.grid), 1)
        self.assertEqual(result.grid[0][0].raw_text, "A")
        self.assertEqual(
            {warning.code for warning in result.warnings},
            {TABLE_SPAN_NORMALIZATION_FAILED},
        )
        self.assertEqual(
            {warning.details["span_name"] for warning in result.warnings},
            {"rowspan", "colspan"},
        )

    def test_rowspan_past_source_rows_is_reported(self) -> None:
        result = expand_table_grid([[TableCell("A", rowspan=2)]])
        self.assertFalse(result.success)
        self.assertEqual(len(result.grid), 2)
        self.assertEqual(
            result.warnings[-1].code, TABLE_SPAN_NORMALIZATION_FAILED
        )

    def test_absurd_span_is_bounded_and_reported(self) -> None:
        result = expand_table_grid([[TableCell("A", rowspan=10**12)]])
        self.assertFalse(result.success)
        self.assertEqual(len(result.grid), 1)
        self.assertEqual(result.grid[0][0].raw_text, "A")
        self.assertEqual(result.warnings[0].details["maximum_span"], 1_000)

    def test_table_wide_grid_limit_keeps_raw_rows_without_expansion(self) -> None:
        result = expand_table_grid(
            [[TableCell("A", rowspan=3, colspan=3)]],
            max_total_slots=4,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.grid, [])
        self.assertEqual(result.warnings[-1].code, TABLE_SPAN_NORMALIZATION_FAILED)
        self.assertEqual(result.warnings[-1].details["maximum_total_slots"], 4)


if __name__ == "__main__":
    unittest.main()
