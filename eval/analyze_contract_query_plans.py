"""Capture representative PostgreSQL EXPLAIN ANALYZE plans for Retrieval V0."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from db.load_postgres import connect


QUERIES = {
    "company_search": ("""
        SELECT d.receipt_no FROM contracts c JOIN disclosures d USING(receipt_no)
        JOIN companies co USING(company_id)
        WHERE d.category='exchange' AND d.report_type='단일판매공급계약체결'
          AND co.company_name_normalized=%s
        ORDER BY d.receipt_date DESC,d.receipt_no DESC LIMIT 5
    """, ("대우건설",)),
    "period_filter": ("""
        SELECT d.receipt_no FROM contracts c JOIN disclosures d USING(receipt_no)
        JOIN companies co USING(company_id)
        WHERE co.company_name_normalized=%s AND d.receipt_date BETWEEN %s AND %s
        ORDER BY d.receipt_date DESC,d.receipt_no DESC LIMIT 5
    """, ("현대건설", "2024-01-01", "2024-12-31")),
    "amount_sort": ("""
        SELECT d.receipt_no,c.contract_amount FROM contracts c JOIN disclosures d USING(receipt_no)
        JOIN companies co USING(company_id) WHERE co.company_name_normalized=%s
        ORDER BY c.contract_amount DESC NULLS LAST,d.receipt_date DESC,d.receipt_no DESC LIMIT 5
    """, ("대우건설",)),
    "counterparty_search": ("""
        SELECT d.receipt_no FROM contracts c JOIN disclosures d USING(receipt_no)
        WHERE c.counterparty ILIKE '%%' || %s || '%%'
        ORDER BY d.receipt_date DESC,d.receipt_no DESC LIMIT 5
    """, ("Pfizer",)),
    "largest_latest_contract": ("""
        SELECT d.receipt_no,c.contract_amount FROM contracts c JOIN disclosures d USING(receipt_no)
        JOIN companies co USING(company_id)
        WHERE co.company_name_normalized=%s
          AND NOT EXISTS (SELECT 1 FROM correction_links cl WHERE cl.predecessor_receipt_no=d.receipt_no)
        ORDER BY c.contract_amount DESC NULLS LAST,d.receipt_date DESC,d.receipt_no DESC LIMIT 1
    """, ("대우건설",)),
    "correction_edge_scan": ("""
        SELECT predecessor_receipt_no,successor_receipt_no FROM correction_links
        WHERE link_method='explicit_original_filing_date' AND confidence='high'
    """, ()),
}


def indexes(node):
    found = []
    if node.get("Index Name"):
        found.append(node["Index Name"])
    for child in node.get("Plans", []):
        found.extend(indexes(child))
    return found


def capture(database_url):
    result = {}
    with connect(database_url) as connection:
        for name, (sql, params) in QUERIES.items():
            plan = connection.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params).fetchone()[0][0]
            result[name] = {
                "planning_time_ms": plan["Planning Time"],
                "execution_time_ms": plan["Execution Time"],
                "top_node": plan["Plan"]["Node Type"],
                "indexes_used": sorted(set(indexes(plan["Plan"]))),
                "plan": plan,
            }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "retrieval_query_plans.json")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    result = capture(args.database_url)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k:{x:v[x] for x in ("planning_time_ms","execution_time_ms","top_node","indexes_used")} for k,v in result.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
