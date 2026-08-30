"""Build the checked-in Retrieval V1 freeze summary from DB and regression artifacts."""

from __future__ import annotations

import json,os
from pathlib import Path

from src.db.load_postgres import connect


def main():
    evaluation=json.load(open("artifacts/retrieval_v1_freeze_evaluation.json",encoding="utf-8"));predictions=json.load(open("artifacts/retrieval_v1_freeze_predictions.json",encoding="utf-8"));experiment=json.load(open("reports/title_path_retrieval_experiment.json",encoding="utf-8"))
    with connect(os.environ["DATABASE_URL"]) as c:
        c.execute("SET TRANSACTION READ ONLY")
        counts=c.execute("SELECT (SELECT count(DISTINCT receipt_no) FROM document_sections WHERE metadata->>'loader_namespace'='periodic-full-v1'),(SELECT count(*) FROM document_sections WHERE metadata->>'loader_namespace'='periodic-full-v1'),(SELECT count(*) FROM retrieval_tables WHERE metadata->>'loader_namespace'='periodic-full-v1'),(SELECT count(*) FROM section_vector_chunks WHERE loader_namespace='periodic-full-v1'),(SELECT count(*) FROM section_embedding_vectors WHERE embedding_model='clova-studio/bge-m3' AND embedding_version='embedding-v2')").fetchone()
    payload={"status":"retrieval_v1_frozen","date":"2026-08-31","default_pipeline":{"fts_depth":20,"vector_depth":20,"title_path_depth":20,"title_subchannel_depth":100,"rrf_k":60,"fusion_depth":10,"reranker":"deterministic-feature-reranker-v1"},"corpus":{"loader_namespace":"periodic-full-v1","periodic_disclosures":counts[0],"document_sections":counts[1],"retrieval_tables":counts[2],"vector_chunks":counts[3],"unique_embeddings":counts[4]},"embedding":{"model":"clova-studio/bge-m3","version":"embedding-v2","dimension":1024,"search":"exact cosine"},"candidate_120":evaluation["overall"],"reviewed_36":evaluation["reviewed_only"],"production_regression_cache":predictions["cache"],"adopted":["FTS","Exact Vector","Title/Path FTS+trigram","unweighted RRF","deterministic reranker"],"rejected":{"HNSW":"filtered recall/latency trade-off","Depth50/100":"candidate gain did not justify final cost/ranking","Parent aggregation":"no Top5/Top1 split improvement","Deterministic query expansion":"easy/exact/title drift","Spacing-only expansion":"no Top100 ceiling improvement"},"known_limitations":["no_candidate_any_channel=19","semantic mismatch remains","84 candidate questions lack independent review","deterministic reranker is not neural/LLM","query embedding latency outliers"],"benchmark_policy":{"reviewed_regression":36,"internal_candidate":120,"official_candidate":False},"damage_guard":experiment["threshold_damage"]}
    Path("reports/retrieval_v1_freeze_summary.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    s=payload["candidate_120"]["section"];r=payload["reviewed_36"]["section"];lat=payload["candidate_120"]["latency"]
    md=f"""# Retrieval V1 freeze summary

Frozen on 2026-08-31. The 120-question set remains internal/candidate; the reviewed 36 is stronger regression evidence.

## Default

`FTS20 + Exact Vector20 + Title/Path20 → unweighted RRF k=60 → Top10 → deterministic reranker`

## Corpus

- namespace: periodic-full-v1
- disclosures/sections/tables: {counts[0]:,} / {counts[1]:,} / {counts[2]:,}
- chunks/unique embeddings: {counts[3]:,} / {counts[4]:,}
- embedding: CLOVA Studio bge-m3, embedding-v2, 1,024d, exact cosine

## Metrics

| set | Top1 | R@3 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|---:|
| candidate 120 (113 section eligible) | {s['top1']:.2%} | {s['recall_at_3']:.2%} | {s['recall_at_5']:.2%} | {s['recall_at_10']:.2%} | {s['mrr']:.4f} |
| reviewed 36 | {r['top1']:.2%} | {r['recall_at_3']:.2%} | {r['recall_at_5']:.2%} | {r['recall_at_10']:.2%} | {r['mrr']:.4f} |

Production-interface latency avg/P50/P95/max: {lat['average_ms']:.2f}/{lat['p50_ms']:.2f}/{lat['p95_ms']:.2f}/{lat['max_ms']:.2f} ms (warm local DB; query embeddings reused from cache).

## Freeze and limitations

Frozen components and rejected experiments are documented in `docs/retrieval_v1_freeze.md`. Remaining limitations include 19 no-candidate questions, semantic mismatch, incomplete independent review for 84 questions, a deterministic non-neural reranker, and provider latency outliers.
"""
    Path("reports/retrieval_v1_freeze_summary.md").write_text(md,encoding="utf-8")
if __name__=="__main__":main()
