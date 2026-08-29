"""Bounded PostgreSQL search projection for lossless table archive rows."""

from __future__ import annotations

import re
from typing import Any

SEARCHABLE_TEXT_LIMIT = 12_000


def _span_width(value: Any) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def project_table(
    table: dict[str, Any],
    *,
    archive_path: str,
    archive_line: int,
    max_chars: int = SEARCHABLE_TEXT_LIMIT,
) -> dict[str, Any]:
    rows = table.get("rows") or []
    cells = [cell for row in rows for cell in row]
    has_rowspan = any(cell.get("rowspan") not in (None, "", "1", 1) for cell in cells)
    has_colspan = any(cell.get("colspan") not in (None, "", "1", 1) for cell in cells)
    column_count = max(
        (sum(_span_width(cell.get("colspan")) for cell in row) for row in rows),
        default=0,
    )

    # Caption and headers are always first. Remaining cells preserve source order
    # until the fixed PostgreSQL projection budget is exhausted.
    candidates: list[str] = []
    if table.get("caption"):
        candidates.append(str(table["caption"]))
    candidates.extend(str(cell.get("text") or "") for cell in cells if cell.get("is_header"))
    candidates.extend(str(cell.get("text") or "") for cell in cells)
    parts: list[str] = []
    seen: set[str] = set()
    used = 0
    for value in candidates:
        value = re.sub(r"\s+", " ", value).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        remaining = max_chars - used - (1 if parts else 0)
        if remaining <= 0:
            break
        parts.append(value[:remaining])
        used += len(parts[-1]) + (1 if len(parts) > 1 else 0)
    searchable_text = "\n".join(parts)[:max_chars]

    try:
        table_order = int(str(table["table_id"]).rsplit("t", 1)[1])
    except (IndexError, TypeError, ValueError):
        table_order = int(table.get("table_order") or 0)
    return {
        "table_id": table["table_id"],
        "receipt_no": table["receipt_no"],
        "section_key": table.get("section_key"),
        "table_order": table_order,
        "caption": table.get("caption"),
        "searchable_text": searchable_text,
        "row_count": len(rows),
        "column_count": column_count,
        "has_rowspan": has_rowspan,
        "has_colspan": has_colspan,
        "source_file": table["source_file"],
        "source_locator": table["source_locator"],
        "provenance": table.get("provenance") or {},
        "archive_path": archive_path,
        "archive_identifier": f"line:{archive_line};table_id:{table['table_id']}",
        "metadata": {
            "projection_version": "periodic-retrieval-table-v1",
            "searchable_text_truncated": len(table.get("searchable_text") or "") > max_chars,
            "lossless_payload_in_postgres": False,
        },
    }
