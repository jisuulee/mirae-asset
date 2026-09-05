"""Question Router V1 execution adapters and shared downstream evidence package."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .contracts import ContractSearch,get_contract,search_contracts
from .corrections import get_correction_history
from .default_sections import search_default_sections
from .embeddings import EmbeddingProvider
from .question_router import RouteDecision,route_question
from .reranker import RerankCache
from .text_normalization import normalize_search_text


@dataclass(frozen=True)
class Evidence:
    route: str
    evidence_type: str
    receipt_no: str | None
    company: str | None
    report_name: str | None
    receipt_date: date | None
    evidence_text: str
    evidence_value: Any
    provenance: Any
    source_file: str | None
    source_locator: str | None
    rank: int | None = None
    score: float | None = None
    retrieval_sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True)
class RoutedSearchResult:
    decision: RouteDecision
    evidence: tuple[Evidence,...]
    errors: tuple[str,...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "router": self.decision.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "errors": list(self.errors),
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, Decimal)):
        return value.isoformat() if isinstance(value, date) else str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _companies(connection) -> list[str]:
    return [str(row[0]) for row in connection.execute("SELECT company_name FROM companies ORDER BY length(company_name) DESC,company_name").fetchall()]


def _locator(provenance:Any) -> str|None:
    if not isinstance(provenance,dict):return None
    for value in provenance.values():
        if isinstance(value,dict) and value.get("locator"):return str(value["locator"])
    return str(provenance.get("locator")) if provenance.get("locator") else None


def _sql_evidence(rows:list[dict[str,Any]]) -> list[Evidence]:
    output=[]
    for rank,row in enumerate(rows,1):
        fields={key:row.get(key) for key in ("contract_name","contract_amount","contract_currency","counterparty","contract_start_date","contract_end_date","sales_ratio")}
        text=f"계약명={fields['contract_name']}; 계약금액={fields['contract_amount']} {fields['contract_currency'] or ''}; 상대방={fields['counterparty']}; 계약기간={fields['contract_start_date']}~{fields['contract_end_date']}; 매출비율={fields['sales_ratio']}"
        provenance=row.get("field_provenance") or {}
        output.append(Evidence("sql","structured_contract",row.get("receipt_no"),row.get("company"),row.get("report_name"),row.get("receipt_date"),text,fields,provenance,row.get("source_path"),_locator(provenance),rank,None,("structured_sql",)))
    return output


def _correction_evidence(connection,rows:list[dict[str,Any]]) -> list[Evidence]:
    receipts=[row["receipt_no"] for row in rows];metadata={}
    if receipts:
        metadata={str(r):str(c) for r,c in connection.execute("SELECT d.receipt_no,co.company_name FROM disclosures d JOIN companies co USING(company_id) WHERE d.receipt_no=ANY(%s)",(receipts,)).fetchall()}
    return [Evidence("correction","correction_observation",row["receipt_no"],metadata.get(row["receipt_no"]),row.get("report_name"),row.get("receipt_date"),f"{row.get('receipt_date')} {row.get('report_name')} (correction={bool(row.get('is_correction_name') or row.get('is_correction_rm'))})",{"is_correction_name":row.get("is_correction_name"),"is_correction_rm":row.get("is_correction_rm")},{"explicit_chain":True,"observation_order":rank},row.get("source_path"),None,rank,None,("correction_chain",)) for rank,row in enumerate(rows,1)]


def _rag_evidence(rows:list[dict[str,Any]]) -> list[Evidence]:
    return [Evidence("rag","document_section",row.get("receipt_no"),row.get("company"),row.get("report_name"),row.get("receipt_date"),row.get("evidence_text") or "",None,row.get("provenance"),row.get("source_file"),row.get("source_locator"),row.get("rank"),float(row["score"]) if row.get("score") is not None else None,tuple(row.get("retrieval_sources") or ())) for row in rows]


def retrieve(question:str,connection,*,provider:EmbeddingProvider|None=None,rerank_cache:RerankCache|None=None,known_companies:list[str]|None=None,limit:int=10) -> RoutedSearchResult:
    decision=route_question(question,known_companies=known_companies if known_companies is not None else _companies(connection))
    if decision.needs_clarification:return RoutedSearchResult(decision,())
    evidence=[];errors=[];f=decision.filters
    for route in decision.routes:
        try:
            if route=="sql":
                if f.receipt_no:rows=[row] if (row:=get_contract(connection,f.receipt_no)) else []
                else:
                    normalized=normalize_search_text(question);order="amount_desc" if "contract_amount" in decision.structured_fields and any(x in normalized for x in ("최대","가장 큰")) else "date_desc"
                    rows=search_contracts(connection,ContractSearch(company=f.company,date_from=f.date_from,date_to=f.date_to,order_by=order,limit=min(limit,100)))
                evidence.extend(_sql_evidence(rows))
            elif route=="correction":
                if not f.receipt_no:errors.append("correction route requires receipt_no")
                else:evidence.extend(_correction_evidence(connection,get_correction_history(connection,f.receipt_no)))
            elif route=="rag":
                if provider is None:errors.append("rag route requires an embedding provider")
                else:evidence.extend(_rag_evidence(search_default_sections(connection,question,provider,top_k=min(limit,10),company=f.company,date_from=f.date_from,date_to=f.date_to,report_type=f.report_type,receipt_no=f.receipt_no,rerank_cache=rerank_cache)))
        except (LookupError,ValueError) as exc:errors.append(f"{route}: {type(exc).__name__}: {exc}")
    return RoutedSearchResult(decision,tuple(evidence),tuple(errors))
