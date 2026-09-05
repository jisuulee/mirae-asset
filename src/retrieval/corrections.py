"""Correction-aware reads using explicit, high-confidence links only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

EXPLICIT_EDGE_SQL = """
SELECT predecessor_receipt_no, successor_receipt_no
FROM correction_links
WHERE link_method = 'explicit_original_filing_date' AND confidence = 'high'
"""


def _rows(cursor: Any) -> list[dict[str, Any]]:
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def get_correction_history(connection: Any, receipt_no: str) -> list[dict[str, Any]]:
    """Return the explicit connected chain in observed filing order.

    Similarity candidates cannot enter correction_links because schema checks
    reject them. A cycle/branch raises instead of silently inventing an order.
    """
    with connection.cursor() as cursor:
        cursor.execute(EXPLICIT_EDGE_SQL)
        edges = cursor.fetchall()
    successors: dict[str, list[str]] = defaultdict(list)
    predecessors: dict[str, list[str]] = defaultdict(list)
    nodes = {receipt_no}
    for before, after in edges:
        successors[before].append(after)
        predecessors[after].append(before)
        nodes.update((before, after))
    component = {receipt_no}
    frontier = [receipt_no]
    while frontier:
        node = frontier.pop()
        for neighbor in successors[node] + predecessors[node]:
            if neighbor not in component:
                component.add(neighbor)
                frontier.append(neighbor)
    if any(len(successors[n]) > 1 or len(predecessors[n]) > 1 for n in component):
        raise ValueError("explicit correction graph is branched")
    placeholders = ",".join(["%s"] * len(component))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""SELECT d.receipt_no, d.receipt_date, d.receipt_at, d.report_name,
                       d.is_correction_name, d.is_correction_rm,
                       f.relative_path AS source_path
                FROM disclosures d
                LEFT JOIN LATERAL (
                    SELECT relative_path FROM disclosure_files
                    WHERE receipt_no=d.receipt_no ORDER BY source_order LIMIT 1
                ) f ON true
                WHERE d.receipt_no IN ({placeholders})
                ORDER BY d.receipt_date, d.receipt_at NULLS FIRST, d.receipt_no""",
            tuple(component),
        )
        result = _rows(cursor)
    if len(result) != len(component):
        raise LookupError("correction link references a missing disclosure")
    if len({row['receipt_no'] for row in result}) != len(result):
        raise ValueError("duplicate disclosure in correction chain")
    return result


def get_latest_observed(connection: Any, receipt_no: str) -> dict[str, Any] | None:
    """Return the latest filing observed in an explicit chain, not a legal final value."""
    history = get_correction_history(connection, receipt_no)
    if not history:
        return None
    latest = dict(history[-1])
    latest["observation_status"] = "latest_observed_in_current_dataset"
    latest["chain_receipt_nos"] = [row["receipt_no"] for row in history]
    return latest
