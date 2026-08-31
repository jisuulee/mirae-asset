from datetime import date
from decimal import Decimal
import inspect
import json

from src.retrieval.question_router import route_question
from src.retrieval.routed_search import retrieve

COMPANIES=("LG에너지솔루션","삼성전자","현대건설")


def test_exact_number_contract_amount_and_receipt_go_sql():
    amount=route_question("삼성전자의 2025년 계약금액이 얼마야?",known_companies=COMPANIES)
    assert amount.route=="sql" and amount.intent=="contract_amount" and amount.company=="삼성전자" and amount.year==2025
    assert amount.filters.date_from==date(2025,1,1) and amount.filters.date_to==date(2025,12,31)
    receipt=route_question("접수번호 20240102000001 계약 조회",known_companies=COMPANIES)
    assert receipt.route=="sql" and receipt.receipt_no=="20240102000001" and receipt.year is None


def test_narrative_rd_and_risk_go_rag():
    assert route_question("삼성전자 주요 사업 내용을 설명해줘",known_companies=COMPANIES).route=="rag"
    assert route_question("연구개발 분야를 찾아줘").route=="rag"
    result=route_question("2024년 사업보고서의 위험요인을 알려줘")
    assert result.route=="rag" and result.report_type=="annual" and result.year==2024


def test_correction_priority_latest_and_multi_intent():
    correction=route_question("20240102000001 최초 공시와 최신 정정을 보여줘")
    assert correction.route=="correction" and correction.routes==("correction",)
    mixed=route_question("20240102000001 계약금액과 정정 내용을 알려줘")
    assert mixed.route=="composite" and mixed.routes==("sql","correction")


def test_clarification_and_determinism():
    vague=route_question("그 회사 계약 알려줘")
    assert vague.needs_clarification and vague.clarification_reason
    assert route_question("그 회사 계약 알려줘")==vague
    assert route_question("").needs_clarification


def test_no_benchmark_specific_router_inputs():
    parameters=set(inspect.signature(route_question).parameters)
    assert not parameters&{"question_id","category","difficulty","gold_section_ids"}


class CompanyRows:
    def __init__(self,rows):self.rows=rows
    def fetchall(self):return self.rows
class Connection:
    def execute(self,sql,params=None):
        if "d.receipt_no,co.company_name" in sql:return CompanyRows([("20240102000001","삼성전자")])
        return CompanyRows([("삼성전자",),("현대건설",)])
class Provider:pass


def contract_row():
    return {"receipt_no":"20240102000001","company":"삼성전자","receipt_date":date(2024,1,2),"report_name":"단일판매ㆍ공급계약체결","contract_name":"공급계약","contract_amount":Decimal("1000"),"contract_currency":"KRW","counterparty":"상대사","contract_start_date":date(2024,1,1),"contract_end_date":date(2025,1,1),"sales_ratio":Decimal("3.2"),"field_provenance":{"contract_amount":{"locator":"row:1"}},"source_path":"contract.xml"}


def test_retrieval_entrypoint_sql_evidence(monkeypatch):
    monkeypatch.setattr("src.retrieval.routed_search.get_contract",lambda connection,receipt:contract_row())
    result=retrieve("접수번호 20240102000001 계약금액",Connection(),known_companies=list(COMPANIES))
    assert result.decision.route=="sql" and len(result.evidence)==1
    evidence=result.evidence[0]
    assert evidence.route=="sql" and evidence.evidence_type=="structured_contract" and evidence.source_locator=="row:1"
    serialized = result.to_dict()
    assert serialized["evidence"][0]["evidence_value"]["contract_amount"] == "1000"
    assert serialized["evidence"][0]["evidence_value"]["contract_start_date"] == "2024-01-01"
    json.dumps(serialized)


def test_invalid_explicit_date_is_not_guessed_or_raised():
    result = route_question("삼성전자 2025-99-99 사업 내용을 설명해줘", known_companies=COMPANIES)
    assert result.route == "rag"
    assert result.filters.date_from is None and result.filters.date_to is None


def test_retrieval_entrypoint_rag_and_provenance(monkeypatch):
    row={"receipt_no":"R1","company":"삼성전자","report_name":"사업보고서","receipt_date":date(2025,3,1),"evidence_text":"사업 근거","provenance":{"file_id":1},"source_file":"a.xml","source_locator":"xpath:1","rank":1,"score":.5,"retrieval_sources":["fts","title_path"]}
    monkeypatch.setattr("src.retrieval.routed_search.search_default_sections",lambda *a,**k:[row])
    result=retrieve("삼성전자 주요 사업 내용을 설명해줘",Connection(),provider=Provider(),known_companies=list(COMPANIES))
    assert result.evidence[0].route=="rag" and result.evidence[0].retrieval_sources==("fts","title_path")
    assert result.evidence[0].provenance=={"file_id":1}


def test_retrieval_entrypoint_correction_and_composite(monkeypatch):
    history=[{"receipt_no":"20240102000001","receipt_date":date(2024,1,2),"report_name":"원공시","is_correction_name":False,"is_correction_rm":False,"source_path":"a.xml"}]
    monkeypatch.setattr("src.retrieval.routed_search.get_correction_history",lambda connection,receipt:history)
    monkeypatch.setattr("src.retrieval.routed_search.get_contract",lambda connection,receipt:contract_row())
    result=retrieve("20240102000001 계약금액과 정정 내용을 알려줘",Connection(),known_companies=list(COMPANIES))
    assert result.decision.route=="composite" and {x.route for x in result.evidence}=={"sql","correction"}


def test_clarification_prevents_retrieval_calls():
    result=retrieve("그 회사 계약 알려줘",Connection(),known_companies=[])
    assert result.decision.needs_clarification and not result.evidence and not result.errors
