"""Build a deterministic, provenance-rich full-corpus benchmark candidate set.

Candidate selection uses corpus metadata and source text only. It never calls a
retrieval function or reads Keyword prediction output. Existing 36 questions
retain their prior manual-review status; new rows require independent human
review before the benchmark can be declared final.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from eval.evaluate_full_retrieval import validate_benchmark
from src.db.load_postgres import connect

VERSION = "periodic-full-v1.0"
NAMESPACE = "periodic-full-v1"
SNAPSHOT = "periodic-full-v1:receipts=1054:sections=82862:tables=1401752"
LEGACY_CATEGORY = {
    "company_section_title": "company_section_title",
    "company_concept": "company_concept",
    "narrative_description": "narrative_description",
    "exact_phrase": "exact_phrase",
    "partial_korean_phrase": "partial_korean_phrase",
    "unicode_spacing_variation": "spacing_or_expression_variation",
    "unicode_nfkc_variation": "spacing_or_expression_variation",
    "year_report_filtered": "year_report_filtered",
    "table_keyword": "table_related",
    "pdf_narrative": "pdf_fallback",
}
TARGET_COUNTS = {
    "company_section_title": 15,
    "company_concept": 15,
    "narrative_description": 20,
    "exact_phrase": 10,
    "partial_korean_phrase": 10,
    "spacing_or_expression_variation": 10,
    "year_report_filtered": 10,
    "ambiguous_similar_sections": 10,
    "table_related": 10,
    "pdf_fallback": 5,
    "difficult_or_ambiguous": 5,
}
TITLE_TOPICS = [
    "배당에 관한 사항", "원재료 및 생산설비", "주요 제품 및 서비스", "위험관리 및 파생거래",
    "주요계약 및 연구개발활동", "이사회에 관한 사항", "우발부채 등에 관한 사항",
    "매출 및 수주상황", "회사의 연혁", "지적재산권", "재무제표 주석",
    "증권의 발행을 통한 자금조달", "임원 및 직원", "대주주 등과의 거래",
    "감사제도", "사업의 개요", "회사의 개요", "감사제도",
    "주주총회", "계열회사", "자기주식", "정관에 관한 사항",
]
SEMANTIC_SPECS = [
    ("해외 통화 가치가 바뀔 때 생기는 재무 위험을 회사가 어떻게 통제하는지 설명한 부분을 찾아줘.", ["외환위험", "위험관리"]),
    ("회사가 보유한 상표·특허 같은 지식재산을 어떻게 관리하는지 설명한 부분을 찾아줘.", ["지적재산권"]),
    ("회사가 집중하고 있는 연구개발 분야와 연구조직을 설명한 부분을 찾아줘.", ["연구개발", "연구소"]),
    ("회사의 핵심 제품군과 각 제품이 어떤 시장을 대상으로 하는지 설명한 부분을 찾아줘.", ["주요 제품"]),
    ("제품 생산에 필요한 핵심 원재료와 가격 변동 요인을 설명한 부분을 찾아줘.", ["원재료", "가격변동"]),
    ("제품이나 서비스를 고객에게 판매하는 조직과 유통 경로를 설명한 부분을 찾아줘.", ["판매조직", "판매경로"]),
    ("주주환원을 위해 배당과 자사주 정책을 어떻게 운영하는지 설명한 부분을 찾아줘.", ["배당정책"]),
    ("이사회가 어떤 위원회로 구성되고 의사결정을 수행하는지 설명한 부분을 찾아줘.", ["이사회", "위원회"]),
    ("현재 진행 중인 소송이 회사 재무에 미칠 불확실성을 설명한 부분을 찾아줘.", ["소송", "예측할 수"]),
    ("온실가스나 배출권 제도에 대응하는 회사의 환경경영 내용을 찾아줘.", ["온실가스", "배출권"]),
    ("게임 사업에 적용되는 규제와 회사가 준수해야 하는 의무를 설명한 부분을 찾아줘.", ["게임산업", "규제"]),
    ("유도무기와 감시정찰 장비 등 방산 제품 구성을 설명한 부분을 찾아줘.", ["유도무기", "감시정찰"]),
    ("조선회사가 건조하는 상선·해양설비·특수선의 종류를 설명한 부분을 찾아줘.", ["LNG", "특수선"]),
    ("식품회사의 소재식품과 가공식품 사업 구성을 설명한 부분을 찾아줘.", ["소재식품", "가공식품"]),
    ("금융회사가 고객 기반과 계열사 협업을 경쟁력으로 활용하는 내용을 찾아줘.", ["고객기반", "계열사"]),
    ("회사가 전자투표를 도입해 주주의 의결권 행사를 지원하는 내용을 찾아줘.", ["전자투표"]),
    ("공급망 운영을 위해 공급자 금융이나 구매 약정을 사용하는 내용을 찾아줘.", ["공급자금융"]),
    ("회사의 생산설비 가동률과 생산능력 산정 방법을 설명한 부분을 찾아줘.", ["생산능력", "가동률"]),
    ("종속회사 변동과 해외 법인을 포함한 연결대상 회사 현황을 설명한 부분을 찾아줘.", ["연결대상", "종속회사"]),
    ("시장·신용·유동성 위험을 식별하고 관리하는 조직과 정책을 설명한 부분을 찾아줘.", ["신용위험", "유동성"]),
    ("회사가 고객 정보와 시스템을 보호하기 위해 운영하는 보안 체계를 설명한 부분을 찾아줘.", ["정보보호"]),
    ("임직원의 안전사고를 줄이기 위한 관리체계와 교육 활동을 설명한 부분을 찾아줘.", ["안전", "교육"]),
    ("회사의 해외 생산·판매 거점과 글로벌 사업 운영 현황을 설명한 부분을 찾아줘.", ["해외", "생산"]),
    ("차입금과 회사채를 통해 자금을 조달한 내역을 설명한 부분을 찾아줘.", ["차입금", "사채"]),
    ("공정가치를 수준별로 구분해 금융상품을 평가하는 기준을 설명한 부분을 찾아줘.", ["공정가치", "수준 3"]),
    ("매출채권 손실 가능성을 반영하기 위한 대손충당금 정책을 설명한 부분을 찾아줘.", ["대손충당금", "매출채권"]),
    ("관계회사와의 거래 및 채권·채무 규모를 설명한 부분을 찾아줘.", ["특수관계자", "채무"]),
    ("사업부문별 매출 비중과 주요 제품 구성을 설명한 부분을 찾아줘.", ["사업부문", "매출"]),
    ("회사가 기후변화와 에너지 사용을 관리하는 정책을 설명한 부분을 찾아줘.", ["기후변화"]),
    ("고객 요구에 맞춰 시스템을 구축하고 운영하는 IT 서비스 사업 특성을 설명한 부분을 찾아줘.", ["시스템 구축", "고객"]),
    ("의약품이나 바이오 제품의 임상 개발 진행 상황을 설명한 부분을 찾아줘.", ["임상", "개발"]),
    ("로봇이나 자동화 기술을 활용한 회사의 신사업 방향을 설명한 부분을 찾아줘.", ["로봇", "자동화"]),
]
TABLE_SPECS = [
    ("이 회사의 채권 발행일과 만기, 발행액을 정리한 표를 찾아줘.", ["발행일", "만기일", "발행액"]),
    ("특수관계자 유형별 거래 상대방을 정리한 표를 찾아줘.", ["특수관계자", "관계기업"]),
    ("주요 제품별 매출액이나 매출 비중을 보여주는 표를 찾아줘.", ["매출액", "비율"]),
    ("원재료별 가격 변동 내역을 비교한 표를 찾아줘.", ["원재료", "가격"]),
    ("임원 구성과 재직 현황을 정리한 표를 찾아줘.", ["성명", "직위"]),
    ("회사의 소송 사건과 소송가액을 정리한 표를 찾아줘.", ["소송", "소송가액"]),
    ("배당금과 주당배당금 지표를 정리한 표를 찾아줘.", ["배당금", "주당"]),
]
PDF_SPECS = [
    (1096, "한화오션이 어떤 종류의 상선과 해양설비, 특수선을 건조하는지 설명한 PDF 페이지를 찾아줘."),
    (1136, "한화오션의 금융상품을 공정가치 범주별로 정리한 PDF 페이지를 찾아줘."),
    (2531, "한화에어로스페이스의 주요 원재료 가격 변동과 산출 기준을 설명한 PDF 페이지를 찾아줘."),
    (201, "KB국민은행이 고객 기반과 계열사 협업을 경쟁력으로 설명한 PDF 페이지를 찾아줘."),
]
HARD_SPECS = [
    ("회사가 예상하지 못한 시장 변화로 자금 지급 능력에 문제가 생기지 않도록 관리하는 내용을 찾아줘.", ["유동성위험"]),
    ("회계상 추정이 달라질 때 금융상품 평가액이 바뀔 수 있음을 설명한 부분을 찾아줘.", ["평가기법", "가정"]),
    ("제품을 직접 만드는 대신 고객 요구에 맞춘 기술 인력과 장비를 조합해 제공하는 사업을 설명한 부분을 찾아줘.", ["Customization", "기술 인력"]),
    ("장기적인 주주 보상을 위해 이익 배분과 자기주식 활용을 함께 설명한 부분을 찾아줘.", ["주주환원", "자사주"]),
    ("재판 결과를 예측할 수 없어 추정 손실을 부채로 반영한 내용을 찾아줘.", ["예상손실", "충당부채"]),
]


def _rows(cursor: Any) -> list[dict[str, Any]]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


class Builder:
    def __init__(self, connection: Any):
        self.connection = connection
        self.used_sections: set[int] = set()
        self.company_counts: Counter[str] = Counter()

    def section(self, section_id: int) -> dict[str, Any]:
        row = self.connection.execute("""
            SELECT s.section_id,s.receipt_no,s.file_id,s.section_order,s.section_path,
                   s.title_normalized,s.body_text,s.source_locator,s.metadata,
                   f.relative_path,f.sha256,co.company_name,d.receipt_date,d.report_type
            FROM document_sections s JOIN disclosure_files f ON f.file_id=s.file_id
            JOIN disclosures d ON d.receipt_no=s.receipt_no JOIN companies co ON co.company_id=d.company_id
            WHERE s.section_id=%s AND s.metadata->>'loader_namespace'=%s
        """, (section_id, NAMESPACE)).fetchone()
        if not row:
            raise LookupError(f"missing section {section_id}")
        names = ["section_id","receipt_no","file_id","section_order","section_path","title","body_text",
                 "source_locator","metadata","relative_path","sha256","company","receipt_date","report_type"]
        return dict(zip(names, row))

    def pick(self, *, title: str | None = None, terms: list[str] | None = None, seed: str,
             kind: str | None = "narrative", report_type: str | None = None) -> dict[str, Any]:
        clauses = ["s.metadata->>'loader_namespace'=%s",
                   "s.title_normalized IS NOT NULL", "length(s.body_text)>20"]
        params: list[Any] = [NAMESPACE]
        if kind:
            clauses.append("s.metadata->>'section_kind'=%s")
            params.append(kind)
        if report_type:
            clauses.append("d.report_type=%s")
            params.append(report_type)
        if title:
            clauses.append("s.title_normalized ILIKE '%%' || %s || '%%'")
            params.append(title)
        for term in terms or []:
            clauses.append("s.body_text ILIKE '%%' || %s || '%%'")
            params.append(term)
        params.append(seed)
        with self.connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT s.section_id FROM document_sections s
                JOIN disclosures d USING(receipt_no) JOIN companies co USING(company_id)
                WHERE {' AND '.join(clauses)}
                ORDER BY md5(s.receipt_no || ':' || s.section_order::text || %s)
                LIMIT 400
            """, tuple(params))
            ids = [row[0] for row in cursor.fetchall()]
        candidates = [self.section(section_id) for section_id in ids if section_id not in self.used_sections]
        if not candidates:
            raise LookupError(f"no candidate for {seed}")
        candidates.sort(key=lambda row: self.company_counts[row["company"]])
        chosen = candidates[0]
        self.used_sections.add(chosen["section_id"])
        self.company_counts[chosen["company"]] += 1
        return chosen

    def provenance(self, section: dict[str, Any], table: dict[str, Any] | None = None) -> dict[str, Any]:
        metadata = section["metadata"]
        result = {
            "receipt_no": section["receipt_no"],
            "file_id": section["file_id"],
            "source_file": section["relative_path"],
            "source_sha256": str(section["sha256"]).strip(),
            "source_locator": table["source_locator"] if table else section["source_locator"],
            "section_source_locator": section["source_locator"],
            "section_id": section["section_id"],
            "section_order": section["section_order"],
            "section_key": metadata.get("section_key"),
            "section_path": section["section_path"],
        }
        if table:
            result["table_id"] = table["table_id"]
            result["table_archive_identifier"] = table["archive_identifier"]
        return result

    def make_row(self, qid: str, category: str, question: str, sections: list[dict[str, Any]], *,
                 difficulty: str, target: str = "section", tables: list[dict[str, Any]] | None = None,
                 split: str = "development", review: str = "needs_review",
                 company_required: bool = True, year_required: bool = False,
                 report_filter: str | None = None, date_from: str | None = None,
                 date_to: str | None = None, notes: str = "") -> dict[str, Any]:
        tables = tables or []
        first = sections[0]
        provenance = [self.provenance(section) for section in sections]
        provenance.extend(self.provenance(self.section(table["section_id"]), table) for table in tables)
        return {
            "question_id": qid,
            "benchmark_version": VERSION,
            "category": category,
            "question": question,
            "difficulty": difficulty,
            "retrieval_target": target,
            "company_filter_required": company_required,
            "company": first["company"],
            "year_filter_required": year_required,
            "year": first["receipt_date"].year if year_required else None,
            "date_from": date_from,
            "date_to": date_to,
            "report_type_filter": report_filter,
            "receipt_no_filter": first["receipt_no"] if target == "pdf_page" else None,
            "gold_receipt_nos": sorted({section["receipt_no"] for section in sections} | {table["receipt_no"] for table in tables}),
            "gold_companies": sorted({section["company"] for section in sections}),
            "gold_years": sorted({section["receipt_date"].year for section in sections}),
            "gold_report_types": sorted({section["report_type"] for section in sections}),
            "gold_section_ids": [section["section_id"] for section in sections],
            "gold_table_ids": [table["table_id"] for table in tables],
            "gold_source_locators": [item["source_locator"] for item in provenance],
            "gold_type": "page" if target == "pdf_page" else ("table" if target == "table" else ("section_or_table" if target == "either" else "section")),
            "gold_file_id": first["file_id"],
            "gold_provenance": provenance,
            "human_review_status": review,
            "review_notes": notes or ("Previously manually reviewed regression gold." if review == "reviewed" else "AI-curated stratified candidate; independent human review is still required."),
            "loader_namespace": NAMESPACE,
            "corpus_snapshot": SNAPSHOT,
            "split": split,
            "source_type": first["metadata"].get("source_type", "xml"),
        }

    def matching_tables(self, section: dict[str, Any], query: str) -> list[dict[str, Any]]:
        table_ids = section["metadata"].get("table_ids") or []
        if not table_ids:
            return []
        rows = self.connection.execute("""
            SELECT table_id,receipt_no,section_id,source_locator,archive_identifier
            FROM retrieval_tables WHERE table_id=ANY(%s)
            AND searchable_text ILIKE '%%' || %s || '%%' ORDER BY table_order LIMIT 5
        """, (table_ids, query.replace(" ", "%"))).fetchall()
        names = ["table_id","receipt_no","section_id","source_locator","archive_identifier"]
        return [dict(zip(names, row)) for row in rows]

    def pick_table(self, terms: list[str], seed: str) -> tuple[dict[str, Any], dict[str, Any]]:
        clauses = ["t.metadata->>'loader_namespace'=%s", "length(t.searchable_text) BETWEEN 50 AND 12000"]
        params: list[Any] = [NAMESPACE]
        for term in terms:
            clauses.append("t.searchable_text ILIKE '%%' || %s || '%%'")
            params.append(term)
        params.append(seed)
        with self.connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT t.table_id,t.receipt_no,t.section_id,t.source_locator,t.archive_identifier
                FROM retrieval_tables t JOIN document_sections s ON s.section_id=t.section_id
                WHERE {' AND '.join(clauses)}
                ORDER BY md5(t.table_id || %s) LIMIT 200
            """, tuple(params))
            names = ["table_id","receipt_no","section_id","source_locator","archive_identifier"]
            candidates = [dict(zip(names, row)) for row in cursor.fetchall()]
        candidates = [row for row in candidates if row["section_id"] not in self.used_sections]
        if not candidates:
            raise LookupError(f"no table candidate for {seed}")
        table = candidates[0]
        section = self.section(table["section_id"])
        self.used_sections.add(section["section_id"])
        self.company_counts[section["company"]] += 1
        return section, table


def legacy_rows(builder: Builder, legacy_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with legacy_path.open(encoding="utf-8-sig") as handle:
        legacy = [json.loads(line) for line in handle if line.strip()]
    for item in legacy:
        sections = [builder.section(int(section_id)) for section_id in item["expected_section_ids"]]
        builder.used_sections.update(section["section_id"] for section in sections)
        builder.company_counts.update(section["company"] for section in sections)
        category = LEGACY_CATEGORY[item["query_type"]]
        target = "pdf_page" if category == "pdf_fallback" else ("either" if category == "table_related" else "section")
        tables: list[dict[str, Any]] = []
        if target == "either":
            tables = builder.matching_tables(sections[0], item["query"])
        difficulty = "easy" if category in {"company_section_title", "exact_phrase"} else ("hard" if category == "pdf_fallback" else "medium")
        rows.append(builder.make_row(
            f"full-{item['question_id']}", category, item["query"], sections,
            difficulty=difficulty, target=target, tables=tables, split="regression",
            review="reviewed", company_required=bool(item.get("company")),
            year_required=False, report_filter=item.get("report_type"),
            date_from=item.get("date_from"), date_to=item.get("date_to"),
        ))
    return rows


def build(database_url: str, legacy_path: Path) -> list[dict[str, Any]]:
    with connect(database_url) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        builder = Builder(connection)
        rows = legacy_rows(builder, legacy_path)
        sequence = Counter(row["category"] for row in rows)

        def add(category: str, question: str, section: dict[str, Any], difficulty: str, **kwargs: Any) -> None:
            sequence[category] += 1
            split = "held_out" if sequence[category] % 3 == 0 else "development"
            rows.append(builder.make_row(
                f"full-{category}-{sequence[category]:03d}", category, question, [section],
                difficulty=difficulty, split=split, **kwargs,
            ))

        while sequence["company_section_title"] < TARGET_COUNTS["company_section_title"]:
            topic = TITLE_TOPICS[sequence["company_section_title"] % len(TITLE_TOPICS)]
            section = builder.pick(title=topic, kind=None, seed=f"title-{sequence['company_section_title']}")
            add("company_section_title", f"{section['company']}의 ‘{topic}’ 관련 항목을 찾아줘.", section, "easy")

        semantic_index = 0
        for category, target in (("company_concept", 15), ("narrative_description", 20)):
            while sequence[category] < target:
                question, terms = SEMANTIC_SPECS[semantic_index % len(SEMANTIC_SPECS)]
                semantic_index += 1
                section = builder.pick(terms=terms, seed=f"{category}-{semantic_index}")
                add(category, f"{section['company']}에서 {question}", section, "medium", company_required=True)

        for category, target, style in (
            ("exact_phrase", 10, "exact"),
            ("partial_korean_phrase", 10, "partial"),
            ("spacing_or_expression_variation", 10, "spacing"),
        ):
            while sequence[category] < target:
                topic = TITLE_TOPICS[(sequence[category] * 3 + len(rows)) % len(TITLE_TOPICS)]
                section = builder.pick(title=topic, kind=None, seed=f"{category}-{sequence[category]}")
                title = section["title"]
                if style == "exact":
                    question = f"{section['company']} 보고서에서 ‘{title}’ 항목을 찾아줘."
                    difficulty = "easy"
                elif style == "partial":
                    phrase = re.sub(r"^(?:[IVX]+|\d+(?:-\d+)*|[가-하]|\([^)]+\))[.．)]?\s*", "", title).strip()
                    phrase = phrase[: max(4, min(10, len(phrase)))]
                    question = f"{section['company']} 공시 제목에서 {phrase} 부분만 기억나는데 해당 내용을 찾아줘."
                    difficulty = "medium"
                else:
                    phrase = re.sub(r"^(?:[IVX]+|\d+(?:-\d+)*|[가-하]|\([^)]+\))[.．)]?\s*", "", title).strip()
                    phrase = phrase[0] + " " + phrase[1:] if len(phrase) > 1 else phrase
                    question = f"{section['company']} 공시에서 ‘{phrase}’처럼 띄어 쓴 항목을 찾아줘."
                    difficulty = "medium"
                add(category, question, section, difficulty)

        while sequence["year_report_filtered"] < TARGET_COUNTS["year_report_filtered"]:
            topic = TITLE_TOPICS[(sequence["year_report_filtered"] * 5) % len(TITLE_TOPICS)]
            desired_report_type = ("annual", "half", "quarter")[sequence["year_report_filtered"] % 3]
            section = builder.pick(title=topic, kind=None, report_type=desired_report_type, seed=f"year-{sequence['year_report_filtered']}")
            year = section["receipt_date"].year
            add(
                "year_report_filtered",
                f"{section['company']}의 {year}년 {topic} 내용을 찾아줘.",
                section,
                "medium",
                year_required=True,
                date_from=f"{year}-01-01",
                date_to=f"{year}-12-31",
                report_filter=section["report_type"],
            )

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT co.company_name,s.title_normalized,array_agg(s.section_id ORDER BY d.receipt_date,s.section_id)
                FROM document_sections s JOIN disclosures d USING(receipt_no) JOIN companies co USING(company_id)
                WHERE s.metadata->>'loader_namespace'=%s AND s.metadata->>'section_kind'<>'hierarchy_only'
                  AND s.title_normalized IS NOT NULL
                GROUP BY 1,2 HAVING count(DISTINCT s.receipt_no) BETWEEN 2 AND 8
                ORDER BY md5(co.company_name || s.title_normalized)
                LIMIT 200
            """, (NAMESPACE,))
            ambiguous_groups = cursor.fetchall()
        for company, title, section_ids in ambiguous_groups:
            if sequence["ambiguous_similar_sections"] >= TARGET_COUNTS["ambiguous_similar_sections"]:
                break
            sections = [builder.section(section_id) for section_id in section_ids]
            if any(section["section_id"] in builder.used_sections for section in sections):
                continue
            builder.used_sections.update(section["section_id"] for section in sections)
            sequence["ambiguous_similar_sections"] += 1
            rows.append(builder.make_row(
                f"full-ambi-{sequence['ambiguous_similar_sections']:03d}",
                "ambiguous_similar_sections",
                f"{company} 공시에서 ‘{title}’에 해당하는 내용을 찾아줘. 특정 연도 하나로 한정하지 않아.",
                sections,
                difficulty="hard",
                split="held_out" if sequence["ambiguous_similar_sections"] % 2 else "development",
                review="ambiguous",
                company_required=True,
                notes="Multiple same-company/same-title sections are intentionally relevant; independent review should confirm semantic equivalence.",
            ))

        for question, terms in TABLE_SPECS:
            section, table = builder.pick_table(terms, f"table-{sequence['table_related']}")
            sequence["table_related"] += 1
            rows.append(builder.make_row(
                f"full-tabl-{sequence['table_related']:03d}", "table_related",
                f"{section['company']}에서 {question}", [section], tables=[table],
                difficulty="medium", target="table",
                split="held_out" if sequence["table_related"] % 3 == 0 else "development",
                company_required=False,
            ))

        for section_id, question in PDF_SPECS:
            section = builder.section(section_id)
            add("pdf_fallback", question, section, "hard", target="pdf_page", company_required=True)

        for question, terms in HARD_SPECS:
            section = builder.pick(terms=terms, seed=f"hard-{sequence['difficult_or_ambiguous']}")
            add("difficult_or_ambiguous", f"{section['company']}에서 {question}", section, "hard")

        counts = Counter(row["category"] for row in rows)
        if counts != Counter(TARGET_COUNTS):
            raise AssertionError(f"category mismatch: {counts}")
        if len(rows) != 120:
            raise AssertionError(f"expected 120 rows, got {len(rows)}")
        validate_benchmark(rows)
        return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--legacy", type=Path, default=Path("eval/periodic_keyword_questions.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("eval/periodic_full_benchmark.jsonl"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    rows = build(args.database_url, args.legacy)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"questions": len(rows), "categories": Counter(row["category"] for row in rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
