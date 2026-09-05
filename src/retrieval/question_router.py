"""Deterministic Question Router V1 over frozen retrieval paths."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal, Sequence

from .text_normalization import canonical_report_type, normalize_search_text

Route = Literal["sql", "rag", "correction", "composite"]
RECEIPT = re.compile(r"(?<!\d)(\d{14})(?!\d)")
YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*년?")
ISO_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")

CORRECTION_PATTERNS = (
    "정정", "변경 이력", "변경이력", "이전 공시", "이전공시", "최초 공시", "최초공시",
    "원래 공시", "최신 수정", "수정 공시", "수정공시",
)
SQL_FIELDS = {
    "contract_amount": ("계약금액", "계약 금액", "계약액"),
    "counterparty": ("계약 상대방", "계약상대방", "상대방", "발주처"),
    "contract_period": ("계약기간", "계약 기간", "계약 시작", "계약 종료"),
    "sales_ratio": ("매출액 대비", "매출 대비", "매출비율", "매출 비율", "비율"),
    "filing_date": ("공시일", "접수일", "날짜", "언제"),
    "receipt_lookup": ("접수번호", "접수 번호", "receipt"),
}
NARRATIVE_PATTERNS = (
    "사업 내용", "사업내용", "연구개발", "r&d", "위험요인", "위험 요인", "리스크",
    "사업 전략", "전략", "회사 설명", "어떤 회사", "주요 사업", "기술 분야", "설명해",
)
VAGUE_REFERENCES = ("그 회사", "그 기업", "해당 회사", "이 회사")


@dataclass(frozen=True)
class RouteFilters:
    date_from: date | None = None
    date_to: date | None = None
    report_type: str | None = None
    company: str | None = None
    receipt_no: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    routes: tuple[str, ...]
    intent: str
    company: str | None
    year: int | None
    report_type: str | None
    receipt_no: str | None
    structured_fields: tuple[str, ...]
    filters: RouteFilters
    confidence: float
    reason: str
    needs_clarification: bool = False
    clarification_reason: str | None = None
    router_version: str = "question-router-v1"

    def to_dict(self) -> dict[str, Any]:
        value=asdict(self)
        for key in ("date_from","date_to"):
            item=value["filters"][key]
            if item is not None:value["filters"][key]=item.isoformat()
        return value


def _company(query_norm: str, known_companies: Sequence[str]) -> str | None:
    matches=[name for name in known_companies if normalize_search_text(name) in query_norm]
    return max(matches,key=len) if matches else None


def _metadata(question: str, known_companies: Sequence[str]):
    normalized=normalize_search_text(question);receipt_match=RECEIPT.search(normalized);receipt=receipt_match.group(1) if receipt_match else None
    temporal_text=normalized.replace(receipt,"") if receipt else normalized;dates=ISO_DATE.findall(temporal_text);year_match=YEAR.search(temporal_text);year=int(year_match.group(1)) if year_match else None
    date_from=date_to=None
    if dates:
        try:
            parsed = [date(int(y), int(m), int(d)) for y, m, d in dates]
        except ValueError:
            parsed = []
        if parsed:
            date_from = min(parsed)
            date_to = max(parsed)
    elif year:
        date_from=date(year,1,1);date_to=date(year,12,31)
    report=None
    for label in ("사업보고서","반기보고서","분기보고서"):
        if label in normalized:report=canonical_report_type(label);break
    return normalized,_company(normalized,known_companies),receipt,year,date_from,date_to,report


def route_question(question: str, *, known_companies: Sequence[str] = ()) -> RouteDecision:
    normalized,company,receipt,year,date_from,date_to,report=_metadata(question,known_companies)
    correction=any(value in normalized for value in CORRECTION_PATTERNS)
    fields=tuple(name for name,patterns in SQL_FIELDS.items() if any(value in normalized for value in patterns))
    if receipt and not correction and not fields:fields=("receipt_lookup",)
    if correction and fields==("receipt_lookup",):fields=()
    numeric=bool(re.search(r"(?:얼마|몇\s*(?:원|퍼센트|%|건)|최대|가장\s*(?:큰|높은)|이상|이하)",normalized))
    contract_context="계약" in normalized or "수주" in normalized
    structured=bool(fields or (numeric and contract_context))
    narrative=any(value in normalized for value in NARRATIVE_PATTERNS)
    routes=[]
    if correction:routes.append("correction")
    if structured:routes.append("sql")
    if narrative:routes.append("rag")
    if not routes:routes=["rag"]
    # Stable downstream order: authoritative structured value, correction chain, narrative evidence.
    routes=sorted(set(routes),key=("sql","correction","rag").index)
    route:Route=routes[0] if len(routes)==1 else "composite"
    if correction and structured:intent="structured_correction_comparison"
    elif correction:intent="correction_history"
    elif structured:intent=fields[0] if len(fields)==1 else "structured_contract_search"
    elif narrative:intent="narrative_evidence"
    else:intent="general_document_search"
    vague=any(value in normalized for value in VAGUE_REFERENCES)
    needs=False;clarification=None
    if not normalized:
        needs=True;clarification="question is empty"
    elif not (company or receipt) and ((structured and vague) or (vague and contract_context) or normalized in {"계약 알려줘","계약을 알려줘"}):
        needs=True;clarification="company or receipt number is required for the structured lookup"
    elif correction and not receipt:
        needs=True;clarification="receipt number is required to resolve an explicit correction chain"
    confidence=.96 if receipt else .90 if correction or fields else .84 if narrative else .55
    if needs:confidence=min(confidence,.60)
    reason_parts=[]
    if correction:reason_parts.append("correction terminology")
    if fields:reason_parts.append("structured fields: "+", ".join(fields))
    if narrative:reason_parts.append("narrative evidence terminology")
    if receipt:reason_parts.append("explicit receipt number")
    if not reason_parts:reason_parts.append("default narrative fallback")
    filters=RouteFilters(date_from,date_to,report,company,receipt)
    return RouteDecision(route,tuple(routes),intent,company,year,report,receipt,fields,filters,confidence,"; ".join(reason_parts),needs,clarification)
