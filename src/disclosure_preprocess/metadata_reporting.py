"""Data-driven reports for Phase 3 metadata validation and handoff."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _display(value: object) -> str:
    if value is None:
        return "[null]"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _escape(value: object) -> str:
    return (
        _display(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _table(headers: Sequence[object], rows: Sequence[Sequence[object]]) -> list[str]:
    if not rows:
        return ["_해당 항목 없음_", ""]
    lines = [
        "| " + " | ".join(_escape(item) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_escape(item) for item in row) + " |" for row in rows
    )
    lines.append("")
    return lines


def render_metadata_report(summary: Mapping[str, Any]) -> str:
    run = _mapping(summary.get("run"))
    source = _mapping(summary.get("source"))
    corpus = _mapping(summary.get("corpus"))
    joins = _mapping(summary.get("joins"))
    completeness = _mapping(summary.get("completeness"))
    uniqueness = _mapping(summary.get("uniqueness"))
    validation = _mapping(summary.get("validation"))
    mismatches = _mapping(summary.get("mismatches"))
    canonicalization = _mapping(summary.get("canonicalization"))
    sample = _mapping(summary.get("sample"))
    safe_filters = _mapping(summary.get("safe_filter_fields"))
    gates = _mapping(summary.get("quality_gates"))

    lines: list[str] = [
        "# DART Metadata Normalization, Validation & Data Contract 결과 보고서",
        "",
        f"- metadata schema version: {_display(summary.get('schema_version'))}",
        f"- Phase 2 content schema version: {_display(summary.get('content_schema_version'))}",
        f"- 시작 시각(UTC): {_display(run.get('started_at_utc'))}",
        f"- 종료 시각(UTC): {_display(run.get('finished_at_utc'))}",
        f"- 실행 시간: {_display(run.get('duration_seconds'))}초",
        f"- Python: {_display(run.get('python_version'))}",
        f"- canonical metadata source: {_display(source.get('canonical_metadata_source'))}",
        "",
        "## 1. Corpus와 join 검증",
        "",
        (
            f"manifest **{_display(joins.get('manifest_document_count'))}건**, Phase 2 content "
            f"**{_display(joins.get('parsed_document_count'))}건**, Phase 3 metadata "
            f"**{_display(joins.get('metadata_record_count'))}건**을 `doc_id`로 조인했다."
        ),
        "",
    ]
    lines.extend(
        _table(
            ["join/check", "건수"],
            [
                ["matched", joins.get("matched_count", 0)],
                ["manifest without content", joins.get("manifest_without_content_count", 0)],
                ["content without manifest", joins.get("content_without_manifest_count", 0)],
                ["doc_id duplicate", joins.get("duplicate_doc_id_count", 0)],
                ["rcept_no duplicate", joins.get("duplicate_rcept_no_count", 0)],
                ["metadata/content link inconsistency", joins.get("link_inconsistency_count", 0)],
                ["main content missing", joins.get("content_missing_count", 0)],
            ],
        )
    )

    lines.extend(["## 2. Metadata validation status", ""])
    lines.extend(
        _table(
            ["status", "문서 수"],
            [[key, value] for key, value in _mapping(validation.get("status_counts")).items()],
        )
    )
    lines.extend(
        _table(
            ["Phase 2 parse status", "문서 수"],
            [[key, value] for key, value in _mapping(corpus.get("parse_status_counts")).items()],
        )
    )
    lines.append(
        "Phase 2 `partial`은 metadata 제거 사유가 아니며 "
        f"검증된 {_display(corpus.get('metadata_record_count'))}건 모두 sidecar에 유지했다."
    )
    lines.append("")

    lines.extend(["## 3. Field completeness와 형식", ""])
    completeness_rows: list[list[object]] = []
    for field, raw in completeness.items():
        item = _mapping(raw)
        completeness_rows.append(
            [
                field,
                item.get("non_null_count", 0),
                item.get("null_count", 0),
                item.get("empty_count", 0),
                item.get("whitespace_only_count", 0),
                item.get("unique_value_count", 0),
                item.get("format_violation_count", 0),
                item.get("nfc_normalization_needed_count", 0),
            ]
        )
    lines.extend(
        _table(
            ["field", "non-null", "null", "empty", "whitespace-only", "unique", "format 위반", "NFC 필요"],
            completeness_rows,
        )
    )

    lines.extend(["## 4. Uniqueness", ""])
    lines.extend(
        _table(
            ["field", "non-null", "unique", "duplicate occurrences", "해석"],
            [
                [
                    field,
                    _mapping(item).get("non_null_count", 0),
                    _mapping(item).get("unique_value_count", 0),
                    _mapping(item).get("duplicate_occurrence_count", 0),
                    _mapping(item).get("interpretation", ""),
                ]
                for field, item in uniqueness.items()
            ],
        )
    )

    lines.extend(["## 5. SOURCE_METADATA_MISMATCH 재검증", ""])
    lines.append(
        f"Phase 2 baseline **{_display(mismatches.get('phase2_occurrence_count'))}회 / "
        f"{_display(mismatches.get('document_count'))}문서**를 Phase 3 taxonomy로 재분류했다."
    )
    lines.append("")
    lines.extend(
        _table(
            ["canonical field", "발생 수"],
            [[key, value] for key, value in _mapping(mismatches.get("counts_by_field")).items()],
        )
    )
    lines.extend(
        _table(
            ["reason code", "발생 수"],
            [[key, value] for key, value in _mapping(mismatches.get("counts_by_reason")).items()],
        )
    )
    lines.extend(
        _table(
            ["severity", "발생 수"],
            [[key, value] for key, value in _mapping(mismatches.get("counts_by_severity")).items()],
        )
    )
    lines.append(
        f"고유 canonical 회사는 **{_display(mismatches.get('canonical_company_count'))}개**, "
        f"고유 source 표기는 **{_display(mismatches.get('source_company_variant_count'))}개**다. "
        f"여러 canonical 회사로 매핑된 source 표기는 **{_display(mismatches.get('ambiguous_source_alias_count'))}개**다."
    )
    lines.append("")

    example_rows: list[list[object]] = []
    for item in _sequence(mismatches.get("examples")):
        example = _mapping(item)
        example_rows.append(
            [
                example.get("doc_id"),
                example.get("canonical_field"),
                example.get("canonical_raw"),
                example.get("source_raw"),
                example.get("reason_code"),
                ", ".join(str(value) for value in _sequence(example.get("example_categories"))),
                example.get("pair_occurrence_count"),
                example.get("source_file"),
            ]
        )
    lines.extend(["### 대표 mismatch 사례", ""])
    lines.extend(
        _table(
            [
                "doc_id",
                "field",
                "canonical",
                "source",
                "reason",
                "example category",
                "pair count",
                "source file",
            ],
            example_rows,
        )
    )

    lines.extend(["## 6. Canonicalization 규칙", ""])
    lines.extend(
        [
            f"- authority: `{_display(canonicalization.get('authority'))}`",
            "- 원문 metadata는 observation/mismatch 증거이며 canonical metadata를 덮어쓰지 않는다.",
            "- `rcept_date`는 manifest `rcept_dt`가 strict YYYYMMDD일 때만 YYYY-MM-DD로 표시한다.",
            "- `rcept_no`에서 날짜를 유도하지 않는다.",
            f"- rcept_no prefix와 rcept_dt가 다른 문서: **{_display(canonicalization.get('rcept_date_prefix_difference_count'))}건**",
            f"- corp_name과 listed_name이 다른 문서: **{_display(canonicalization.get('corp_listed_name_difference_count'))}건**",
            "- `is_correction`만 보존하며 correction lineage는 생성하지 않는다.",
            "",
        ]
    )

    lines.extend(["## 7. Safe filter fields", ""])
    filter_rows: list[list[object]] = []
    for field, raw in safe_filters.items():
        item = _mapping(raw)
        filter_rows.append(
            [
                field,
                item.get("type"),
                item.get("nullable"),
                item.get("canonical_source"),
                item.get("filter_allowed"),
                item.get("conditions"),
            ]
        )
    lines.extend(
        _table(
            ["field", "type", "nullable", "source", "filter", "조건/주의"],
            filter_rows,
        )
    )

    lines.extend(["## 8. 역할 2 handoff sample", ""])
    lines.extend(
        [
            f"- sample documents: **{_display(sample.get('sample_count'))}건**",
            f"- selection policy: `{_display(sample.get('selection_policy_version'))}`",
            f"- 원본 line byte 보존: **{_display(sample.get('source_lines_copied_verbatim'))}**",
            f"- quota validation: **{_display(sample.get('quota_validation_passed'))}**",
            "",
        ]
    )
    lines.extend(
        _table(
            ["doc_group", "sample 수"],
            [[key, value] for key, value in _mapping(sample.get("counts_by_doc_group")).items()],
        )
    )
    lines.extend(
        _table(
            ["parse status", "sample 수"],
            [[key, value] for key, value in _mapping(sample.get("counts_by_parse_status")).items()],
        )
    )

    lines.extend(["## 9. 잘못된 metadata 연결 의심", ""])
    suspect_rows = [
        [
            _mapping(item).get("doc_id"),
            _mapping(item).get("code"),
            _mapping(item).get("reason"),
        ]
        for item in _sequence(joins.get("suspected_linkage_documents"))
    ]
    lines.extend(_table(["doc_id", "code", "reason"], suspect_rows))

    lines.extend(["## 10. Quality gates", ""])
    lines.extend(
        _table(
            ["gate", "통과"],
            [[key, value] for key, value in gates.items()],
        )
    )

    lines.extend(["## 11. Downstream 주의사항", ""])
    lines.extend(
        [
            "- `null`은 확인할 수 없음이며 0, 없음, 해당 없음과 같지 않다.",
            "- `corp_name`은 manifest canonical 회사명, `listed_name`은 별도 표시명이다.",
            "- 과거 공시 원문의 법적명·과거명과 현재 manifest canonical 회사명이 다를 수 있다.",
            "- `corp_code`, `stock_code`, `rcept_no`는 숫자로 변환하지 않는다.",
            "- `doc_subtype`, `base_year`, `base_month` filter는 null-aware하게 구현한다.",
            "- `partial` 문서를 자동 제외하지 말고 parse warning을 별도 품질 신호로 사용한다.",
            "- Phase 3는 chunking, DB, embedding, correction lineage를 포함하지 않는다.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_handoff_readme(summary: Mapping[str, Any]) -> str:
    joins = _mapping(summary.get("joins"))
    sample = _mapping(summary.get("sample"))
    validation = _mapping(summary.get("validation"))
    return "\n".join(
        [
            "# Phase 3 Metadata Handoff",
            "",
            "이 디렉터리는 DB·검색·RAG 담당자에게 전달하는 metadata contract와 개발용 sample이다.",
            "Phase 2의 전체 26.6GB content는 복제하지 않고 `../data/processed/parsed_disclosures.jsonl`을 참조한다.",
            "",
            "## 파일",
            "",
            "- `data_contract.md`: 필드·null·filter·provenance 계약",
            "- `metadata_normalized.jsonl`: manifest 기반 canonical metadata sidecar",
            "- `sample_disclosures.jsonl`: Phase 2 원본 line을 그대로 복사한 대표 sample",
            "- `sample_selection.jsonl`: 각 sample의 선정 이유와 원본 line hash",
            "- `handoff_manifest.json`: artifact hash/count/join/quality gate",
            "",
            "## 확정 수치",
            "",
            f"- one-to-one joins: {_display(joins.get('matched_count'))}",
            f"- metadata validation: {_display(validation.get('status_counts'))}",
            f"- sample count: {_display(sample.get('sample_count'))}",
            "",
            "## 사용 순서",
            "",
            "1. `data_contract.md`를 먼저 읽는다.",
            "2. `metadata_normalized.jsonl`을 `doc_id`로 content와 one-to-one join한다.",
            "3. `rcept_no`를 secondary consistency key로 확인한다.",
            "4. 개발은 sample로 시작하되 production 적재는 full content를 streaming 처리한다.",
            "",
            "정정공시 lineage, chunking, embedding, DB schema는 이 handoff에 포함되지 않는다.",
            "",
        ]
    )


__all__ = ["render_handoff_readme", "render_metadata_report"]
