"""Dedicated PostgreSQL title/path search; section body text is never queried."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .sections import _rows, _where
from .text_normalization import normalize_search_text

TITLE_PATH_SUBCHANNEL_DEPTH = 100


def search_title_paths(connection:Any,query:str,*,top_k:int=20,mode:str="combined",company:str|None=None,date_from:date|None=None,date_to:date|None=None,report_type:str|None=None,receipt_no:str|None=None,corpus_namespace:str="periodic-full-v1"):
    if not 1<=top_k<=100:raise ValueError("top_k must be 1..100")
    if mode not in {"fts","trigram","combined"}:raise ValueError("mode must be fts, trigram, or combined")
    query_norm=normalize_search_text(query);query_nospace=normalize_search_text(query,remove_spaces=True)
    if not query_norm:return []
    if mode=="combined":
        # The frozen experiment fuses deep title subchannels before pruning the
        # dedicated channel to Title/Path20. Do not prune each subchannel first.
        depth=max(top_k,TITLE_PATH_SUBCHANNEL_DEPTH)
        return fuse_named_channels({"title_fts":search_title_paths(connection,query,top_k=depth,mode="fts",company=company,date_from=date_from,date_to=date_to,report_type=report_type,receipt_no=receipt_no,corpus_namespace=corpus_namespace),"title_trigram":search_title_paths(connection,query,top_k=depth,mode="trigram",company=company,date_from=date_from,date_to=date_to,report_type=report_type,receipt_no=receipt_no,corpus_namespace=corpus_namespace)},top_k=top_k)
    clauses,params=_where(query_norm,company=company,date_from=date_from,date_to=date_to,report_type=report_type,section_title=None,receipt_no=receipt_no,corpus_namespace=corpus_namespace)
    if mode=="fts":
        score="ts_rank_cd(s.title_path_search_tsv,websearch_to_tsquery('simple',%s))";score_params=[query_norm];clauses.append("s.title_path_search_tsv @@ websearch_to_tsquery('simple',%s)");params.append(query_norm)
    else:
        score="greatest(similarity(s.title_path_search_normalized,%s),word_similarity(%s,s.title_path_search_normalized),similarity(s.title_path_search_nospace,%s))";score_params=[query_norm,query_norm,query_nospace]
        clauses.append("(s.title_path_search_normalized %% %s OR s.title_path_search_normalized <%% %s OR s.title_path_search_nospace %% %s)");params.extend((query_norm,query_norm,query_nospace))
    sql=f"""SELECT s.section_id,s.receipt_no,co.company_name,d.report_name,d.receipt_date,s.title_normalized title,s.section_path,'' body_snippet,{score} score,%s retrieval_method,f.relative_path source_file,s.source_locator,s.metadata->'provenance' provenance,f.file_role
      FROM document_sections s JOIN disclosures d USING(receipt_no) JOIN companies co ON co.company_id=d.company_id JOIN disclosure_files f ON f.file_id=s.file_id
      WHERE {' AND '.join(clauses)} ORDER BY score DESC,(f.file_role='main') DESC,s.section_id LIMIT %s"""
    with connection.cursor() as cursor:cursor.execute(sql,tuple(score_params+[f"title_path_{mode}"]+params+[top_k]));return _rows(cursor)


def fuse_named_channels(channels:dict[str,Iterable[dict[str,Any]]],*,top_k:int=100,rrf_k:int=60,weights:dict[str,float]|None=None):
    if not 1<=top_k<=100 or rrf_k<1:raise ValueError("invalid fusion parameters")
    weights=weights or {};merged={}
    source_labels={"fts":"fts","vector":"vector","title":"title_path"}
    for name,results in channels.items():
        seen=set();weight=float(weights.get(name,1.0))
        for rank,result in enumerate(results,1):
            section_id=int(result["section_id"])
            if section_id in seen:continue
            seen.add(section_id);row=merged.setdefault(section_id,{**result,"section_id":section_id,"rrf_score":0.0,"channel_ranks":{},"channel_scores":{},"channel_provenance":{},"retrieval_method":"multi_channel_rrf"})
            row["channel_ranks"][name]=rank;row["channel_scores"][name]=result.get("score");row["channel_provenance"][name]={key:result.get(key) for key in ("vector_chunk_id","chunk_order","source_locator","provenance","source_file") if result.get(key) is not None};row["rrf_score"]+=weight/(rrf_k+rank)
            if name=="fts":row.update({k:v for k,v in result.items() if k not in {"score","retrieval_method"}})
    ranked=sorted(merged.values(),key=lambda x:(-x["rrf_score"],min(x["channel_ranks"].values()),x["section_id"]))
    for position,row in enumerate(ranked[:top_k],1):
        row["score"]=row["rrf_score"];row["hybrid_rank"]=position;row["fts_rank"]=row["channel_ranks"].get("fts");row["vector_rank"]=row["channel_ranks"].get("vector");row["title_path_rank"]=row["channel_ranks"].get("title");row["title_rank"]=row["title_path_rank"];row["retrieval_sources"]=[source_labels.get(name,name) for name in channels if name in row["channel_ranks"]]
    return ranked[:top_k]
