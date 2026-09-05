"""Ordered block parser for DART XML and locally captured HTML."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from lxml import etree, html

from .table_extractor import clean_text, dom_path, element_text, extract_table, local_name, make_provenance

HEADING_TAGS = {"TITLE", "COVER-TITLE", "SECTION-TITLE", "SUBTITLE"}
PARAGRAPH_TAGS = {"P", "PARAGRAPH", "TEXT", "LI", "DD", "DT"}
PAGE_BREAK_TAGS = {"PGBRK", "PAGEBREAK", "PAGE-BREAK", "HR"}
IGNORED_ANCESTORS = {"TABLE", "SCRIPT", "STYLE", "HEAD", "SELECT", "OPTION"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_ancestor(element: etree._Element, names: set[str]) -> bool:
    parent = element.getparent()
    while parent is not None:
        if isinstance(parent.tag, str) and local_name(parent) in names:
            return True
        parent = parent.getparent()
    return False


def _narrative_text(element: etree._Element) -> str:
    """Read inline text without swallowing nested structural blocks or tables."""
    parts: list[str] = [element.text or ""]
    structural = HEADING_TAGS | PAGE_BREAK_TAGS | {"TABLE", "P", "PARAGRAPH"}
    for child in element:
        if isinstance(child.tag, str) and local_name(child) not in structural:
            parts.append(_narrative_text(child))
        if child.tail:
            parts.append(child.tail)
    return clean_text("".join(parts))


def _parse_tree(path: Path) -> tuple[etree._ElementTree, list[str]]:
    warnings: list[str] = []
    if path.suffix.lower() in {".html", ".htm"}:
        parser = html.HTMLParser(recover=True, remove_comments=False)
        tree = html.parse(str(path), parser)
    else:
        parser = etree.XMLParser(
            recover=True,
            huge_tree=True,
            remove_comments=False,
            resolve_entities=False,
            no_network=True,
        )
        tree = etree.parse(str(path), parser)
    if parser.error_log:
        warnings.append("MARKUP_RECOVERY_APPLIED")
    return tree, warnings


def parse_markup(
    path: Path,
    *,
    receipt_no: str,
    source_file: str,
    source_index: int | None = None,
) -> dict[str, Any]:
    tree, warnings = _parse_tree(path)
    blocks: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []

    for element in tree.iter():
        if not isinstance(element.tag, str):
            continue
        tag = local_name(element)
        # Every TABLE is a first-class source block, including nested tables.
        # Its text descendants are skipped to avoid narrative duplication.
        if tag != "TABLE" and _has_ancestor(element, IGNORED_ANCESTORS):
            continue
        block_type: str | None = None
        text = ""
        table_id: str | None = None
        extra: dict[str, Any] = {}
        if tag in HEADING_TAGS:
            text = element_text(element)
            if text:
                block_type = "heading"
                extra["tag_level"] = element.attrib.get("LEVEL") or element.attrib.get("level")
        elif tag == "TABLE":
            table_id = f"{receipt_no}:t{len(tables):06d}"
            table, table_warnings = extract_table(
                element,
                table_id=table_id,
                receipt_no=receipt_no,
                source_file=source_file,
                source_index=source_index,
            )
            tables.append(table)
            warnings.extend(table_warnings)
            block_type = "table"
            text = table.get("caption") or ""
        elif tag in PAGE_BREAK_TAGS:
            block_type = "page_break"
        elif tag in PARAGRAPH_TAGS:
            text = _narrative_text(element)
            if text:
                block_type = "paragraph"
        if block_type is None:
            continue
        provenance = make_provenance(element, source_file, source_index=source_index)
        block = {
            "block_id": f"{receipt_no}:b{len(blocks):07d}",
            "block_type": block_type,
            "text": clean_text(text),
            "source_file": source_file,
            "source_locator": dom_path(element),
            "source_line": element.sourceline,
            "provenance": provenance,
            "table_id": table_id,
            "attributes": extra,
        }
        blocks.append(block)

    return {
        "blocks": blocks,
        "tables": tables,
        "source_sha256": sha256_file(path),
        "parse_status": "partial" if warnings else "success",
        "warning_codes": sorted(set(warnings)),
    }
