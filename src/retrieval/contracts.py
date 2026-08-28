"""PostgreSQL contract Retrieval V0 query interface."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
import re


@dataclass(frozen=True)
class ContractSearch:
    company: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    counterparty: str | None = None
    min_amount: int | None = None
    max_amount: int | None = None
    contract_start_from: date | None = None
    contract_start_to: date | None = None
    active_on: date | None = None
    min_sales_ratio: float | None = None
    max_sales_ratio: float | None = None
    latest_observed_only: bool = False
    order_by: Literal["date_desc", "amount_desc", "sales_ratio_desc"] = "date_desc"
    limit: int = 20
    offset: int = 0


def normalize_company_name(value: str) -> str:
    """Minimal V1 normalization; alias resolution remains a separate concern."""
    return re.sub(r"\s+", "", value).casefold()


def _dict_rows(cursor: Any) -> list[dict[str, Any]]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def search_contracts(connection: Any, query: ContractSearch) -> list[dict[str, Any]]:
    """Search exchange single-sale/supply contracts with bound parameters."""
    if not 1 <= query.limit <= 100 or query.offset < 0:
        raise ValueError("limit must be 1..100 and offset must be non-negative")
    where = ["d.category = 'exchange'", "d.report_type = '단일판매공급계약체결'"]
    params: list[Any] = []
    filters = (
        (normalize_company_name(query.company) if query.company else None, "co.company_name_normalized = %s"),
        (query.date_from, "d.receipt_date >= %s"),
        (query.date_to, "d.receipt_date <= %s"),
        (query.counterparty, "c.counterparty ILIKE '%%' || %s || '%%'"),
        (query.min_amount, "c.contract_amount >= %s"),
        (query.max_amount, "c.contract_amount <= %s"),
        (query.contract_start_from, "c.contract_start_date >= %s"),
        (query.contract_start_to, "c.contract_start_date <= %s"),
        (query.active_on, "c.contract_start_date <= %s"),
        (query.active_on, "c.contract_end_date >= %s"),
        (query.min_sales_ratio, "c.sales_ratio >= %s"),
        (query.max_sales_ratio, "c.sales_ratio <= %s"),
    )
    for value, clause in filters:
        if value is not None:
            where.append(clause)
            params.append(value)
    if query.latest_observed_only:
        where.append("NOT EXISTS (SELECT 1 FROM correction_links cl WHERE cl.predecessor_receipt_no=d.receipt_no)")
    if query.order_by == "amount_desc":
        order = "c.contract_amount DESC NULLS LAST, d.receipt_date DESC"
    elif query.order_by == "sales_ratio_desc":
        order = "c.sales_ratio DESC NULLS LAST, d.receipt_date DESC"
    else:
        order = "d.receipt_date DESC, d.receipt_no DESC"
    sql = f"""
        SELECT d.receipt_no, co.company_name AS company, d.receipt_date,
               d.report_name, c.contract_name, c.contract_amount,
               c.contract_amount_raw, c.contract_currency, c.counterparty,
               c.counterparty_raw, c.contract_start_date, c.contract_end_date,
               c.sales_ratio, c.field_provenance,
               f.relative_path AS source_path
        FROM contracts c
        JOIN disclosures d ON d.receipt_no=c.receipt_no
        JOIN companies co ON co.company_id=d.company_id
        JOIN disclosure_files f ON f.file_id=c.source_file_id
        WHERE {' AND '.join(where)}
        ORDER BY {order}, d.receipt_no DESC LIMIT %s OFFSET %s
    """
    params.extend((query.limit, query.offset))
    with connection.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        return _dict_rows(cursor)


def get_contract(connection: Any, receipt_no: str) -> dict[str, Any] | None:
    rows = _by_receipt_nos(connection, [receipt_no])
    return rows[0] if rows else None


def _by_receipt_nos(connection: Any, receipt_nos: list[str]) -> list[dict[str, Any]]:
    if not receipt_nos:
        return []
    placeholders = ",".join(["%s"] * len(receipt_nos))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""SELECT d.receipt_no, co.company_name AS company, d.receipt_date,
                       c.*, f.relative_path AS source_path
                FROM contracts c JOIN disclosures d USING (receipt_no)
                JOIN companies co ON co.company_id=d.company_id
                JOIN disclosure_files f ON f.file_id=c.source_file_id
                WHERE d.receipt_no IN ({placeholders})
                ORDER BY d.receipt_date, d.receipt_no""",
            tuple(receipt_nos),
        )
        return _dict_rows(cursor)


def get_latest_observed_contract(connection: Any, receipt_no: str) -> dict[str, Any] | None:
    """Resolve the explicit chain and return its latest observed contract row."""
    from .corrections import get_latest_observed

    observation = get_latest_observed(connection, receipt_no)
    if observation is None:
        return None
    row = get_contract(connection, observation["receipt_no"])
    if row is not None:
        row["observation_status"] = observation["observation_status"]
        row["chain_receipt_nos"] = observation["chain_receipt_nos"]
    return row


def get_largest_contract(connection: Any, company: str, date_from: date | None = None, date_to: date | None = None) -> dict[str, Any] | None:
    rows = search_contracts(connection, ContractSearch(company=company, date_from=date_from, date_to=date_to, latest_observed_only=True, order_by="amount_desc", limit=1))
    return rows[0] if rows else None
