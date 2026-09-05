"""Conservative heading hierarchy and section-boundary construction."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize_title(title: str | None) -> str | None:
    if not title:
        return None
    value = unicodedata.normalize("NFKC", title)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def infer_level(title: str | None, explicit_level: Any = None) -> int | None:
    if explicit_level is not None:
        try:
            level = int(explicit_level)
            return level if 1 <= level <= 6 else None
        except (TypeError, ValueError):
            pass
    value = normalize_title(title) or ""
    if re.match(r"^(?:[IVX]+)[.．]\s*", value, re.I) or re.match(r"^제\s*\d+\s*장", value):
        return 1
    if re.match(r"^\d+[.．]\s*", value):
        return 2
    if re.match(r"^\d+[-.]\d+[.．]?\s*", value):
        return 3
    if re.match(r"^[가-하][.．]\s*", value):
        return 3
    if re.match(r"^\([0-9가-하]+\)\s*", value):
        return 4
    return None


def _body_from_blocks(blocks: list[dict[str, Any]]) -> tuple[str, list[str]]:
    parts: list[str] = []
    table_ids: list[str] = []
    for block in blocks:
        if block["block_type"] == "paragraph" and block.get("text"):
            parts.append(block["text"])
        elif block["block_type"] == "table" and block.get("table_id"):
            table_id = block["table_id"]
            table_ids.append(table_id)
            parts.append(f"[TABLE: {table_id}]")
    return "\n\n".join(parts), table_ids


def _fallback_sections(
    blocks: list[dict[str, Any]], receipt_no: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """PDF pages become untitled retrieval sections without invented headings."""
    sections: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for block in blocks:
        if block["block_type"] == "page_break":
            if current:
                sections.append(_make_fallback_section(current, receipt_no, len(sections)))
                current = []
        else:
            current.append(block)
    if current:
        sections.append(_make_fallback_section(current, receipt_no, len(sections)))
    return sections, ["PDF_PAGE_FALLBACK_SECTION"] if sections else ["NO_SECTION_DETECTED"]


def _make_fallback_section(
    blocks: list[dict[str, Any]], receipt_no: str, order: int
) -> dict[str, Any]:
    body_text, table_ids = _body_from_blocks(blocks)
    page = (blocks[0].get("provenance") or {}).get("page_number")
    key = f"{receipt_no}:s{order:05d}"
    return {
        "section_key": key,
        "title_raw": None,
        "title_normalized": None,
        "level": None,
        "section_order": order,
        "parent_section_key": None,
        "section_path": f"page/{page or order + 1}",
        "start_block_index": None,
        "end_block_index": None,
        "block_ids": [block["block_id"] for block in blocks],
        "body_text": body_text,
        "table_ids": table_ids,
        "source_locator": f"page:{page or order + 1}",
        "provenance": blocks[0].get("provenance"),
        "warning_codes": ["PDF_PAGE_FALLBACK_SECTION"],
    }


def build_sections(
    blocks: list[dict[str, Any]], *, receipt_no: str
) -> tuple[list[dict[str, Any]], list[str]]:
    heading_indices = [i for i, block in enumerate(blocks) if block["block_type"] == "heading"]
    if not heading_indices:
        return _fallback_sections(blocks, receipt_no)

    sections: list[dict[str, Any]] = []
    stack: list[tuple[int, str, str]] = []
    warnings: list[str] = []
    for order, start in enumerate(heading_indices):
        end = heading_indices[order + 1] - 1 if order + 1 < len(heading_indices) else len(blocks) - 1
        heading = blocks[start]
        title = heading.get("text")
        level = infer_level(title, (heading.get("attributes") or {}).get("tag_level"))
        key = f"{receipt_no}:s{order:05d}"
        parent_key: str | None = None
        path_titles: list[str] = []
        section_warnings: list[str] = []
        if level is None:
            warnings.append("SECTION_HIERARCHY_UNCERTAIN")
            section_warnings.append("SECTION_HIERARCHY_UNCERTAIN")
        else:
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                parent_key = stack[-1][1]
                path_titles = [entry[2] for entry in stack]
            stack.append((level, key, normalize_title(title) or ""))
        body_text, table_ids = _body_from_blocks(blocks[start + 1 : end + 1])
        section = {
            "section_key": key,
            "title_raw": title,
            "title_normalized": normalize_title(title),
            "level": level,
            "section_order": order,
            "parent_section_key": parent_key,
            "section_path": " / ".join(path_titles + [normalize_title(title) or ""]),
            "start_block_index": start,
            "end_block_index": end,
            "block_ids": [block["block_id"] for block in blocks[start : end + 1]],
            "body_text": body_text,
            "table_ids": table_ids,
            "source_locator": heading.get("source_locator"),
            "provenance": heading.get("provenance"),
            "warning_codes": section_warnings,
        }
        sections.append(section)

    section_by_table = {
        table_id: section["section_key"]
        for section in sections
        for table_id in section["table_ids"]
    }
    for block in blocks[: heading_indices[0]]:
        if block.get("table_id"):
            warnings.append("ORPHAN_TABLE_BEFORE_FIRST_HEADING")
    return sections, sorted(set(warnings))


def attach_table_sections(
    tables: list[dict[str, Any]], sections: list[dict[str, Any]]
) -> None:
    mapping = {
        table_id: section["section_key"]
        for section in sections
        for table_id in section.get("table_ids", [])
    }
    for table in tables:
        table["section_key"] = mapping.get(table["table_id"])
