"""Evaluate HNSW against exact top-10 and immutable benchmark gold."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import evaluate, read_jsonl


def _pct(values: list[float], p: float) -> float | None:
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)] if values else None


def latency(values: list[float]) -> dict[str, Any]:
    return {"count": len(values), "average_ms": statistics.fmean(values) if values else None,
            "p50_ms": _pct(values, .5), "p95_ms": _pct(values, .95),
            "max_ms": max(values) if values else None}


def _prediction(row: dict[str, Any], key: str) -> dict[str, Any]:
    if row.get("excluded"):
        return {"question_id": row["question_id"], "error": "excluded_table_only_target"}
    value = row["exact"] if key == "exact" else row["hnsw"][key]
    return {"question_id": row["question_id"], **value}


def build(gold: list[dict[str, Any]], raw: dict[str, Any]) -> dict[str, Any]:
    by_gold = {row["question_id"]: row for row in gold}
    keys = ["exact", *map(str, raw["experiment"]["ef_search"])]
    evaluations = {key: evaluate(gold, [_prediction(row, key) for row in raw["rows"]]) for key in keys}
    ann: dict[str, Any] = {}
    for key in keys[1:]:
        samples, exact_matches, profiles = [], [], defaultdict(list)
        for row in raw["rows"]:
            if row.get("excluded"):
                continue
            exact_ids = [x["section_id"] for x in row["exact"]["section_results"][:10]]
            ann_ids = [x["section_id"] for x in row["hnsw"][key]["section_results"][:10]]
            samples.append(len(set(exact_ids) & set(ann_ids)) / len(exact_ids) if exact_ids else 1.0)
            exact_matches.append(ann_ids == exact_ids)
            item = by_gold[row["question_id"]]
            profile = ("receipt" if item.get("receipt_no_filter") else
                       "year_report" if item.get("year_filter_required") or item.get("report_type_filter") else
                       "company" if item.get("company_filter_required") else "namespace")
            profiles[profile].append(float(row["hnsw"][key]["latency_ms"]))
        ann[key] = {"ann_recall_at_10_vs_exact": statistics.fmean(samples),
                    "exact_top10_order_match_rate": statistics.fmean(exact_matches),
                    "latency_by_filter": {name: latency(vals) for name, vals in sorted(profiles.items())}}
    all_latencies, filter_latencies = {}, {}
    for key in keys:
        vals, profiles = [], defaultdict(list)
        for row in raw["rows"]:
            if row.get("excluded"): continue
            value = row["exact"] if key == "exact" else row["hnsw"][key]
            elapsed=float(value["latency_ms"]); vals.append(elapsed)
            item=by_gold[row["question_id"]]
            profile=("receipt" if item.get("receipt_no_filter") else
                     "year_report" if item.get("year_filter_required") or item.get("report_type_filter") else
                     "company" if item.get("company_filter_required") else "namespace")
            profiles[profile].append(elapsed)
        all_latencies[key] = latency(vals)
        filter_latencies[key] = {name: latency(values) for name,values in sorted(profiles.items())}
    probe_latency = {"exact": latency([float(x["exact"]["latency_ms"]) for x in raw["namespace_probes"]])}
    probe_recall = {}
    for key in keys[1:]:
        probe_latency[key] = latency([float(x["hnsw"][key]["latency_ms"]) for x in raw["namespace_probes"]])
        samples=[]
        for row in raw["namespace_probes"]:
            exact_ids=[x["section_id"] for x in row["exact"]["section_results"][:10]]
            ann_ids=[x["section_id"] for x in row["hnsw"][key]["section_results"][:10]]
            samples.append(len(set(exact_ids)&set(ann_ids))/len(exact_ids) if exact_ids else 1.0)
        probe_recall[key]=statistics.fmean(samples)
    return {"status": "internal_candidate_experiment", "benchmark_policy": {
                "reviewed_36": "reviewed regression set", "full_120": "internal/candidate development benchmark",
                "gold_changed": False}, "experiment": raw["experiment"], "provider_stats": raw["provider_stats"],
            "evaluations": evaluations, "ann": ann, "db_latency": all_latencies,
            "db_latency_by_filter": filter_latencies,
            "namespace_only_probe_latency": probe_latency,
            "namespace_only_ann_recall_at_10": probe_recall}


def _metric_line(label: str, result: dict[str, Any]) -> str:
    s, r = result["section"], result["receipt"]
    return (f"| {label} | {s['top1']:.2%} | {s['recall_at_5']:.2%} | {s['recall_at_10']:.2%} | "
            f"{s['mrr']:.4f} | {r['top1']:.2%} | {r['recall_at_5']:.2%} | {r['recall_at_10']:.2%} |")


def markdown(report: dict[str, Any], *, build_seconds: float, index_bytes: int,
             projection_bytes: int) -> str:
    lines = ["# Exact vs HNSW Vector experiment", "",
             "> Policy: reviewed 36 is the reviewed regression set; full 120 remains an internal/candidate development benchmark.",
             "> Exact top-10 is ANN reference truth. Benchmark gold, embeddings, chunks, and ranking were unchanged.", "",
             f"- HNSW: cosine, m=16, ef_construction=64; build {build_seconds:.3f}s",
             f"- HNSW index: {index_bytes:,} bytes; projection including indexes/TOAST: {projection_bytes:,} bytes",
             f"- ef_search: {', '.join(map(str, report['experiment']['ef_search']))}; candidate_k: {report['experiment']['candidate_k']}; no cache flush; DB latency excludes query embedding", "",
             "## Reviewed regression (36)", "", "| mode | Section Top-1 | R@5 | R@10 | MRR | Receipt Top-1 | R@5 | R@10 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key, value in report["evaluations"].items(): lines.append(_metric_line(key, value["reviewed_only"]))
    lines += ["", "## Full candidate (120; 113 section-vector eligible)", "",
              "| mode | Section Top-1 | R@5 | R@10 | MRR | Receipt Top-1 | R@5 | R@10 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key, value in report["evaluations"].items(): lines.append(_metric_line(key, value["overall"]))
    lines += ["", "## ANN fidelity and DB latency", "",
              "| ef_search | ANN Recall@10 vs Exact | exact order match | avg ms | P50 | P95 | max |",
              "|---:|---:|---:|---:|---:|---:|---:|"]
    for key, value in report["ann"].items():
        lat = report["db_latency"][key]
        lines.append(f"| {key} | {value['ann_recall_at_10_vs_exact']:.2%} | {value['exact_top10_order_match_rate']:.2%} | {lat['average_ms']:.2f} | {lat['p50_ms']:.2f} | {lat['p95_ms']:.2f} | {lat['max_ms']:.2f} |")
    exact = report["db_latency"]["exact"]
    lines += ["", f"Exact overall DB latency: avg {exact['average_ms']:.2f}ms, P50 {exact['p50_ms']:.2f}ms, P95 {exact['p95_ms']:.2f}ms, max {exact['max_ms']:.2f}ms.",
              "", "Filtered HNSW timings intentionally force the HNSW KNN path. PostgreSQL otherwise chooses metadata-index + exact sort for selective filters; that native exact choice can be faster.", ""]
    best=str(max(map(int, report["ann"])))
    lines += ["## Recommendation", "",
              f"Among tested settings, ef_search={best} preserves the most Exact Top-10 results, but overall ANN Recall@10 is only {report['ann'][best]['ann_recall_at_10_vs_exact']:.2%}.",
              f"Namespace-only probes improve from {report['namespace_only_probe_latency']['exact']['average_ms']:.2f}ms Exact to {report['namespace_only_probe_latency'][best]['average_ms']:.2f}ms HNSW, with {report['namespace_only_ann_recall_at_10'][best]:.2%} ANN Recall@10.",
              "Do not replace Exact globally with this index configuration. Keep Exact for selective metadata filters; ef_search=400 is only the least-lossy experimental namespace-only option. A higher-quality index build must beat Exact Recall@10 before production adoption.", ""]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--gold", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    p.add_argument("--input", type=Path, default=Path("artifacts/periodic_full_hnsw_experiment.json"))
    p.add_argument("--json-output", type=Path, default=Path("reports/periodic_full_hnsw_experiment.json"))
    p.add_argument("--md-output", type=Path, default=Path("reports/periodic_full_hnsw_experiment.md"))
    p.add_argument("--index-build-seconds", type=float, required=True)
    p.add_argument("--index-bytes", type=int, required=True)
    p.add_argument("--projection-bytes", type=int, required=True)
    a = p.parse_args(); report = build(read_jsonl(a.gold), json.loads(a.input.read_text(encoding="utf-8")))
    report["storage"] = {"index_build_seconds": a.index_build_seconds, "hnsw_index_bytes": a.index_bytes,
                         "projection_total_bytes": a.projection_bytes}
    a.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    a.md_output.write_text(markdown(report, build_seconds=a.index_build_seconds, index_bytes=a.index_bytes,
                                    projection_bytes=a.projection_bytes), encoding="utf-8")
    print(json.dumps({"outputs": [str(a.json_output), str(a.md_output)]}, indent=2))


if __name__ == "__main__": main()
