"""Build the manually curated SQL Retrieval V0 benchmark from the fixed DB snapshot.

The question wording and query specifications are curated below. Gold receipt
numbers are resolved from PostgreSQL so they are never guessed; every row keeps
the SQL snapshot and source provenance used for review.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from db.load_postgres import connect
from retrieval.contracts import ContractSearch, get_contract, search_contracts
from retrieval.corrections import get_correction_history, get_latest_observed


SPECS = [
    # A. Company + filing period
    ("CP01", "company_period", "현대건설의 2023년 6월 26일 계약 공시는?", {"operation":"search","company":"현대건설","date_from":"2023-06-26","date_to":"2023-06-26"}),
    ("CP02", "company_period", "대우건설의 2025년 계약 공시를 최신순으로 찾아줘", {"operation":"search","company":"대우건설","date_from":"2025-01-01","date_to":"2025-12-31"}),
    ("CP03", "company_period", "삼성E&A의 2024년 계약 공시를 찾아줘", {"operation":"search","company":"삼성E&A","date_from":"2024-01-01","date_to":"2024-12-31"}),
    ("CP04", "company_period", "한화오션의 2025년 상반기 계약 공시는?", {"operation":"search","company":"한화오션","date_from":"2025-01-01","date_to":"2025-06-30"}),
    ("CP05", "company_period", "삼성바이오로직스의 2023년 계약 공시를 보여줘", {"operation":"search","company":"삼성바이오로직스","date_from":"2023-01-01","date_to":"2023-12-31"}),
    ("CP06", "company_period", "LIG디펜스앤에어로스페이스의 2025년 계약 공시는?", {"operation":"search","company":"LIG디펜스앤에어로스페이스","date_from":"2025-01-01","date_to":"2025-12-31"}),
    # B. Amount
    ("AM01", "amount", "대우건설에서 현재 관측 기준 가장 큰 계약은?", {"operation":"search","company":"대우건설","latest_observed_only":True,"order_by":"amount_desc","single_best":True}),
    ("AM02", "amount", "현대건설의 2024년 공시 중 계약금액이 가장 큰 계약은?", {"operation":"search","company":"현대건설","date_from":"2024-01-01","date_to":"2024-12-31","order_by":"amount_desc","single_best":True}),
    ("AM03", "amount", "삼성E&A의 5천억원 이상 계약을 금액순으로 찾아줘", {"operation":"search","company":"삼성E&A","min_amount":500000000000,"order_by":"amount_desc"}),
    ("AM04", "amount", "한화오션의 2025년 계약 중 가장 큰 계약은?", {"operation":"search","company":"한화오션","date_from":"2025-01-01","date_to":"2025-12-31","order_by":"amount_desc","single_best":True}),
    ("AM05", "amount", "삼성바이오로직스의 4천억원 이상 계약을 찾아줘", {"operation":"search","company":"삼성바이오로직스","min_amount":400000000000,"order_by":"amount_desc"}),
    ("AM06", "amount", "한전기술의 계약 중 금액이 가장 큰 계약은?", {"operation":"search","company":"한전기술","latest_observed_only":True,"order_by":"amount_desc","single_best":True}),
    # C. Counterparty
    ("CT01", "counterparty", "한국토지주택공사를 상대방으로 하는 계약을 찾아줘", {"operation":"search","counterparty":"한국토지주택공사"}),
    ("CT02", "counterparty", "Pfizer와 삼성바이오로직스가 체결한 계약은?", {"operation":"search","company":"삼성바이오로직스","counterparty":"Pfizer"}),
    ("CT03", "counterparty", "S-OIL을 상대방으로 하는 현대건설 계약은?", {"operation":"search","company":"현대건설","counterparty":"S-OIL"}),
    ("CT04", "counterparty", "Shell Petroleum과 대우건설의 계약을 찾아줘", {"operation":"search","company":"대우건설","counterparty":"Shell Petroleum"}),
    ("CT05", "counterparty", "두산에너빌리티가 상대방인 한전기술 계약은?", {"operation":"search","company":"한전기술","counterparty":"두산에너빌리티"}),
    ("CT06", "counterparty", "홍콩 병원관리국과 현대건설의 계약 공시는?", {"operation":"search","company":"현대건설","counterparty":"병원관리국"}),
    # D. Contract dates / active period
    ("DT01", "date_range", "2024년 1월 1일에 진행 중인 삼성E&A 계약은?", {"operation":"search","company":"삼성E&A","active_on":"2024-01-01"}),
    ("DT02", "date_range", "2025년 1월 1일에 진행 중인 한화오션 계약은?", {"operation":"search","company":"한화오션","active_on":"2025-01-01"}),
    ("DT03", "date_range", "2026년 1월 1일에 진행 중인 대우건설 계약은?", {"operation":"search","company":"대우건설","active_on":"2026-01-01"}),
    ("DT04", "date_range", "2025년에 시작한 삼성바이오로직스 계약은?", {"operation":"search","company":"삼성바이오로직스","contract_start_from":"2025-01-01","contract_start_to":"2025-12-31"}),
    ("DT05", "date_range", "2024년에 시작한 현대건설 계약을 찾아줘", {"operation":"search","company":"현대건설","contract_start_from":"2024-01-01","contract_start_to":"2024-12-31"}),
    ("DT06", "date_range", "2025년 6월 30일에 진행 중인 한전기술 계약은?", {"operation":"search","company":"한전기술","active_on":"2025-06-30"}),
    # E. Sales ratio
    ("SR01", "sales_ratio", "현대건설 계약 중 매출액 대비 비율이 가장 높은 것은?", {"operation":"search","company":"현대건설","order_by":"sales_ratio_desc","single_best":True}),
    ("SR02", "sales_ratio", "대우건설의 매출액 대비 10% 이상 계약을 비율순으로 찾아줘", {"operation":"search","company":"대우건설","min_sales_ratio":10,"order_by":"sales_ratio_desc"}),
    ("SR03", "sales_ratio", "삼성바이오로직스의 매출액 대비 비율이 가장 높은 계약은?", {"operation":"search","company":"삼성바이오로직스","order_by":"sales_ratio_desc","single_best":True}),
    ("SR04", "sales_ratio", "한화오션의 매출액 대비 10% 이상 계약은?", {"operation":"search","company":"한화오션","min_sales_ratio":10,"order_by":"sales_ratio_desc"}),
    ("SR05", "sales_ratio", "삼성E&A 계약 중 sales ratio가 가장 높은 계약은?", {"operation":"search","company":"삼성E&A","order_by":"sales_ratio_desc","single_best":True}),
    ("SR06", "sales_ratio", "한전기술의 매출액 대비 20% 이상 계약을 찾아줘", {"operation":"search","company":"한전기술","min_sales_ratio":20,"order_by":"sales_ratio_desc"}),
    # F. Correction aware
    ("CR01", "correction", "현대건설 20230213800847 계약의 정정 이력은?", {"operation":"correction_history","query_receipt_no":"20230213800847"}),
    ("CR02", "correction", "현대건설 20230110800416 계약의 최신 관측 공시는?", {"operation":"latest_observed","query_receipt_no":"20230110800416","single_best":True}),
    ("CR03", "correction", "대우건설 20230203800007 계약의 최신 관측본은?", {"operation":"latest_observed","query_receipt_no":"20230203800007","single_best":True}),
    ("CR04", "correction", "삼성E&A 20230210800576 계약의 현재 데이터상 최신본은?", {"operation":"latest_observed","query_receipt_no":"20230210800576","single_best":True}),
    ("CR05", "correction", "한전기술 20230105800282 계약의 정정 이력을 보여줘", {"operation":"correction_history","query_receipt_no":"20230105800282"}),
    ("CR06", "correction", "한화오션 20230213800586 계약의 latest observed 공시는?", {"operation":"latest_observed","query_receipt_no":"20230213800586","single_best":True}),
    # G. Direct receipt lookup
    ("RC01", "receipt_lookup", "접수번호 20251224800595 계약을 조회해줘", {"operation":"receipt_lookup","query_receipt_no":"20251224800595","single_best":True}),
    ("RC02", "receipt_lookup", "접수번호 20250526800282 계약은?", {"operation":"receipt_lookup","query_receipt_no":"20250526800282","single_best":True}),
    ("RC03", "receipt_lookup", "20240401801549 공시의 계약 정보를 보여줘", {"operation":"receipt_lookup","query_receipt_no":"20240401801549","single_best":True}),
    ("RC04", "receipt_lookup", "20250912800544 계약 공시를 직접 조회해줘", {"operation":"receipt_lookup","query_receipt_no":"20250912800544","single_best":True}),
    ("RC05", "receipt_lookup", "접수번호 20230704800001의 계약은?", {"operation":"receipt_lookup","query_receipt_no":"20230704800001","single_best":True}),
    ("RC06", "receipt_lookup", "20260220801140 계약을 조회해줘", {"operation":"receipt_lookup","query_receipt_no":"20260220801140","single_best":True}),
]


def parse_date(value):
    return date.fromisoformat(value) if value else None


def execute(connection, query):
    operation = query["operation"]
    if operation == "receipt_lookup":
        row = get_contract(connection, query["query_receipt_no"])
        return [row] if row else []
    if operation == "correction_history":
        return get_correction_history(connection, query["query_receipt_no"])
    if operation == "latest_observed":
        row = get_latest_observed(connection, query["query_receipt_no"])
        return [row] if row else []
    kwargs = {key: value for key, value in query.items() if key not in {"operation", "single_best"}}
    for key in ("date_from", "date_to", "contract_start_from", "contract_start_to", "active_on"):
        if key in kwargs:
            kwargs[key] = parse_date(kwargs[key])
    kwargs["limit"] = 100
    return search_contracts(connection, ContractSearch(**kwargs))


def build(database_url: str):
    rows = []
    with connect(database_url) as connection:
        for code, query_type, question, query in SPECS:
            results = execute(connection, query)
            if not results:
                raise RuntimeError(f"curated query returned no result: {code}")
            receipts = [row["receipt_no"] for row in results]
            expected = receipts[:1] if query.get("single_best") else receipts
            first_contract = get_contract(connection, expected[0])
            source = first_contract.get("source_path") if first_contract else results[0].get("source_path")
            row = {
                "question_id": f"contract_{code.lower()}",
                "question": question,
                "query_type": query_type,
                "expected_receipt_nos": expected,
                "company_name": query.get("company") or (first_contract or {}).get("company"),
                "query": query,
                "notes": "Gold resolved from the fixed PostgreSQL snapshot; raw/normalized values and provenance were reviewed.",
                "validation_source": source or "PostgreSQL correction_links + disclosures",
            }
            if len(expected) == 1:
                row["expected_receipt_no"] = expected[0]
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("retrieval_questions.jsonl"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    rows = build(args.database_url)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"question_count": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
