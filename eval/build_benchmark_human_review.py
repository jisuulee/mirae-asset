"""Build a static, leakage-conscious review package for non-reviewed benchmark rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import read_jsonl
from eval.validate_full_benchmark import validate_database
from src.db.load_postgres import connect


REVIEW_DECISIONS = (
    "ACCEPT", "ACCEPT_MULTI_GOLD", "CHANGE_GOLD", "REWRITE_QUESTION",
    "CHANGE_TARGET", "AMBIGUOUS_KEEP", "DROP",
)
REVIEW_FIELDS = {
    "review_decision": None, "reviewer_notes": None,
    "accepted_gold_receipts": [], "accepted_gold_sections": [], "accepted_gold_tables": [],
    "accepted_pdf_pages": [], "proposed_question": None, "proposed_retrieval_target": None,
}


def _excerpt(text: str | None, question: str, limit: int = 1800) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= limit:
        return value
    terms = [term for term in re.findall(r"[0-9A-Za-z가-힣]{2,}", question) if len(term) >= 2]
    positions = [value.find(term) for term in terms if value.find(term) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - limit // 3)
    end = min(len(value), start + limit)
    start = max(0, end - limit)
    return ("…" if start else "") + value[start:end] + ("…" if end < len(value) else "")


def _rank(prediction: dict[str, Any], gold_ids: set[int]) -> int | None:
    results = prediction.get("section_results", prediction.get("results", []))
    return next((index for index, row in enumerate(results, 1) if row.get("section_id") in gold_ids), None)


def _diagnostic(prediction: dict[str, Any], gold_ids: set[int]) -> dict[str, Any]:
    results = prediction.get("section_results", prediction.get("results", []))
    top = results[0] if results else None
    return {
        "gold_rank": _rank(prediction, gold_ids),
        "top1": None if top is None else {
            key: top.get(key) for key in
            ("section_id", "receipt_no", "title", "section_path", "source_locator", "score")
        },
        "error": prediction.get("error"),
    }


def _section_evidence(connection: Any, section_id: int, question: str) -> dict[str, Any] | None:
    row = connection.execute("""
        SELECT s.section_id,s.receipt_no,s.file_id,s.parent_section_id,s.section_order,s.section_path,
               s.title_raw,s.title_normalized,s.concept,s.body_text,s.source_locator,
               s.metadata->>'section_key',s.metadata->>'source_type',s.metadata->>'section_kind',
               f.relative_path,f.file_role,f.sha256,d.receipt_date,d.report_name,d.report_type,co.company_name
        FROM document_sections s JOIN disclosure_files f ON f.file_id=s.file_id
        JOIN disclosures d ON d.receipt_no=s.receipt_no JOIN companies co ON co.company_id=d.company_id
        WHERE s.section_id=%s
    """, (section_id,)).fetchone()
    if row is None:
        return None
    keys = ("section_id", "receipt_no", "file_id", "parent_section_id", "section_order", "section_path",
            "title_raw", "title", "concept", "body_text", "source_locator", "section_key", "source_type",
            "section_kind", "source_file", "file_role", "source_sha256", "receipt_date", "report_name",
            "report_type", "company")
    value = dict(zip(keys, row))
    value["evidence_excerpt"] = _excerpt(value.pop("body_text"), question)
    return value


def _table_evidence(connection: Any, table_id: str, question: str) -> dict[str, Any] | None:
    row = connection.execute("""
        SELECT t.table_id,t.receipt_no,t.file_id,t.section_id,t.table_order,t.caption,t.searchable_text,
               t.source_locator,t.archive_path,t.archive_identifier,f.relative_path,f.sha256
        FROM retrieval_tables t JOIN disclosure_files f USING(file_id) WHERE t.table_id=%s
    """, (table_id,)).fetchone()
    if row is None:
        return None
    keys = ("table_id", "receipt_no", "file_id", "section_id", "table_order", "caption", "searchable_text",
            "source_locator", "archive_path", "archive_identifier", "source_file", "source_sha256")
    value = dict(zip(keys, row))
    value["evidence_excerpt"] = _excerpt(value.pop("searchable_text"), question, 1600)
    return value


def _alternatives(connection: Any, gold: dict[str, Any], question: str, excluded: set[int]) -> list[dict[str, Any]]:
    rows = connection.execute("""
        WITH anchor AS (
          SELECT s.section_id,s.receipt_no,s.section_order,s.title_normalized,s.section_path,d.company_id,d.receipt_date,d.report_type
          FROM document_sections s JOIN disclosures d USING(receipt_no) WHERE s.section_id=%s
        ), candidates AS (
          SELECT s.section_id,
            CASE WHEN s.receipt_no=a.receipt_no AND s.title_normalized=a.title_normalized THEN 0
                 WHEN s.receipt_no=a.receipt_no THEN 1
                 WHEN s.title_normalized=a.title_normalized THEN 2 ELSE 3 END priority,
            abs(s.section_order-a.section_order) order_distance
          FROM anchor a JOIN disclosures d ON d.company_id=a.company_id
          JOIN document_sections s ON s.receipt_no=d.receipt_no
          WHERE s.section_id<>ALL(%s) AND s.metadata->>'loader_namespace'='periodic-full-v1'
            AND s.metadata->>'section_kind'='narrative'
            AND (s.title_normalized=a.title_normalized
                 OR (s.receipt_no=a.receipt_no AND abs(s.section_order-a.section_order)<=3)
                 OR (d.receipt_date=a.receipt_date AND d.report_type=a.report_type AND s.section_path=a.section_path))
        )
        SELECT section_id FROM candidates ORDER BY priority,order_distance,section_id LIMIT 3
    """, (gold["section_id"], list(excluded))).fetchall()
    return [evidence for (section_id,) in rows
            if (evidence := _section_evidence(connection, int(section_id), question)) is not None]


def build_cards(connection: Any, rows: list[dict[str, Any]], fts: list[dict[str, Any]],
                vector: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fts_by_id = {row["question_id"]: row for row in fts}
    vector_by_id = {row["question_id"]: row for row in vector}
    provenance = validate_database(connection, rows)
    blocked = {row["question_id"] for row in provenance["mismatches"]}
    cards = []
    potential_multi_gold = potential_target_mismatch = 0
    for item in rows:
        section_evidence = [evidence for section_id in item["gold_section_ids"]
                            if (evidence := _section_evidence(connection, int(section_id), item["question"]))]
        table_evidence = [evidence for table_id in item["gold_table_ids"]
                          if (evidence := _table_evidence(connection, str(table_id), item["question"]))]
        excluded = set(item["gold_section_ids"])
        alternatives = _alternatives(connection, section_evidence[0], item["question"], excluded) if section_evidence else []
        exact_title_alternative = any(
            candidate.get("title") and candidate.get("title") in {gold.get("title") for gold in section_evidence}
            for candidate in alternatives
        )
        potential_multi_gold += exact_title_alternative
        target_bad = ((item["retrieval_target"] == "table" and not table_evidence)
                      or (item["retrieval_target"] in {"section", "pdf_page"} and not section_evidence)
                      or (item["retrieval_target"] == "pdf_page"
                          and any(not str(value).startswith("page:") for value in item["gold_source_locators"])))
        potential_target_mismatch += target_bad
        gold_ids = set(item["gold_section_ids"])
        cards.append({
            "question_id": item["question_id"], "split": item["split"],
            "current_review_status": item["human_review_status"], "review_package_status": "REVIEW_BLOCKED" if item["question_id"] in blocked else "READY",
            "category": item["category"], "difficulty": item["difficulty"], "retrieval_target": item["retrieval_target"],
            "question": item["question"],
            "filters": {key: item.get(key) for key in ("company", "company_filter_required", "year", "year_filter_required",
                        "date_from", "date_to", "report_type_filter", "receipt_no_filter")},
            "current_gold": {key: item.get(key) for key in ("gold_receipt_nos", "gold_section_ids", "gold_table_ids",
                             "gold_source_locators", "gold_type", "gold_file_id", "gold_provenance")},
            "gold_evidence": {"sections": section_evidence, "tables": table_evidence},
            "alternative_evidence": alternatives,
            "automatic_review_hints": {
                "potential_multi_gold": bool(exact_title_alternative),
                "potential_question_ambiguity": item["human_review_status"] == "ambiguous",
                "potential_target_mismatch": bool(target_bad),
                "status": "AUTOMATIC_HINT_NOT_HUMAN_DECISION",
            },
            "retrieval_diagnostic": {
                "warning": "DO NOT USE RETRIEVAL RESULT AS GOLD TRUTH",
                "fts": _diagnostic(fts_by_id.get(item["question_id"], {}), gold_ids),
                "vector": _diagnostic(vector_by_id.get(item["question_id"], {}), gold_ids),
            },
            **REVIEW_FIELDS,
        })
    summary = {
        "review_target_count": len(cards),
        "review_status": dict(sorted(Counter(row["current_review_status"] for row in cards).items())),
        "category": dict(sorted(Counter(row["category"] for row in cards).items())),
        "difficulty": dict(sorted(Counter(row["difficulty"] for row in cards).items())),
        "retrieval_target": dict(sorted(Counter(row["retrieval_target"] for row in cards).items())),
        "stable_provenance": provenance,
        "alternative_candidate_questions": sum(bool(row["alternative_evidence"]) for row in cards),
        "potential_multi_gold": potential_multi_gold,
        "potential_question_ambiguity": sum(row["automatic_review_hints"]["potential_question_ambiguity"] for row in cards),
        "potential_target_mismatch": potential_target_mismatch,
        "diagnostic_note": "Potential counts are automatic review hints, not human decisions or gold changes.",
    }
    return cards, summary


def _html(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    decisions = json.dumps(REVIEW_DECISIONS)
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Periodic Full Benchmark Human Review</title><style>
:root{{--bg:#f4f1ea;--card:#fffdf8;--ink:#20231f;--muted:#666b64;--line:#d8d2c6;--accent:#315b4a;--warn:#8c3b2f}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}}
header{{position:sticky;top:0;z-index:3;background:#243c32;color:white;padding:12px 20px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
header input,header select,button{{font:inherit;padding:8px;border:1px solid var(--line);border-radius:6px}} button{{cursor:pointer;background:white}} #counter{{font-weight:700;margin-right:auto}}
main{{max-width:1120px;margin:20px auto;padding:0 16px}} .card,section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin:14px 0}}
h1,h2,h3{{line-height:1.25}} h1{{font-size:22px;margin:0}} h2{{font-size:18px}} h3{{font-size:15px;color:var(--accent)}}
.meta{{display:flex;gap:8px;flex-wrap:wrap}} .tag{{background:#e8eee9;border-radius:999px;padding:3px 9px}} .question{{font-size:20px;font-weight:650}}
pre{{white-space:pre-wrap;word-break:break-word;background:#f1eee7;padding:12px;border-radius:6px;max-height:420px;overflow:auto}}
.evidence{{border-left:4px solid var(--accent)}} .alternative{{border-left:4px solid #9b7b3b}} details.diagnostic{{border:2px solid var(--warn)}} details.diagnostic summary{{color:var(--warn);font-weight:800}}
.review-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} label{{display:block;font-weight:650}} textarea,input[type=text],.review-grid select{{width:100%;padding:9px;margin-top:4px}}
.warning{{color:var(--warn);font-weight:800}} .hidden{{display:none}} footer{{color:var(--muted);padding:20px 0}} @media(max-width:720px){{.review-grid{{grid-template-columns:1fr}}}}
</style></head><body><header><strong>Human Review</strong><span id="counter"></span>
<button id="prev">Previous</button><button id="next">Next</button>
<select id="category"><option value="">All categories</option></select><select id="difficulty"><option value="">All difficulties</option></select>
<select id="status"><option value="">All statuses</option><option>needs_review</option><option>ambiguous</option></select>
<input id="search" placeholder="question_id search"><button id="export">Export review JSON</button></header>
<main><div id="app"></div><footer>Retrieval diagnostics are collapsed by default and are never gold truth. Progress is stored only in this browser's localStorage until exported.</footer></main>
<script id="review-data" type="application/json">{payload}</script><script>
const DATA=JSON.parse(document.getElementById('review-data').textContent), DECISIONS={decisions};
const UI={{app:document.getElementById('app'),counter:document.getElementById('counter'),prev:document.getElementById('prev'),next:document.getElementById('next'),category:document.getElementById('category'),difficulty:document.getElementById('difficulty'),status:document.getElementById('status'),search:document.getElementById('search'),exportButton:document.getElementById('export')}};
const KEY='periodic-full-benchmark-review-v1'; let saved={{}}; try{{saved=JSON.parse(localStorage.getItem(KEY)||'{{}}')}}catch(error){{console.warn('Ignoring invalid saved review state',error)}} let filtered=[],pos=0;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const pretty=v=>esc(JSON.stringify(v,null,2));
function state(c){{return Object.assign({{}},c,saved[c.question_id]||{{}})}}
function save(c){{const ids=['review_decision','reviewer_notes','accepted_gold_receipts','accepted_gold_sections','accepted_gold_tables','accepted_pdf_pages','proposed_question','proposed_retrieval_target'];let v={{}};ids.forEach(id=>{{let el=document.getElementById(id);if(!el)return;v[id]=id.startsWith('accepted_')?el.value.split(',').map(x=>x.trim()).filter(Boolean).map(x=>/sections|pdf_pages/.test(id)&&/^\\d+$/.test(x)?Number(x):x):el.value||null}});saved[c.question_id]=v;try{{localStorage.setItem(KEY,JSON.stringify(saved))}}catch(error){{console.warn('Review state remains in memory because localStorage is unavailable',error)}}}}
function evidence(x,kind='evidence'){{return `<div class="${{kind}}"><h3>${{esc(x.title||x.caption||'(no title)')}}</h3><div>${{esc(x.company||'')}} · receipt ${{esc(x.receipt_no)}} · section ${{esc(x.section_id||'')}} · ${{esc(x.source_locator)}}</div><div>${{esc(x.section_path||'')}}</div><pre>${{esc(x.evidence_excerpt||'')}}</pre></div>`}}
function render(){{if(!filtered.length){{UI.app.innerHTML='<section>No matching questions.</section>';UI.counter.textContent='Question 0 / 0';return}};pos=Math.max(0,Math.min(pos,filtered.length-1));let c=state(filtered[pos]);UI.counter.textContent=`Question ${{pos+1}} / ${{filtered.length}} · ${{c.question_id}}`;
let sections=c.gold_evidence.sections.map(x=>evidence(x)).join(''),tables=c.gold_evidence.tables.map(x=>evidence(x)).join(''),alts=c.alternative_evidence.map(x=>evidence(x,'alternative')).join('')||'<p>No structural alternative candidate selected.</p>';
UI.app.innerHTML=`<article class="card"><section><h2>Identity</h2><div class="meta"><span class="tag">${{esc(c.question_id)}}</span><span class="tag">split: ${{esc(c.split)}}</span><span class="tag">status: ${{esc(c.current_review_status)}}</span><span class="tag">category: ${{esc(c.category)}}</span><span class="tag">difficulty: ${{esc(c.difficulty)}}</span><span class="tag">target: ${{esc(c.retrieval_target)}}</span><span class="tag">${{esc(c.review_package_status)}}</span></div></section><h2>Question</h2><p class="question">${{esc(c.question)}}</p><section><h2>Filters</h2><pre>${{pretty(c.filters)}}</pre></section><section><h2>Current Gold</h2><pre>${{pretty(c.current_gold)}}</pre></section><section><h2>Gold Evidence</h2>${{sections}}${{tables}}</section><section><h2>Alternative Evidence</h2><p>Structural review candidates only; not proposed gold changes.</p>${{alts}}</section>
<details class="diagnostic"><summary>DO NOT USE RETRIEVAL RESULT AS GOLD TRUTH — diagnostic reference only</summary><pre>${{pretty(c.retrieval_diagnostic)}}</pre></details>
<section><h2>Human Decision</h2><div class="review-grid"><label>Decision<select id="review_decision"><option value="">Select…</option>${{DECISIONS.map(x=>`<option ${{c.review_decision===x?'selected':''}}>${{x}}</option>`).join('')}}</select></label><label>Proposed target<select id="proposed_retrieval_target"><option value="">No change</option>${{['section','table','either','pdf_page'].map(x=>`<option ${{c.proposed_retrieval_target===x?'selected':''}}>${{x}}</option>`).join('')}}</select></label><label>Accepted receipts<input id="accepted_gold_receipts" type="text" value="${{esc((c.accepted_gold_receipts||[]).join(','))}}"></label><label>Accepted sections<input id="accepted_gold_sections" type="text" value="${{esc((c.accepted_gold_sections||[]).join(','))}}"></label><label>Accepted tables<input id="accepted_gold_tables" type="text" value="${{esc((c.accepted_gold_tables||[]).join(','))}}"></label><label>Accepted PDF pages<input id="accepted_pdf_pages" type="text" value="${{esc((c.accepted_pdf_pages||[]).join(','))}}"></label></div><label>Reviewer notes<textarea id="reviewer_notes" rows="4">${{esc(c.reviewer_notes||'')}}</textarea></label><label>Proposed question<textarea id="proposed_question" rows="3">${{esc(c.proposed_question||'')}}</textarea></label><button id="save">Save current card</button></section></article>`;document.getElementById('save').onclick=()=>{{save(c);render()}}}}
function applyFilter(){{let cat=UI.category.value,diff=UI.difficulty.value,st=UI.status.value,q=UI.search.value.trim().toLowerCase();filtered=DATA.cards.filter(c=>(!cat||c.category===cat)&&(!diff||c.difficulty===diff)&&(!st||c.current_review_status===st)&&(!q||c.question_id.toLowerCase().includes(q)));pos=0;render()}}
for(let [element,key] of [[UI.category,'category'],[UI.difficulty,'difficulty']]){{[...new Set(DATA.cards.map(x=>x[key]))].sort().forEach(v=>element.insertAdjacentHTML('beforeend',`<option>${{esc(v)}}</option>`));element.onchange=applyFilter}} UI.status.onchange=applyFilter;UI.search.oninput=applyFilter;
UI.prev.onclick=()=>{{if(filtered.length){{save(state(filtered[pos]));pos=(pos-1+filtered.length)%filtered.length;render()}}}};UI.next.onclick=()=>{{if(filtered.length){{save(state(filtered[pos]));pos=(pos+1)%filtered.length;render()}}}};
UI.exportButton.onclick=()=>{{if(filtered.length)save(state(filtered[pos]));let rows=DATA.cards.map(c=>Object.assign({{question_id:c.question_id}},saved[c.question_id]||{{review_decision:null,reviewer_notes:null,accepted_gold_receipts:[],accepted_gold_sections:[],accepted_gold_tables:[],accepted_pdf_pages:[],proposed_question:null,proposed_retrieval_target:null}}));let blob=new Blob([JSON.stringify(rows,null,2)],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='periodic_full_benchmark_review_export.json';a.click();URL.revokeObjectURL(a.href)}};applyFilter();
</script></body></html>'''


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = ["# Full Benchmark Human Review Package", "",
             "This package prepares evidence for independent human review. It does not change or confirm benchmark gold.", "",
             f"- Review targets: {summary['review_target_count']}",
             f"- needs_review / ambiguous: {summary['review_status'].get('needs_review', 0)} / {summary['review_status'].get('ambiguous', 0)}",
             f"- Stable provenance mismatches: {summary['stable_provenance']['mismatch_count']}",
             f"- Questions with structural alternative candidates: {summary['alternative_candidate_questions']}",
             f"- Potential multi-gold hints: {summary['potential_multi_gold']}",
             f"- Potential ambiguity hints: {summary['potential_question_ambiguity']}",
             f"- Potential target mismatch hints: {summary['potential_target_mismatch']}", "",
             "Potential counts are automatic triage hints, not reviewer decisions.", ""]
    for heading, key in (("Category", "category"), ("Difficulty", "difficulty"), ("Retrieval target", "retrieval_target")):
        lines += [f"## {heading}", ""]
        lines.extend(f"- `{name}`: {count}" for name, count in summary[key].items())
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    parser.add_argument("--fts-predictions", type=Path, default=Path("artifacts/periodic_full_keyword_fts.jsonl"))
    parser.add_argument("--vector-predictions", type=Path, default=Path("artifacts/periodic_full_vector_predictions.jsonl"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/human_review"))
    parser.add_argument("--template", type=Path, default=Path("eval/periodic_full_benchmark_review.jsonl"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("DATABASE_URL is required")
    benchmark_bytes = args.benchmark.read_bytes()
    original_sha256 = hashlib.sha256(benchmark_bytes).hexdigest()
    rows = [row for row in read_jsonl(args.benchmark) if row["human_review_status"] in {"needs_review", "ambiguous"}]
    with connect(args.database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        cards, summary = build_cards(connection, rows, read_jsonl(args.fts_predictions), read_jsonl(args.vector_predictions))
    if len(cards) != 84 or len({row["question_id"] for row in cards}) != 84:
        raise RuntimeError("review package must contain exactly 84 unique questions")
    data = {"package_version": "periodic-full-human-review-v1", "benchmark_sha256": original_sha256,
            "review_decisions": REVIEW_DECISIONS, "summary": summary, "cards": cards}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "review_data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (args.output_dir / "index.html").write_text(_html(data), encoding="utf-8")
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (args.output_dir / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    with args.template.open("w", encoding="utf-8", newline="\n") as handle:
        for item, card in zip(rows, cards):
            handle.write(json.dumps({**item, "review_package_status": card["review_package_status"], **REVIEW_FIELDS},
                                    ensure_ascii=False, separators=(",", ":")) + "\n")
    if hashlib.sha256(args.benchmark.read_bytes()).hexdigest() != original_sha256:
        raise RuntimeError("source benchmark changed while building review package")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
