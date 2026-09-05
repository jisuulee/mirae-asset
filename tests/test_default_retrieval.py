from src.retrieval.default_sections import DEFAULT_RETRIEVAL_V1, RetrievalV1Config, search_default_sections


class Rows:
    def fetchall(self):return [(1,"evidence one"),(2,"evidence two"),(3,"evidence three")]
class Connection:
    def execute(self,sql,params):return Rows()
class Provider:pass


def candidate(section,method):
    return {"section_id":section,"receipt_no":f"R{section}","company_name":"Company","report_name":"Annual","receipt_date":"2026-01-01","title":"Title","section_path":"II / Title","body_snippet":"snippet","score":.8,"retrieval_method":method,"source_file":"x.xml","source_locator":f"x:{section}","provenance":{"file_id":1}}


def test_frozen_config_values():
    assert DEFAULT_RETRIEVAL_V1==RetrievalV1Config(20,20,20,60,10,True)


def test_default_three_way_retrieval_sources_contract_and_filters(monkeypatch):
    calls=[]
    def fts(connection,query,**kwargs):calls.append(("fts",query,kwargs));return [candidate(1,"fts"),candidate(2,"fts")]
    def vector(connection,query,provider,**kwargs):calls.append(("vector",query,kwargs));return [candidate(2,"vector"),candidate(3,"vector")]
    def title(connection,query,**kwargs):calls.append(("title",query,kwargs));return [candidate(3,"title_path"),candidate(2,"title_path")]
    monkeypatch.setattr("src.retrieval.default_sections.search_sections",fts);monkeypatch.setattr("src.retrieval.default_sections.search_vector_sections",vector);monkeypatch.setattr("src.retrieval.default_sections.search_title_paths",title)
    rows=search_default_sections(Connection(),"original query",Provider(),company="Company",receipt_no="R2")
    assert rows[0]["section_id"]==2 and rows[0]["retrieval_sources"]==["fts","vector","title_path"]
    assert rows[0]["fts_rank"]==2 and rows[0]["vector_rank"]==1 and rows[0]["title_path_rank"]==2
    assert rows[0]["channel_provenance"]["vector"]["source_locator"]=="x:2"
    assert rows[0]["evidence_text"] and rows[0]["company"]=="Company" and rows[0]["retrieval_config"]=="retrieval-v1-frozen"
    assert all(call[1]=="original query" for call in calls)
    assert all(call[2]["company"]=="Company" and call[2]["receipt_no"]=="R2" for call in calls)


def test_empty_channels_and_reranker_disable(monkeypatch):
    monkeypatch.setattr("src.retrieval.default_sections.search_sections",lambda *a,**k:[]);monkeypatch.setattr("src.retrieval.default_sections.search_vector_sections",lambda *a,**k:[]);monkeypatch.setattr("src.retrieval.default_sections.search_title_paths",lambda *a,**k:[])
    assert search_default_sections(Connection(),"query",Provider(),config=RetrievalV1Config(reranker_enabled=False))==[]


def test_top_k_cannot_escape_frozen_fusion_depth():
    try:search_default_sections(Connection(),"query",Provider(),top_k=11)
    except ValueError:pass
    else:raise AssertionError("top_k beyond frozen fusion depth must fail")
