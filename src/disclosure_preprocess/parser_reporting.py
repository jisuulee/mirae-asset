"""Atomic JSON and Markdown reporting for the Phase 2 parser."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _count(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _display(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return str(value)


def _percentage(numerator: object, denominator: object) -> str:
    total = _count(denominator)
    if total <= 0:
        return "N/A"
    return f"{(_count(numerator) / total) * 100:.2f}%"


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
        "| " + " | ".join(_escape(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_escape(cell) for cell in row) + " |" for row in rows
    )
    lines.append("")
    return lines


def _count_rows(value: object) -> list[list[object]]:
    return [[key, count] for key, count in _mapping(value).items()]


def _statuses(*values: object) -> list[str]:
    names: set[str] = set()
    for value in values:
        names.update(str(key) for key in _mapping(value))
    preferred = ["success", "partial", "failed"]
    return [name for name in preferred if name in names] + sorted(
        names - set(preferred)
    )


def _document_example_rows(value: object, limit: int = 10) -> list[list[object]]:
    rows: list[list[object]] = []
    for item in _sequence(value)[:limit]:
        record = _mapping(item)
        rows.append(
            [
                record.get("doc_id", ""),
                record.get("rcept_no", ""),
                record.get("doc_group", ""),
                record.get("report_nm", ""),
            ]
        )
    return rows


def render_parser_report(summary: Mapping[str, Any]) -> str:
    """Render a self-contained Korean parser quality report."""

    run = _mapping(summary.get("run"))
    source = _mapping(summary.get("source"))
    logical = _mapping(summary.get("logical_documents"))
    physical = _mapping(summary.get("physical_files"))
    extraction = _mapping(summary.get("extraction"))
    warnings = _mapping(summary.get("warnings"))
    warning_codes = _mapping(warnings.get("counts_by_code"))
    suspicious = _mapping(summary.get("suspicious_documents"))
    auxiliary = _mapping(summary.get("auxiliary_json"))
    guarantees = _mapping(summary.get("guarantees"))

    manifest_count = _count(logical.get("manifest_document_count"))
    expected_value = logical.get("expected_document_count")
    expected_count = _count(expected_value) if expected_value is not None else None
    target_count = _count(logical.get("processed_target_count"))
    output_count = _count(logical.get("output_line_count"))
    parsed_file_count = _count(physical.get("parsed_file_count"))
    sha_count = _count(physical.get("sha256_count"))
    sha_missing = max(parsed_file_count - sha_count, 0)

    lines: list[str] = [
        "# DART Evidence-Preserving Unified Parser 결과 보고서",
        "",
        f"- schema version: {_display(summary.get('schema_version'))}",
        f"- 시작 시각(UTC): {_display(run.get('started_at_utc'))}",
        f"- 종료 시각(UTC): {_display(run.get('finished_at_utc'))}",
        f"- 실행 시간: {_display(run.get('duration_seconds'))}초",
        f"- Python: {_display(run.get('python_version'))}",
        f"- 입력: {_display(source.get('kind'))} / {_display(source.get('path'))}",
        f"- 논리 루트: {_display(source.get('logical_root'))}",
        "",
        "## 1. 실행 범위와 논리 공시 수",
        "",
        f"manifest 논리 공시는 **{manifest_count:,}건**, 이번 실행 대상은 "
        f"**{target_count:,}건**, JSONL 출력은 **{output_count:,}줄**이다.",
    ]
    if expected_count is None:
        lines.append("별도의 기대 문서 수는 설정되지 않았다.")
    else:
        lines.append(
            f"설정된 요구 기준은 **{expected_count:,}건**이며 manifest 일치 여부는 "
            f"**{_display(logical.get('matches_expected_count', False))}**다."
        )
    if run.get("complete_corpus_parse"):
        lines.append(
            "전체 코퍼스 실행이며 출력 줄 수와 manifest 문서 수의 일치 여부는 "
            f"**{_display(logical.get('matches_manifest_count', False))}**다."
        )
    else:
        selected_ids = _sequence(run.get("document_ids"))
        if selected_ids:
            lines.append(
                f"doc_id 선택 실행({len(selected_ids):,}건)이므로 전체 manifest 일치 "
                "판정은 적용하지 않는다."
            )
        else:
            lines.append(
                f"document_limit={_display(run.get('document_limit'))}인 제한 실행이므로 "
                "전체 manifest 일치 판정은 적용하지 않는다."
            )
    lines.extend(["", "### 논리 공시 parse status", ""])
    lines.extend(_table(["status", "문서 수"], _count_rows(logical.get("status_counts"))))

    lines.extend(["### doc_group별 결과", ""])
    status_by_group = _mapping(logical.get("status_by_doc_group"))
    group_statuses = _statuses(
        logical.get("status_counts"),
        *(_mapping(item) for item in status_by_group.values()),
    )
    group_rows: list[list[object]] = []
    for group, total in _mapping(logical.get("doc_group_counts")).items():
        counts = _mapping(status_by_group.get(group))
        group_rows.append(
            [group, total, *(counts.get(status, 0) for status in group_statuses)]
        )
    lines.extend(_table(["doc_group", "합계", *group_statuses], group_rows))

    lines.extend(
        [
            "## 2. 물리 파일·형식·역할",
            "",
            f"논리 공시를 구성한 물리 파일은 **{parsed_file_count:,}개**, 첨부는 "
            f"**{_count(physical.get('attachment_count')):,}개**다.",
            f"SHA-256 기록은 **{sha_count:,}/{parsed_file_count:,}개**이며 누락은 "
            f"**{sha_missing:,}개**다.",
            "",
            "### content detection 형식별 상태",
            "",
        ]
    )
    status_by_format = _mapping(physical.get("status_by_detected_format"))
    format_statuses = _statuses(
        physical.get("parse_status_counts"),
        *(_mapping(item) for item in status_by_format.values()),
    )
    format_rows: list[list[object]] = []
    for format_name, total in _mapping(physical.get("detected_format_counts")).items():
        counts = _mapping(status_by_format.get(format_name))
        format_rows.append(
            [
                format_name,
                total,
                *(counts.get(status, 0) for status in format_statuses),
            ]
        )
    lines.extend(
        _table(["detected format", "파일 수", *format_statuses], format_rows)
    )
    lines.extend(["### 파일 역할", ""])
    lines.extend(_table(["role", "파일 수"], _count_rows(physical.get("role_counts"))))

    lines.extend(["## 3. title/body/section/table 추출", ""])
    extraction_rows = [
        ["main title 추출 문서", extraction.get("main_title_document_count", 0)],
        [
            "main title 추출 성공률",
            _percentage(extraction.get("main_title_document_count", 0), target_count),
        ],
        ["main title 미추출 문서", extraction.get("main_title_missing_count", 0)],
        ["main body 추출 문서", extraction.get("main_body_document_count", 0)],
        [
            "main non-empty body 비율",
            _percentage(extraction.get("main_body_document_count", 0), target_count),
        ],
        ["main empty body", extraction.get("main_empty_body_count", 0)],
        ["main section 보유 문서", extraction.get("main_section_document_count", 0)],
        ["main section 미추출 문서", extraction.get("main_no_section_count", 0)],
        ["main section 수", extraction.get("main_section_count", 0)],
        ["main table 보유 문서", extraction.get("main_table_document_count", 0)],
        ["main table 수", extraction.get("main_table_count", 0)],
        ["전체 물리 block 수", extraction.get("all_physical_block_count", 0)],
        ["전체 물리 section 수", extraction.get("all_physical_section_count", 0)],
        ["전체 물리 table 수", extraction.get("all_physical_table_count", 0)],
        ["provenance 대상 수", extraction.get("provenance_item_count", 0)],
        ["provenance 누락 수", extraction.get("provenance_missing_count", 0)],
    ]
    lines.extend(_table(["항목", "개수"], extraction_rows))

    lines.extend(
        [
            "## 4. warning codes와 소스 이상",
            "",
            f"구조화 경고는 총 **{_count(warnings.get('occurrence_count')):,}건**이다.",
            "",
        ]
    )
    lines.extend(_table(["warning code", "발생 수"], _count_rows(warning_codes)))
    lines.extend(["### warning severity", ""])
    lines.extend(
        _table(
            ["severity", "발생 수"], _count_rows(warnings.get("counts_by_severity"))
        )
    )
    anomaly_rows = [
        ["PDF fallback", warnings.get("pdf_fallback_count", 0)],
        [
            "인코딩 선언 불일치",
            warning_codes.get("ENCODING_DECLARATION_MISMATCH", 0),
        ],
        [
            "source metadata 불일치",
            warnings.get("source_metadata_mismatch_count", 0),
        ],
        ["source 탐색/읽기 문제", source.get("source_issue_count", 0)],
        ["manifest 미연결 raw", summary.get("unmatched_raw_file_count", 0)],
        ["SHA-256 누락", sha_missing],
    ]
    lines.extend(["### PDF fallback·인코딩·source anomaly", ""])
    lines.extend(_table(["신호", "건수"], anomaly_rows))
    lines.extend(["### source issue 예시", ""])
    lines.extend(
        _table(
            ["stage", "path", "error"],
            [
                [
                    _mapping(item).get("stage", ""),
                    _mapping(item).get("path", ""),
                    _mapping(item).get("error", ""),
                ]
                for item in _sequence(source.get("source_issue_examples"))[:10]
            ],
        )
    )

    lines.extend(
        [
            "## 5. Auxiliary JSON 제외 검증",
            "",
            f"기업별 수집 목록 JSON은 **{_count(auxiliary.get('file_count')):,}개**이며 "
            f"검증된 record 합계는 **{_count(auxiliary.get('record_count')):,}개**다.",
            "이 JSON을 논리 disclosure로 포함했는가? "
            f"**{_display(auxiliary.get('included_as_disclosures', False))}**.",
            "auxiliary JSON은 형식·레코드 검증 대상일 뿐 최종 공시 JSONL에는 포함되지 않는다.",
            "",
        ]
    )
    lines.extend(
        _table(
            ["auxiliary status", "파일 수"],
            _count_rows(auxiliary.get("parse_status_counts")),
        )
    )
    lines.extend(["### auxiliary root type", ""])
    lines.extend(
        _table(["root type", "파일 수"], _count_rows(auxiliary.get("root_type_counts")))
    )

    cause_groups = {
        "source syntax/표기 이상": {
            "BARE_AMPERSAND_PRESERVED",
            "ENCODING_DECLARATION_MISMATCH",
            "MARKUP_RECOVERY_APPLIED",
            "SOURCE_METADATA_MISMATCH",
        },
        "보수적 parser 한계/불확실성": {
            "EMPTY_BODY",
            "MALFORMED_TABLE",
            "PDF_PAGE_EXTRACTION_FAILED",
            "SECTION_HIERARCHY_UNCERTAIN",
            "TABLE_SPAN_NORMALIZATION_FAILED",
            "TITLE_NOT_FOUND",
            "UNSUPPORTED_FORMAT",
        },
        "대체표현·role 선택": {
            "ATTACHMENT_ROLE_UNKNOWN",
            "HTML_VIEWER_NOT_SUBSTANTIVE",
            "MAIN_DOCUMENT_ROLE_UNRESOLVED",
            "PDF_FALLBACK_USED",
        },
        "물리 source/linkage 실패": {
            "PHYSICAL_FILE_READ_FAILED",
            "SOURCE_FILE_COUNT_MISMATCH",
            "SOURCE_FILE_MISSING",
        },
    }
    cause_rows = [
        [label, sum(_count(warning_codes.get(code)) for code in codes)]
        for label, codes in cause_groups.items()
    ]
    lines.extend(
        [
            "### partial/failed 원인 신호 분류",
            "",
            "아래 값은 warning 발생 횟수의 진단 분류이며 문서 수가 아니고 서로 배타적이지 않다. "
            "특히 `MARKUP_RECOVERY_APPLIED`는 원본 XML/HTML 문법 이상을 tolerant parser가 "
            "복구한 source-data 신호이고, `SECTION_HIERARCHY_UNCERTAIN`은 근거 없는 계층 "
            "추론을 하지 않은 parser 제한 신호다.",
            "",
        ]
    )
    lines.extend(_table(["원인 범주", "warning 발생 수"], cause_rows))

    lines.extend(["## 6. suspicious document 예시", ""])
    suspicious_labels = {
        "title_missing_examples": "title 미추출",
        "empty_body_examples": "empty body",
        "no_section_examples": "section 미추출",
        "failed_examples": "failed",
        "partial_examples": "partial",
        "malformed_table_examples": "malformed table/span",
        "unknown_attachment_role_examples": "attachment role unknown",
        "source_metadata_mismatch_examples": "source metadata mismatch",
    }
    found_suspicious = False
    for key, label in suspicious_labels.items():
        examples = suspicious.get(key)
        if not _sequence(examples):
            continue
        found_suspicious = True
        lines.extend([f"### {label}", ""])
        lines.extend(
            _table(
                ["doc_id", "rcept_no", "doc_group", "report_nm"],
                _document_example_rows(examples),
            )
        )
    if not found_suspicious:
        lines.extend(["_해당 항목 없음_", ""])

    parsing_examples = _sequence(summary.get("parsing_examples"))[:5]
    lines.extend(
        [
            "## 7. 대표 파싱 예시",
            "",
            f"실행 통계가 보존한 대표 예시 **{len(parsing_examples):,}건**을 표시한다. "
            "없는 유형의 예시는 임의로 생성하지 않았다.",
            "",
        ]
    )
    lines.extend(
        _table(
            [
                "kind",
                "doc_id",
                "회사",
                "report_nm",
                "format/role",
                "sections",
                "tables",
                "source path",
                "원문 preview",
            ],
            [
                [
                    _mapping(item).get("kind", ""),
                    _mapping(item).get("doc_id", ""),
                    _mapping(item).get("corp_name", ""),
                    _mapping(item).get("report_nm", ""),
                    f"{_mapping(item).get('main_format', '')}/"
                    f"{_mapping(item).get('main_role', '')}",
                    _mapping(item).get("section_count", 0),
                    _mapping(item).get("table_count", 0),
                    _mapping(item).get("source_path", ""),
                    _mapping(item).get("text_preview", ""),
                ]
                for item in parsing_examples
            ],
        )
    )

    status_counts = _mapping(logical.get("status_counts"))
    issue_candidates = [
        ["failed 공시 재현·원인 확인", status_counts.get("failed", 0)],
        ["partial 공시 warning 검토", status_counts.get("partial", 0)],
        ["title 미추출 검토", extraction.get("main_title_missing_count", 0)],
        ["empty body 검토", extraction.get("main_empty_body_count", 0)],
        ["section 미추출 검토", extraction.get("main_no_section_count", 0)],
        [
            "table span/malformed table 검토",
            _count(warning_codes.get("TABLE_SPAN_NORMALIZATION_FAILED"))
            + _count(warning_codes.get("MALFORMED_TABLE")),
        ],
        [
            "unknown attachment role 검토",
            warning_codes.get("ATTACHMENT_ROLE_UNKNOWN", 0),
        ],
        [
            "source metadata mismatch 검토",
            warnings.get("source_metadata_mismatch_count", 0),
        ],
        [
            "PDF fallback의 표 행·열 구조 미보존 검토",
            warnings.get("pdf_fallback_count", 0),
        ],
        ["source 탐색/읽기 문제 해결", source.get("source_issue_count", 0)],
        ["manifest 미연결 raw 분류", summary.get("unmatched_raw_file_count", 0)],
        ["SHA-256 누락 보완", sha_missing],
    ]
    phase_three_issues = [
        [label, _count(value)] for label, value in issue_candidates if _count(value)
    ]
    lines.extend(["## 8. Phase 3 전 확인할 이슈", ""])
    if phase_three_issues:
        lines.extend(_table(["확인 항목", "영향 건수"], phase_three_issues))
    else:
        lines.extend(
            ["현재 요약 통계에서 수치로 확인된 미해결 parser 품질 이슈는 없다.", ""]
        )
    lines.extend(
        [
            "정정공시 계보, RAG chunking, embedding과 의미 추론은 이 parser 단계에서 "
            "수행하지 않았으며 다음 단계에서 별도 정책과 평가가 필요하다.",
            "PDF fallback은 OCR 없이 page text와 page locator만 보존한다. 따라서 해당 "
            "PDF의 표는 행·열/cell 구조로 역추적할 수 없으며, Phase 3 전에 viewer 대체 "
            "또는 별도 PDF table extractor 정책을 검토해야 한다.",
            "",
            "## 9. 근거 보존 보증과 명시적 답변",
            "",
        ]
    )
    lines.extend(
        _table(
            ["보증 항목", "충족"],
            [[key, value] for key, value in guarantees.items()],
        )
    )

    no_generation = guarantees.get("semantic_inference_or_summarization") is False
    traceable = (
        bool(guarantees.get("source_sha256_recorded"))
        and sha_count == parsed_file_count
        and bool(
            guarantees.get(
                "structured_content_provenance_recorded",
                guarantees.get("block_table_provenance_recorded"),
            )
        )
    )
    if no_generation:
        generation_answer = (
            "**아니오.** parser는 원문 추출과 NFC·공백 등 보수적 정규화만 수행하며, "
            "원문에 없는 정보를 생성하지 않고 소스에 없는 사실의 추론·요약·보강을 "
            "수행하지 않는다."
        )
    else:
        generation_answer = (
            "**현재 summary만으로는 아니오라고 보증할 수 없다.** "
            "semantic inference 보증을 확인해야 한다."
        )
    if traceable:
        trace_answer = (
            "**구조화 항목은 예.** title/section/block/list item/table/cell마다 물리 경로와 "
            "파일 역할, DOM-like/source index 또는 PDF page locator가 있고 source 파일의 "
            f"SHA-256 범위는 {sha_count:,}/{parsed_file_count:,}개다. 다만 `body_text`는 "
            "ordered block에서 파생한 검색용 뷰이므로 개별 문자 범위(char offset) 역매핑은 "
            "제공하지 않는다. 문장과 숫자는 원래 block 또는 table cell 단위로 추적해야 한다."
        )
    else:
        trace_answer = (
            "**완전하게 보증할 수 없다.** source SHA-256과 구조화 content provenance가 모두 "
            f"필요하다. SHA-256 범위는 {sha_count:,}/{parsed_file_count:,}개다."
        )
    lines.extend(
        [
            f"1. **소스에 없는 정보가 생성될 가능성이 있는가?** {generation_answer}",
            "",
            f"2. **추출된 숫자·표·문장을 물리 소스까지 역추적할 수 있는가?** {trace_answer}",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise


def write_parser_reports(
    summary: Mapping[str, Any], report_dir: str | Path
) -> dict[str, Path]:
    """Publish the JSON/Markdown pair with rollback on partial replacement."""

    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    summary_path = directory / "parser_summary.json"
    report_path = directory / "parser_report.md"
    json_text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    markdown_text = render_parser_report(summary)
    if not markdown_text.endswith("\n"):
        markdown_text += "\n"
    stage = Path(tempfile.mkdtemp(prefix=".parser-reports-", dir=directory))
    staged = {
        summary_path: stage / summary_path.name,
        report_path: stage / report_path.name,
    }
    backup_dir = stage / "previous-generation"
    backup_dir.mkdir()
    for final_path, staged_path in staged.items():
        text = json_text if final_path == summary_path else markdown_text
        with staged_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())

    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for final_path in staged:
            if final_path.exists() and final_path.is_dir():
                raise ValueError(f"Report artifact path is a directory: {final_path}")
            if final_path.exists() or final_path.is_symlink():
                backup_path = backup_dir / final_path.name
                os.replace(final_path, backup_path)
                backups[final_path] = backup_path
        for final_path, staged_path in staged.items():
            os.replace(staged_path, final_path)
            published.append(final_path)
    except BaseException:
        for final_path in reversed(published):
            final_path.unlink(missing_ok=True)
        for final_path, backup_path in backups.items():
            if backup_path.exists() or backup_path.is_symlink():
                os.replace(backup_path, final_path)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {"parser_summary": summary_path, "parser_report": report_path}


__all__ = ["render_parser_report", "write_parser_reports"]
