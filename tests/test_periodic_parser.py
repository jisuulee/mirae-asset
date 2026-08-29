from __future__ import annotations

import hashlib
from pathlib import Path

from src.rag.periodic_parser import PeriodicParser, join_metadata
from src.rag.section_builder import build_sections
from src.rag.xml_parser import parse_markup


SAMPLE_XML = b"""<?xml version='1.0' encoding='utf-8'?>
<DOCUMENT>
  <TITLE>I. Company</TITLE>
  <P>First paragraph.</P>
  <TITLE>1. Business</TITLE>
  <P>Before table.</P>
  <TABLE><TBODY><TR><TH rowspan='2'>Head</TH><TD>A</TD></TR><TR><TD>B</TD></TR></TBODY></TABLE>
  <P>After table.</P>
  <PGBRK/>
  <TITLE>2. Risk</TITLE><P>Risk text.</P>
</DOCUMENT>"""


def write_xml(tmp_path: Path, content: bytes = SAMPLE_XML) -> Path:
    path = tmp_path / "20250101000001.xml"
    path.write_bytes(content)
    return path


def metadata() -> dict[str, str]:
    return {
        "rcept_no": "20250101000001",
        "corp_code": "00000001",
        "corp_name": "Test Corp",
        "rcept_date": "2025-01-01",
        "report_nm": "Annual Report",
    }


def test_receipt_metadata_join():
    row = {"rcept_no": "1", "metadata": {**metadata(), "doc_group": "periodic"}}
    joined = join_metadata(row)
    assert joined["rcept_no"] == "20250101000001"
    assert joined["corp_name"] == "Test Corp"
    assert joined["report_nm"] == "Annual Report"


def test_block_ordering_and_section_boundaries(tmp_path: Path):
    parsed = parse_markup(write_xml(tmp_path), receipt_no=metadata()["rcept_no"], source_file="sample.xml")
    assert [b["block_type"] for b in parsed["blocks"]] == [
        "heading", "paragraph", "heading", "paragraph", "table", "paragraph", "page_break", "heading", "paragraph"
    ]
    sections, _ = build_sections(parsed["blocks"], receipt_no=metadata()["rcept_no"])
    assert [(s["start_block_index"], s["end_block_index"]) for s in sections] == [(0, 1), (2, 6), (7, 8)]


def test_parent_hierarchy_and_path(tmp_path: Path):
    document = PeriodicParser().parse(write_xml(tmp_path), metadata=metadata(), source_file="sample.xml")
    root, business, risk = document["sections"]
    assert root["level"] == 1
    assert business["level"] == risk["level"] == 2
    assert business["parent_section_key"] == root["section_key"]
    assert business["section_path"] == "I. Company / 1. Business"


def test_body_reconstruction_and_table_reference(tmp_path: Path):
    document = PeriodicParser().parse(write_xml(tmp_path), metadata=metadata(), source_file="sample.xml")
    business = document["sections"][1]
    table = document["tables"][0]
    assert "Before table." in business["body_text"]
    assert f"[TABLE: {table['table_id']}]" in business["body_text"]
    assert "Head | A" not in business["body_text"]
    assert table["table_id"] in business["table_ids"]
    assert table["section_key"] == business["section_key"]
    assert table["rows"][0][0]["rowspan"] == "2"
    assert "TABLE_SPAN_PRESERVED" in document["warning_codes"]


def test_sha_and_provenance(tmp_path: Path):
    path = write_xml(tmp_path)
    document = PeriodicParser().parse(path, metadata=metadata(), source_file="sample.xml")
    assert document["source_sha256"] == hashlib.sha256(SAMPLE_XML).hexdigest()
    assert all(block["provenance"]["source_file"] == "sample.xml" for block in document["blocks"])
    assert all(block["source_line"] for block in document["blocks"])


def test_malformed_xml_is_recovered(tmp_path: Path):
    malformed = b"<DOCUMENT><TITLE>I. Broken</TITLE><P>recover me<TABLE><TR><TD>x</DOCUMENT>"
    parsed = parse_markup(write_xml(tmp_path, malformed), receipt_no=metadata()["rcept_no"], source_file="broken.xml")
    assert parsed["blocks"]
    assert parsed["parse_status"] == "partial"
    assert "MARKUP_RECOVERY_APPLIED" in parsed["warning_codes"]
