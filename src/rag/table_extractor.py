"""Loss-conscious extraction of DART XML/HTML tables."""

from __future__ import annotations

import re
from typing import Any

from lxml import etree


def local_name(element: etree._Element) -> str:
    """Return a stable tag name even for recoverable non-XML names."""
    try:
        return etree.QName(element).localname.upper()
    except (TypeError, ValueError):
        tag = element.tag if isinstance(element.tag, str) else ""
        if "}" in tag:
            tag = tag.rsplit("}", 1)[-1]
        return tag.rsplit(":", 1)[-1].upper()


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def element_text(element: etree._Element) -> str:
    return clean_text("".join(element.itertext()))


def dom_path(element: etree._Element) -> str:
    try:
        return element.getroottree().getpath(element)
    except (AttributeError, ValueError):
        return "/unknown"


def make_provenance(
    element: etree._Element,
    source_file: str,
    *,
    file_role: str = "main",
    source_index: int | None = None,
    page_number: int | None = None,
) -> dict[str, Any]:
    return {
        "source_file": source_file,
        "file_role": file_role,
        "source_index": source_index,
        "dom_path": dom_path(element),
        "tag": local_name(element).lower(),
        "source_line": element.sourceline,
        "page_number": page_number,
        "attributes": dict(element.attrib),
    }


def _is_nested_in_other_table(element: etree._Element, table: etree._Element) -> bool:
    parent = element.getparent()
    while parent is not None and parent is not table:
        if local_name(parent) == "TABLE":
            return True
        parent = parent.getparent()
    return False


def extract_table(
    table: etree._Element,
    *,
    table_id: str,
    receipt_no: str,
    source_file: str,
    source_index: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Extract cells while retaining span attributes and source locations."""
    warnings: list[str] = []
    rows: list[list[dict[str, Any]]] = []
    row_nodes = [
        node
        for node in table.iter()
        if local_name(node) in {"TR", "ROW"} and not _is_nested_in_other_table(node, table)
    ]
    for row_index, row in enumerate(row_nodes):
        cells: list[dict[str, Any]] = []
        for cell in row.iterchildren():
            if local_name(cell) not in {"TH", "TD", "ENTRY"}:
                continue
            attrs = {str(k).lower(): str(v) for k, v in cell.attrib.items()}
            rowspan = attrs.get("rowspan") or attrs.get("morerows")
            colspan = attrs.get("colspan") or attrs.get("namest")
            if rowspan or colspan:
                warnings.append("TABLE_SPAN_PRESERVED")
            cells.append(
                {
                    "row_index": row_index,
                    "cell_index": len(cells),
                    "text": element_text(cell),
                    "raw_text": "".join(cell.itertext()),
                    "is_header": local_name(cell) == "TH",
                    "rowspan": rowspan,
                    "colspan": colspan,
                    "attributes": dict(cell.attrib),
                    "provenance": make_provenance(
                        cell, source_file, source_index=source_index
                    ),
                }
            )
        if cells:
            rows.append(cells)

    caption_node = next(
        (node for node in table.iter() if local_name(node) in {"CAPTION", "TABLE-CAPTION"}),
        None,
    )
    caption = element_text(caption_node) if caption_node is not None else None
    searchable_text = "\n".join(
        " | ".join(cell["text"] for cell in row if cell["text"]) for row in rows
    ).strip()
    result = {
        "table_id": table_id,
        "receipt_no": receipt_no,
        "source_file": source_file,
        "source_locator": dom_path(table),
        "caption": caption,
        "rows": rows,
        "searchable_text": searchable_text,
        "section_key": None,
        "attributes": dict(table.attrib),
        "provenance": make_provenance(table, source_file, source_index=source_index),
    }
    return result, sorted(set(warnings))
