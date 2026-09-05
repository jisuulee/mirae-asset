"""Human-readable Markdown rendering for inventory results."""

from __future__ import annotations

from typing import Any


def _escape(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
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


def _count_rows(counts: dict[str, int]) -> list[list[object]]:
    return [[key, value] for key, value in counts.items()]


def _format_bytes(value: int | float | None) -> str:
    if value is None:
        return "-"
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{amount:,.0f} {unit}"
            return f"{amount:,.2f} {unit}"
        amount /= 1024
    return f"{amount:,.2f} TiB"


def _value(summary: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = summary
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def render_inventory_report(summary: dict[str, Any]) -> str:
    """Render a self-contained Korean Phase 1 report."""

    manifest = summary.get("manifest", {})
    physical = summary.get("physical_files", {})
    encoding = summary.get("encoding", {})
    multi = summary.get("periodic_multi_file", {})
    join = summary.get("join_quality", {})
    issues = summary.get("issues", {})
    source = summary.get("source", {})

    manifest_available = bool(manifest.get("available", False))
    document_count = int(manifest.get("document_count", 0))
    raw_file_count = int(physical.get("raw_file_count", 0))
    declared_payload_count = int(manifest.get("declared_physical_file_count", 0))
    linked_value = physical.get("manifest_linked_raw_file_count")
    unlinked_value = physical.get("manifest_unlinked_raw_file_count")
    linked_count = int(linked_value) if linked_value is not None else 0
    unlinked_count = int(unlinked_value) if unlinked_value is not None else 0
    group_listing_count = int(physical.get("group_listing_file_count", 0))
    unmatched_raw_count = int(physical.get("unmatched_raw_file_count", 0))
    mismatch_count = int(physical.get("extension_format_mismatch_count", 0))
    disguised_html_count = int(physical.get("xml_extension_html_count", 0))

    lines: list[str] = [
        "# DART 코퍼스 인벤토리 및 형식 프로파일",
        "",
        f"- 생성 시각(UTC): `{summary.get('generated_at_utc', '')}`",
        f"- 입력 종류: `{source.get('kind', '')}`",
        f"- 입력 경로: `{source.get('location', '')}`",
        f"- 자동 탐지된 논리 루트: `{source.get('logical_root', '')}`",
        f"- content detection prefix: `{source.get('prefix_bytes', 0):,}` bytes",
        "",
        "## 1. 코퍼스 개요",
        "",
    ]

    if manifest_available:
        lines.extend(
            [
                f"manifest의 논리 공시 문서는 **{document_count:,}건**이고, `raw/` 아래 물리 파일은 **{raw_file_count:,}개**다.",
                f"manifest의 `n_files` 합은 **{declared_payload_count:,}개**이며 실제 manifest 연결 파일은 **{linked_count:,}개**다.",
                f"manifest 미연결 raw 파일은 **{unlinked_count:,}개**이며, 이 중 식별된 기업별 수집 목록 JSON은 **{group_listing_count:,}개**, 그 밖의 unmatched raw 파일은 **{unmatched_raw_count:,}개**다.",
                "따라서 논리 문서 수와 raw 디렉터리의 모든 물리 파일 수를 직접 비교하면 보조 파일과 다중 첨부를 함께 세게 된다.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                f"`raw/` 아래 물리 파일은 **{raw_file_count:,}개**지만 manifest를 읽을 수 없어 논리 문서 및 payload 연결 통계는 **측정 불가**다.",
                "raw 파일을 manifest 미연결 보조 파일로 간주하지 않았으며, 아래 문서 통계는 불완전하다.",
                "",
            ]
        )

    overview_rows = [
        ["논리 공시 문서(manifest)", f"{document_count:,}"],
        ["manifest `n_files` 합", f"{declared_payload_count:,}" if manifest_available else "측정 불가"],
        ["manifest 연결 raw 파일", f"{linked_count:,}" if manifest_available else "측정 불가"],
        ["manifest 미연결 raw 파일", f"{unlinked_count:,}" if manifest_available else "측정 불가"],
        ["기업별 group listing", f"{group_listing_count:,}"],
        ["기타 unmatched raw", f"{unmatched_raw_count:,}" if manifest_available else "측정 불가"],
        ["전체 raw 파일", f"{raw_file_count:,}"],
        ["dataset 보조 파일(raw 외부)", f"{physical.get('auxiliary_file_count', 0):,}"],
    ]
    lines.extend(_table(["구분", "개수"], overview_rows))
    if manifest_available:
        lines.extend(["### manifest 미연결 raw 보조 파일", ""])
        lines.extend(
            _table(
                ["파일명", "개수"],
                _count_rows(physical.get("unlinked_raw_filename_counts", {})),
            )
        )

    lines.extend(["## 2. manifest 문서 통계", ""])
    lines.extend(
        _table(
            ["doc_group", "문서 수"],
            _count_rows(manifest.get("doc_group_counts", {})),
        )
    )
    lines.extend(["### doc_subtype", ""])
    lines.extend(
        _table(
            ["doc_subtype", "문서 수"],
            _count_rows(manifest.get("doc_subtype_counts", {})),
        )
    )
    lines.extend(["### 정정 여부", ""])
    lines.extend(
        _table(
            ["구분", "문서 수"],
            _count_rows(manifest.get("correction_counts", {})),
        )
    )
    lines.extend(["### manifest file_format", ""])
    lines.extend(
        _table(
            ["file_format", "문서 수"],
            _count_rows(manifest.get("file_format_counts", {})),
        )
    )
    lines.extend(["### n_files 분포", ""])
    lines.extend(
        _table(
            ["n_files", "문서 수"],
            _count_rows(manifest.get("n_files_distribution", {})),
        )
    )

    lines.extend(["### metadata 일관성", ""])
    lines.extend(
        _table(
            ["manifest 무결성 항목", "건수"],
            [
                ["malformed JSONL line", manifest.get("malformed_line_count", 0)],
                ["duplicate file_path", manifest.get("duplicate_file_path_count", 0)],
                ["duplicate doc_id", manifest.get("duplicate_doc_id_count", 0)],
                ["duplicate rcept_no", manifest.get("duplicate_rcept_no_count", 0)],
                ["unsafe/invalid file_path", manifest.get("invalid_file_path_count", 0)],
            ],
        )
    )
    lines.extend(
        [
            f"`corp_name`과 `listed_name`이 다른 레코드는 **{manifest.get('corp_name_listed_name_mismatch_count', 0):,}건**이다. 실제 폴더 join key는 `corp_name`이므로 `listed_name`으로 경로를 구성하면 안 된다.",
            "",
        ]
    )
    lines.extend(
        _table(
            ["corp_name → listed_name", "문서 수"],
            _count_rows(manifest.get("corp_name_listed_name_mismatch_pairs", {})),
        )
    )
    lines.extend(
        [
            f"`rcept_no`의 앞 8자리와 명시적 `rcept_dt`가 다른 레코드는 **{manifest.get('rcept_date_prefix_mismatch_count', 0):,}건**이다. 날짜는 접수번호에서 유도하지 않고 `rcept_dt`를 보존해야 한다.",
            "",
        ]
    )
    lines.extend(
        _table(
            ["doc_id", "rcept_no", "rcept_dt"],
            [
                [
                    item.get("doc_id", ""),
                    item.get("rcept_no", ""),
                    item.get("rcept_dt", ""),
                ]
                for item in manifest.get("rcept_date_prefix_mismatch_examples", [])
            ],
        )
    )

    company_counts = manifest.get("company_document_counts", {})
    lines.extend(
        [
            f"### 기업별 문서 수 (상위 20개 / 총 {manifest.get('company_count', 0):,}개 기업)",
            "",
        ]
    )
    lines.extend(
        _table(
            ["기업", "문서 수"],
            _count_rows(dict(list(company_counts.items())[:20])),
        )
    )
    lines.append("전체 기업별 수치는 `inventory_summary.json`에 수록했다.")
    lines.append("")

    lines.extend(["## 3. 물리 파일 통계", "", "### 확장자 기준", ""])
    lines.extend(
        _table(
            ["확장자", "파일 수"],
            _count_rows(physical.get("extension_counts", {})),
        )
    )
    lines.extend(["### content-based detected format", ""])
    lines.extend(
        _table(
            ["실제 형식", "파일 수"],
            _count_rows(physical.get("detected_format_counts", {})),
        )
    )
    lines.extend(
        [
            f"알려진 확장자의 기대 형식과 실제 감지 형식이 다른 파일은 **{mismatch_count:,}개**다.",
            "",
        ]
    )
    lines.extend(
        _table(
            ["확장자 → 실제 형식", "파일 수"],
            _count_rows(physical.get("extension_format_mismatch_pairs", {})),
        )
    )

    lines.extend(["### doc_group별 실제 형식", ""])
    lines.extend(
        [
            f"아래 표는 `raw/` 전체 기준이므로 기업별 `list_*.json` **{group_listing_count:,}개**도 포함한다.",
            "",
        ]
    )
    group_formats: dict[str, dict[str, int]] = physical.get(
        "doc_group_detected_format_counts", {}
    )
    all_formats = sorted(
        {format_name for counts in group_formats.values() for format_name in counts}
    )
    group_rows = [
        [group, *(counts.get(format_name, 0) for format_name in all_formats)]
        for group, counts in group_formats.items()
    ]
    lines.extend(_table(["doc_group", *all_formats], group_rows))

    size = physical.get("file_size", {})
    lines.extend(["### raw 파일 크기", ""])
    size_rows = [
        ["합계", _format_bytes(size.get("total_bytes"))],
        ["최소", _format_bytes(size.get("min_bytes"))],
        ["평균", _format_bytes(size.get("mean_bytes"))],
        ["중앙값", _format_bytes(size.get("median_bytes"))],
        ["95 백분위", _format_bytes(size.get("p95_bytes"))],
        ["99 백분위", _format_bytes(size.get("p99_bytes"))],
        ["최대", _format_bytes(size.get("max_bytes"))],
    ]
    lines.extend(_table(["통계", "크기"], size_rows))

    lines.extend(
        [
            "## 4. `.xml` 확장자 내부 HTML",
            "",
            f"`.xml`이지만 HTML로 감지된 파일은 **{disguised_html_count:,}개**다.",
            "확장자는 parser 선택 조건이 될 수 없으며, HTML 시그니처를 XML보다 먼저 검사해야 한다.",
            "",
        ]
    )

    lines.extend(
        [
            "## 5. 인코딩 조사",
            "",
            f"인코딩 모드는 `{encoding.get('mode', '')}`이며 텍스트 형식 **{encoding.get('scanned_file_count', 0):,}개**를 파일 전체를 메모리에 올리지 않고 증분 UTF-8 디코더로 검사했다.",
            "",
            "### 선언 charset",
            "",
        ]
    )
    lines.extend(
        _table(
            ["declared charset", "파일 수"],
            _count_rows(encoding.get("declared_charset_counts", {}))
            + [["[none]", encoding.get("no_charset_declaration_count", 0)]],
        )
    )
    lines.extend(["### 실제 UTF-8 strict decode 가능 여부", ""])
    lines.extend(
        _table(
            ["결과", "파일 수"],
            _count_rows(encoding.get("utf8_decodability_counts", {})),
        )
    )
    lines.extend(
        [
            f"charset 선언과 UTF-8 byte 검사 결과가 충돌한 사례는 **{encoding.get('declaration_mismatch_count', 0):,}개**다.",
            "단, ASCII-only 파일은 여러 ASCII 호환 인코딩에서 동일하므로 비-UTF-8 선언만으로 불일치 처리하지 않았다.",
            "",
        ]
    )
    mismatch_examples = encoding.get("declaration_mismatch_examples", [])[:20]
    lines.extend(
        _table(
            ["경로", "선언", "UTF-8 가능", "비ASCII 포함"],
            [
                [
                    item.get("relative_path", ""),
                    item.get("declared_charset", ""),
                    item.get("utf8_decodable", ""),
                    item.get("contains_non_ascii", ""),
                ]
                for item in mismatch_examples
            ],
        )
    )
    lines.append(
        "여기서 확인하는 것은 UTF-8 유효성이지 실패 파일의 정확한 legacy encoding 추정이 아니다."
    )
    lines.append("")

    lines.extend(
        [
            "## 6. periodic 다중 파일 구조",
            "",
            f"`periodic`이면서 `n_files > 1`인 문서는 **{multi.get('document_count', 0):,}건**, 실제 연결 파일은 **{multi.get('observed_file_count', 0):,}개**다.",
            f"manifest 기대 개수와 실제 개수가 다른 문서는 **{multi.get('file_count_mismatch_count', 0):,}건**이다.",
            "",
            "### 파일 역할",
            "",
        ]
    )
    lines.extend(
        _table(
            ["역할", "파일 수"],
            _count_rows(multi.get("role_counts", {})),
        )
    )
    lines.extend(["### DOCUMENT-NAME", ""])
    lines.extend(
        _table(
            ["DOCUMENT-NAME", "파일 수"],
            _count_rows(multi.get("document_name_counts", {})),
        )
    )
    sample_documents = multi.get("documents", [])[:12]
    sample_rows: list[list[object]] = []
    for document in sample_documents:
        role_summary: dict[str, int] = {}
        for file_info in document.get("files", []):
            role = str(file_info.get("role", "unknown"))
            role_summary[role] = role_summary.get(role, 0) + 1
        sample_rows.append(
            [
                document.get("doc_id", ""),
                document.get("report_name", ""),
                document.get("expected_n_files", ""),
                document.get("observed_n_files", ""),
                ", ".join(f"{key}:{value}" for key, value in role_summary.items()),
            ]
        )
    lines.extend(["### 문서 예시", ""])
    lines.extend(
        _table(
            ["doc_id", "보고서명", "기대", "실제", "역할 구성"],
            sample_rows,
        )
    )
    lines.append("전체 다중 파일별 구성은 `inventory_summary.json`에 수록했다.")
    lines.append("")

    lines.extend(["## 7. 경로 정규화 및 join 품질", ""])
    lines.extend(
        [
            "ZIP 경로와 manifest 경로는 비교 전에 Unicode NFC 및 POSIX 구분자로 정규화했다.",
            f"NFC 정규화 후 서로 다른 ZIP 엔트리가 같은 경로로 충돌한 사례는 **{join.get('canonical_path_collision_count', 0):,}개**다.",
            (
                f"manifest `n_files`와 연결된 실제 파일 수가 다른 사례는 **{join.get('manifest_file_count_mismatch_count', 0):,}건**이다."
                if manifest_available
                else "manifest를 사용할 수 없어 파일 수 join 품질은 측정하지 않았다."
            ),
            "",
        ]
    )

    lines.extend(["## 8. 발견된 예외와 Phase 2 전 결정사항", ""])
    decisions = [
        f"parser dispatch는 확장자가 아니라 content detection 결과를 사용해야 한다 (`.xml`→HTML {disguised_html_count:,}개).",
        (
            f"manifest payload와 별개인 group listing JSON {group_listing_count:,}개의 포함/제외 정책을 정해야 한다."
            if manifest_available
            else "manifest 복구 및 payload 연결 가능 여부를 먼저 확인해야 한다."
        ),
        "모든 경로·회사명 join key는 NFC로 정규화하되 원본 경로 문자열도 추적 가능하게 유지해야 한다.",
        "물리 폴더명은 `corp_name`을 따르며 축약 상장명인 `listed_name`과 혼용하지 않아야 한다.",
        "접수일은 `rcept_no` 앞자리에서 유도하지 않고 manifest의 `rcept_dt`를 권위값으로 유지해야 한다.",
        "periodic 문서는 디렉터리를 한 문서 단위로 보고 `DOCUMENT-NAME`과 파일명 규칙으로 본문/감사/연결감사 첨부 역할을 보존해야 한다.",
        "HTML charset 선언과 byte-level UTF-8 검사 결과가 다를 수 있으므로 UTF-8 strict 시도 후 명시적인 fallback/오류 정책을 정해야 한다.",
        "PDF+HTML처럼 동일 공시의 복수 표현이 있는 경우 canonical 본문 선택과 대체 표현 보존 정책을 정해야 한다.",
        "정정공시 연결은 이번 단계에서 구현하지 않았으며 Phase 2 전에 원본 선택 및 계보 모델을 별도로 확정해야 한다.",
    ]
    lines.extend(f"- {decision}" for decision in decisions)
    lines.append("")

    lines.extend(["## 9. 스캔 경고/오류", ""])
    lines.append(
        f"파일 단위 오류는 **{issues.get('total_count', 0):,}건**이며 한 건의 실패가 전체 스캔을 중단하지 않도록 처리했다."
    )
    lines.append(
        f"순회 또는 경로 안전성 문제로 건너뛴 source entry는 **{source.get('skipped_entry_count', 0):,}개**다."
    )
    lines.append("")
    lines.extend(
        _table(
            ["단계", "건수"],
            _count_rows(issues.get("counts_by_stage", {})),
        )
    )
    if issues.get("examples"):
        lines.extend(
            _table(
                ["단계", "경로", "오류"],
                [
                    [item.get("stage", ""), item.get("path", ""), item.get("error", "")]
                    for item in issues["examples"][:20]
                ],
            )
        )

    lines.extend(
        [
            "## 산출물 해석 주의",
            "",
            "- `file_inventory.csv`는 재귀 탐색 조건을 충족하도록 raw 및 auxiliary 파일을 모두 포함하며 `scope`와 `file_scope` 열로 구분한다.",
            "- 이 보고서의 물리 파일 통계는 공시 코퍼스 범위인 `raw/` 파일만 집계한다.",
            "- 본문 전체 parsing, RAG chunking, 정정공시 연결은 수행하지 않았다.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = ["render_inventory_report"]
