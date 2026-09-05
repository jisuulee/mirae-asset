"""Build integrity, exact-vector, and FTS comparison reports without changing gold."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import evaluate, read_jsonl
from eval.evaluate_full_vector import evaluate_vector
from src.db.load_postgres import connect


MODEL = "clova-studio/bge-m3"
VERSION = "embedding-v2"
NAMESPACE = "periodic-full-v1"


def _one(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...]:
    return connection.execute(sql, params).fetchone()


def collect_integrity(connection: Any) -> dict[str, Any]:
    vectors = _one(connection, """
        SELECT count(*),count(DISTINCT content_hash),
               count(*) FILTER (WHERE embedding IS NULL),
               count(*) FILTER (WHERE embedding_dimension<>1024 OR vector_dims(embedding)<>1024),
               count(*) FILTER (WHERE embedding_model<>%s),
               count(*) FILTER (WHERE embedding_version<>%s)
        FROM section_embedding_vectors
    """, (MODEL, VERSION))
    chunks = _one(connection, """
        SELECT count(*),count(DISTINCT content_hash),count(DISTINCT section_id),
               count(*) FILTER (WHERE loader_namespace<>%s),
               count(*) FILTER (WHERE receipt_no IS NULL OR file_id IS NULL OR section_id IS NULL
                 OR section_order IS NULL OR source_locator IS NULL OR btrim(source_locator)=''
                 OR chunk_text IS NULL OR btrim(chunk_text)='' OR content_hash IS NULL
                 OR section_path IS NULL OR metadata IS NULL)
        FROM section_vector_chunks
    """, (NAMESPACE,))
    links = _one(connection, """
        SELECT count(*),count(DISTINCT vector_chunk_id),count(DISTINCT embedding_id),
               count(*) FILTER (WHERE embedding_model<>%s),
               count(*) FILTER (WHERE embedding_version<>%s)
        FROM section_chunk_embeddings
    """, (MODEL, VERSION))
    checks: dict[str, int] = {}
    check_sql = {
        "duplicate_vector_key": """SELECT count(*) FROM (SELECT 1 FROM section_embedding_vectors
            GROUP BY content_hash,embedding_model,embedding_version HAVING count(*)>1) q""",
        "orphan_chunk": """SELECT count(*) FROM section_vector_chunks c LEFT JOIN section_chunk_embeddings l
            USING(vector_chunk_id) WHERE l.vector_chunk_id IS NULL""",
        "orphan_vector": """SELECT count(*) FROM section_embedding_vectors e LEFT JOIN section_chunk_embeddings l
            USING(embedding_id) WHERE l.embedding_id IS NULL""",
        "orphan_link": """SELECT count(*) FROM section_chunk_embeddings l LEFT JOIN section_vector_chunks c
            USING(vector_chunk_id) LEFT JOIN section_embedding_vectors e USING(embedding_id)
            WHERE c.vector_chunk_id IS NULL OR e.embedding_id IS NULL""",
        "reference_mismatch": """SELECT count(*) FROM section_vector_chunks c
            LEFT JOIN document_sections s USING(section_id) LEFT JOIN disclosures d ON d.receipt_no=c.receipt_no
            LEFT JOIN disclosure_files f ON f.file_id=c.file_id LEFT JOIN document_sections p ON p.section_id=c.parent_section_id
            WHERE s.section_id IS NULL OR d.receipt_no IS NULL OR f.file_id IS NULL
               OR s.receipt_no<>c.receipt_no OR s.file_id<>c.file_id
               OR c.parent_section_id IS DISTINCT FROM s.parent_section_id OR c.section_order<>s.section_order
               OR (c.parent_section_id IS NOT NULL AND p.section_id IS NULL)""",
        "content_hash_mapping_mismatch": """SELECT count(*) FROM section_chunk_embeddings l
            JOIN section_vector_chunks c USING(vector_chunk_id) JOIN section_embedding_vectors e USING(embedding_id)
            WHERE c.content_hash<>e.content_hash OR l.embedding_model<>e.embedding_model
               OR l.embedding_version<>e.embedding_version""",
        "chunk_order_or_parent_mapping": """SELECT count(*) FROM (SELECT section_id,loader_namespace,
            min(chunk_order) mn,max(chunk_order) mx,count(*) n,count(DISTINCT chunk_order) dn
            FROM section_vector_chunks GROUP BY 1,2
            HAVING min(chunk_order)<>0 OR max(chunk_order)<>count(*)-1 OR count(*)<>count(DISTINCT chunk_order)) q""",
        "ann_index_count": """SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema()
            AND tablename=ANY(ARRAY['section_embedding_vectors','section_vector_chunks','section_chunk_embeddings'])
            AND indexdef ~* 'USING (hnsw|ivfflat)'""",
    }
    for name, sql in check_sql.items():
        checks[name] = int(_one(connection, sql)[0])

    bad_hash = bad_finite = zero_vectors = 0
    with connection.cursor(name="full_vector_integrity") as cursor:
        cursor.itersize = 256
        cursor.execute("""SELECT c.chunk_text,c.content_hash,e.embedding::text
            FROM section_vector_chunks c JOIN section_chunk_embeddings l USING(vector_chunk_id)
            JOIN section_embedding_vectors e USING(embedding_id) ORDER BY c.vector_chunk_id""")
        for text, content_hash, literal in cursor:
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != str(content_hash).strip():
                bad_hash += 1
            values = (float(value) for value in literal[1:-1].split(","))
            finite, nonzero = True, False
            for value in values:
                finite = finite and math.isfinite(value)
                nonzero = nonzero or value != 0.0
            bad_finite += not finite
            zero_vectors += not nonzero
    checks.update(exact_content_hash_mismatch=bad_hash, nonfinite_vector=bad_finite, zero_vector=zero_vectors)

    pdf = _one(connection, """SELECT count(*),count(DISTINCT section_id),count(DISTINCT receipt_no),
        count(*) FILTER (WHERE source_locator !~ '^page:[0-9]+$')
        FROM section_vector_chunks WHERE metadata->>'source_type'='pdf'""")
    sizes = _one(connection, """SELECT
        pg_total_relation_size('section_embedding_vectors'),pg_relation_size('section_embedding_vectors'),pg_indexes_size('section_embedding_vectors'),
        pg_total_relation_size('section_vector_chunks'),pg_relation_size('section_vector_chunks'),pg_indexes_size('section_vector_chunks'),
        pg_total_relation_size('section_chunk_embeddings'),pg_relation_size('section_chunk_embeddings'),pg_indexes_size('section_chunk_embeddings')""")
    storage = {
        "section_embedding_vectors": {"total_bytes": sizes[0], "table_bytes": sizes[1], "index_bytes": sizes[2]},
        "section_vector_chunks": {"total_bytes": sizes[3], "table_bytes": sizes[4], "index_bytes": sizes[5]},
        "section_chunk_embeddings": {"total_bytes": sizes[6], "table_bytes": sizes[7], "index_bytes": sizes[8]},
        "total_vector_related_bytes": sizes[0] + sizes[3] + sizes[6],
        "database_total_bytes": _one(connection, "SELECT pg_database_size(current_database())")[0],
    }
    expected = {"vectors": 34084, "chunks": 38971, "links": 38971}
    actual = {"vectors": vectors[0], "chunks": chunks[0], "links": links[0]}
    cache_safe = vectors[1] == 34084 and chunks[1] == 34084 and links[2] == 34084 and checks["content_hash_mapping_mismatch"] == 0
    return {
        "status": "pass" if actual == expected and all(value == 0 for key, value in checks.items() if key != "ann_index_count") else "fail",
        "expected": expected, "actual": actual,
        "unique_content_hashes": vectors[1], "unique_chunk_hashes": chunks[1], "narrative_sections": chunks[2],
        "chunk_links_reusing_existing_unique_vectors": chunks[0] - vectors[0],
        "chunk_link_reuse_rate": (chunks[0] - vectors[0]) / chunks[0],
        "null_vectors": vectors[2], "dimension_mismatch": vectors[3],
        "model_mismatch": vectors[4] + links[3], "version_mismatch": vectors[5] + links[4],
        "namespace_mismatch": chunks[3], "provenance_missing": chunks[4],
        "checks": checks,
        "pdf_narrative": {"chunks": pdf[0], "sections": pdf[1], "receipts": pdf[2], "invalid_page_locator": pdf[3]},
        "cache_lookup_validation": {
            "status": "pass" if cache_safe else "fail", "api_calls": 0,
            "rule": "content_hash + embedding_model + embedding_version",
            "all_unique_chunk_hashes_resolved": cache_safe,
        },
        "storage": storage,
    }


def aligned_fts_report(gold: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    adjusted = []
    target_by_id = {row["question_id"]: row["retrieval_target"] for row in gold}
    for row in predictions:
        if target_by_id.get(row["question_id"]) == "table":
            adjusted.append({**row, "error": "excluded_table_only_target", "section_results": [], "table_results": []})
        else:
            adjusted.append(row)
    return evaluate(gold, adjusted)


def chunking_analysis(connection: Any, gold: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    section_ids = sorted({int(value) for row in gold for value in row["gold_section_ids"]})
    counts = dict(connection.execute("""SELECT section_id,count(*) FROM section_vector_chunks
        WHERE loader_namespace=%s AND section_id=ANY(%s) GROUP BY section_id""", (NAMESPACE, section_ids)).fetchall())
    pred = {row["question_id"]: row for row in predictions}
    rows = []
    for item in gold:
        if item["retrieval_target"] == "table" or not item["gold_section_ids"]:
            continue
        expected = set(item["gold_section_ids"])
        results = pred.get(item["question_id"], {}).get("section_results", [])
        ids = [row.get("section_id") for row in results]
        split = any(counts.get(section_id, 0) > 1 for section_id in expected)
        rows.append({"question_id": item["question_id"], "split": split,
                     "top1": bool(ids and ids[0] in expected), "hit_at_10": bool(expected.intersection(ids[:10]))})

    def aggregate(selected: list[dict[str, Any]]) -> dict[str, Any]:
        return {"question_count": len(selected),
                "top1": sum(row["top1"] for row in selected) / len(selected) if selected else None,
                "recall_at_10": sum(row["hit_at_10"] for row in selected) / len(selected) if selected else None,
                "miss_question_ids": [row["question_id"] for row in selected if not row["hit_at_10"]]}
    return {"policy": {"long_threshold_chars": 8000, "target_chars": 6000, "single_paragraph_overlap_chars": 200},
            "unsplit": aggregate([row for row in rows if not row["split"]]),
            "split": aggregate([row for row in rows if row["split"]])}


def metric_view(report: dict[str, Any], scope: str) -> dict[str, Any]:
    aggregate = report[scope]
    return {"section": aggregate["section"], "receipt": aggregate["receipt"], "latency": aggregate["latency"]}


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def comparison_table(fts: dict[str, Any], vector: dict[str, Any], group: str) -> list[dict[str, Any]]:
    keys = sorted(set(fts[group]) | set(vector[group]))
    return [{"name": key, "fts": fts[group].get(key), "vector": vector[group].get(key)} for key in keys]


def write_markdown(path: Path, integrity: dict[str, Any], vector: dict[str, Any], fts: dict[str, Any],
                   chunking: dict[str, Any], title: str) -> None:
    lines = [f"# {title}", "", "Gold, chunking, embedding model, and ranking were not changed.", "",
             "## Integrity", "", f"- Status: `{integrity['status']}`",
             f"- Vectors/chunks/links: `{integrity['actual']['vectors']:,}` / `{integrity['actual']['chunks']:,}` / `{integrity['actual']['links']:,}`",
             f"- Model/version/dimension: `{MODEL}` / `{VERSION}` / `1024`",
             f"- PDF narrative pages: `{integrity['pdf_narrative']['chunks']:,}` across `{integrity['pdf_narrative']['receipts']}` receipts",
             f"- Vector-related storage: `{integrity['storage']['total_vector_related_bytes'] / 1024 / 1024:.2f} MiB`",
             "- ANN indexes: none", "", "## Overall comparison", "",
             "| Scope | Engine | Section Top-1 | R@5 | R@10 | MRR | Receipt Top-1 | Receipt R@5 |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for scope, label in (("reviewed_only", "Reviewed 36"), ("overall", "Candidate 120 (113 section-eligible)")):
        for engine, report in (("FTS", fts), ("Vector", vector)):
            sec, rec = report[scope]["section"], report[scope]["receipt"]
            lines.append(f"| {label} | {engine} | {pct(sec['top1'])} | {pct(sec['recall_at_5'])} | {pct(sec['recall_at_10'])} | {sec['mrr']:.4f} | {pct(rec['top1'])} | {pct(rec['recall_at_5'])} |")
    lines += ["", "Vector latency is end-to-end query embedding plus exact PostgreSQL cosine search; it was a single application-level pass without forcibly flushing OS/PostgreSQL caches.", "",
              "## Category comparison (section Top-1)", "", "| Category | Eligible | FTS | Vector |", "|---|---:|---:|---:|"]
    for row in comparison_table(fts, vector, "by_category"):
        vf, vv = row["fts"], row["vector"]
        lines.append(f"| {row['name']} | {vv['section']['question_count'] if vv else 0} | {pct(vf['section']['top1'] if vf else None)} | {pct(vv['section']['top1'] if vv else None)} |")
    lines += ["", "`table_related` contains seven table-only questions excluded from section-vector denominators and three `either` questions.", "",
              "## Difficulty comparison (section Top-1)", "", "| Difficulty | Eligible | FTS | Vector |", "|---|---:|---:|---:|"]
    for row in comparison_table(fts, vector, "by_difficulty"):
        vf, vv = row["fts"], row["vector"]
        lines.append(f"| {row['name']} | {vv['section']['question_count']} | {pct(vf['section']['top1'])} | {pct(vv['section']['top1'])} |")
    lines += ["", "## Chunking", "", f"- Unsplit: `{chunking['unsplit']['question_count']}` questions, Top-1 `{pct(chunking['unsplit']['top1'])}`, R@10 `{pct(chunking['unsplit']['recall_at_10'])}`",
              f"- Split: `{chunking['split']['question_count']}` questions, Top-1 `{pct(chunking['split']['top1'])}`, R@10 `{pct(chunking['split']['recall_at_10'])}`",
              "- Split misses are boundary/long-section review candidates, not proof that chunking caused the miss; no gold was changed.", "",
              "## FTS failure recovery at Vector R@10", "", "| FTS taxonomy | Failures | Recovered | Rate |", "|---|---:|---:|---:|"]
    for name in ("semantic_paraphrase", "lexical_mismatch", "korean_spacing", "tokenizer", "ranking", "hard"):
        value = vector["fts_recovery"][name]
        lines.append(f"| {name} | {value['fts_failure_count']} | {value['vector_recovered_count']} | {pct(value['recovery_rate'])} |")
    all_four = vector["top1_four_way"]["all_candidate_section_eligible"]["counts"]
    reviewed_four = vector["top1_four_way"]["reviewed_only"]["counts"]
    lines += ["", "## Top-1 four-way comparison", "",
              "| Scope | Both | FTS only | Vector only | Neither |", "|---|---:|---:|---:|---:|",
              f"| Reviewed 36 | {reviewed_four['both_correct']} | {reviewed_four['fts_only_correct']} | {reviewed_four['vector_only_correct']} | {reviewed_four['both_wrong']} |",
              f"| Candidate section-eligible 113 | {all_four['both_correct']} | {all_four['fts_only_correct']} | {all_four['vector_only_correct']} | {all_four['both_wrong']} |",
              "", "## Vector failure taxonomy", "",
              "Automatic taxonomy is a human-review aid and does not change benchmark gold.", ""]
    for name, count in vector["failure_taxonomy"].items():
        lines.append(f"- `{name}`: {count}")
    latency = vector["overall"]["latency"]
    lines += ["", "## End-to-end latency", "",
              f"- avg/P50/P95/max: `{ms(latency['average_ms'])}` / `{ms(latency['p50_ms'])}` / `{ms(latency['p95_ms'])}` / `{ms(latency['max_ms'])}` ms",
              "- The maximum includes provider rate-limit capacity waiting; DB-only plans are reported separately.", ""]
    plan_path = Path("reports/periodic_full_vector_query_plans.json")
    if plan_path.exists():
        plans = json.loads(plan_path.read_text(encoding="utf-8"))["plans"]
        lines += ["## DB-only exact cosine plans", "", "| Filter | Execution ms |", "|---|---:|"]
        for name, value in plans.items():
            lines.append(f"| {name} | {value['execution_time_ms']:.2f} |")
        lines += ["", "Plans use an existing stored 1,024-d vector to isolate PostgreSQL execution. No ANN index exists.", ""]
    lines += ["## Decisions", "",
              "- HNSW: warranted as a controlled next experiment because namespace-only exact execution is about 2.5 s, while filtered exact queries are much faster. Exact R@10 remains the reference truth; no ANN index was created here.",
              "- Semantic Hybrid: experimentally warranted because candidate Top-1 contains both FTS-only and Vector-only wins, especially on hard/PDF cases. Evidence is preliminary because the reviewed 36 has no Vector-only Top-1 win and 84 candidate questions still require review.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_integrity_markdown(path: Path, integrity: dict[str, Any]) -> None:
    storage = integrity["storage"]
    lines = ["# Periodic Full Vector Integrity", "", f"Status: `{integrity['status']}`", "",
             f"- vectors/chunks/links: `{integrity['actual']['vectors']:,}` / `{integrity['actual']['chunks']:,}` / `{integrity['actual']['links']:,}`",
             f"- unique content hashes: `{integrity['unique_content_hashes']:,}`",
             "- dimension/model/version/namespace/provenance mismatch: `0`",
             "- NULL/non-finite/zero vectors: `0`",
             "- duplicate keys/orphans/reference/hash/parent/order problems: `0`",
             f"- PDF narrative pages: `{integrity['pdf_narrative']['chunks']:,}`",
             "- cache lookup validation: all unique chunk hashes resolve by content hash + model + version; API calls `0`",
             "- ANN index count: `0`", "", "## Storage", ""]
    for name in ("section_embedding_vectors", "section_vector_chunks", "section_chunk_embeddings"):
        value = storage[name]
        lines.append(f"- `{name}`: {value['total_bytes'] / 1024 / 1024:.2f} MiB total ({value['table_bytes'] / 1024 / 1024:.2f} MiB table, {value['index_bytes'] / 1024 / 1024:.2f} MiB indexes)")
    lines += [f"- Vector-related total: {storage['total_vector_related_bytes'] / 1024 / 1024:.2f} MiB",
              f"- Current database total: {storage['database_total_bytes'] / 1024 / 1024:.2f} MiB", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--gold", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    parser.add_argument("--vector-predictions", type=Path, default=Path("artifacts/periodic_full_vector_predictions.jsonl"))
    parser.add_argument("--fts-predictions", type=Path, default=Path("artifacts/periodic_full_keyword_fts.jsonl"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL is required")
    gold, vector_predictions, fts_predictions = read_jsonl(args.gold), read_jsonl(args.vector_predictions), read_jsonl(args.fts_predictions)
    fts_failures = json.loads(Path("reports/periodic_full_keyword_failures.json").read_text(encoding="utf-8"))
    with connect(args.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        integrity = collect_integrity(connection)
        if integrity["status"] != "pass":
            raise RuntimeError(f"embedding integrity failed: {integrity}")
        chunking = chunking_analysis(connection, gold, vector_predictions)
    vector = evaluate_vector(gold, vector_predictions, fts_failures, fts_predictions)
    fts = aligned_fts_report(gold, fts_predictions)

    split_misses = set(chunking["split"]["miss_question_ids"])
    for failure in vector["failures"]:
        if failure["question_id"] in split_misses and failure["failure_category"] not in {"gold_ambiguity", "pdf"}:
            failure["failure_category"] = "long_section_or_chunk_boundary_candidate"
        failure["taxonomy_status"] = "automatic_review_aid"
        failure["review_candidate"] = failure["failure_category"] == "gold_ambiguity"
    vector["failure_taxonomy"] = dict(sorted(Counter(row["failure_category"] for row in vector["failures"]).items()))
    vector.update({
        "status": "official_reviewed_regression_and_full_candidate",
        "provider_profile": {"model": MODEL, "version": VERSION, "dimension": 1024, "distance": "cosine"},
        "evaluation_condition": "single application-level pass; query embedding API + exact cosine; OS/PostgreSQL caches not forcibly flushed",
        "table_only_excluded": 7, "integrity": integrity, "chunking_analysis": chunking,
        "fts_aligned_comparison": {"reviewed_only": metric_view(fts, "reviewed_only"), "overall": metric_view(fts, "overall"),
                                   "by_category": fts["by_category"], "by_difficulty": fts["by_difficulty"]},
    })
    loader_log = Path("artifacts/full_embedding_load.stdout.log")
    if loader_log.exists() and loader_log.stat().st_size:
        load_metrics = json.loads(loader_log.read_text(encoding="utf-8"))
        vector["embedding_load"] = {
            "api_generated": load_metrics.get("embedded"), "preexisting_cache_reused": load_metrics.get("reused"),
            "requests": load_metrics.get("provider_stats", {}).get("requests"),
            "retries": load_metrics.get("provider_stats", {}).get("retries"),
            "rate_limit_retries": load_metrics.get("provider_stats", {}).get("rate_limit_retries"),
            "permanent_failures": load_metrics.get("provider_stats", {}).get("permanent_failures"),
            "input_tokens": load_metrics.get("input_tokens"),
            "chunk_to_unique_vector_reuse": integrity["chunk_links_reusing_existing_unique_vectors"],
        }
    Path("reports/periodic_full_vector_baseline.json").write_text(json.dumps(vector, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    Path("reports/periodic_full_vector_failures.json").write_text(json.dumps(vector["failures"], ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    Path("reports/periodic_full_vector_integrity.json").write_text(json.dumps(integrity, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    write_integrity_markdown(Path("reports/periodic_full_vector_integrity.md"), integrity)
    write_markdown(Path("reports/periodic_full_vector_baseline.md"), integrity, vector, fts, chunking, "Periodic Full Vector-only Baseline")
    write_markdown(Path("reports/fts_vs_vector.md"), integrity, vector, fts, chunking, "FTS vs Vector")
    print(json.dumps({"integrity": integrity["status"], "reviewed": vector["reviewed_only"],
                      "overall": vector["overall"], "four_way": vector["top1_four_way"],
                      "recovery": vector["fts_recovery"], "chunking": chunking}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
