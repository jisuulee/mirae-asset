"""Streaming Phase 5 final assembly, quality gate, and Role 2 handoff."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from disclosure_preprocess.correction_lineage import MetadataIndex, validate_lineage
from disclosure_preprocess.final_index import (
    PHASE5_SCHEMA_VERSION,
    build_chain_membership,
    build_document_index_row,
    count_provenance,
    sample_traits,
    select_representative_sample,
)
from disclosure_preprocess.manifest import load_manifest
from disclosure_preprocess.source import SourceFile, open_corpus_source


LOGGER = logging.getLogger(__name__)

DOCUMENT_INDEX_FILENAME = "document_index.jsonl"
ARTIFACT_MANIFEST_FILENAME = "artifact_manifest.json"

# This project-specific lock was established by the completed Phase 2~4 runs.
# A different byte sequence must be reviewed and explicitly supplied instead of
# being silently accepted as the same canonical artifact.
LOCKED_INPUT_HASHES = {
    "parsed_disclosures": "967dcfbbf6fce28f8acd949a681d6389df0f3d6a1c42da1b4f5d69b3d764109c",
    "normalized_metadata": "8ad1c1c6c73299eb2841a338cc7001cec4600d3202c53048427d7c9d8bcfde57",
    "correction_lineage": "f367eed6395d7332f2a25e9469d1f694cfffb8229f0ff5a2eaf772128b589abc",
    "correction_chains": "c7c6eb77e73d5e0a92700a3da91e258b026c59ab02342bd3342d31c177b50bd9",
    "manifest": "04750795e1a2d5c35f73e4bb7766ebede02ff3512d33c382bd4c3a51daba3364",
}

LOCKED_INPUT_SIZES = {
    "parsed_disclosures": 26_585_627_476,
    "normalized_metadata": 8_279_693,
    "correction_lineage": 6_953_642,
    "correction_chains": 227_698,
}

EXPECTED_CORPUS_METRICS = {
    "document_count": 4204,
    "company_count": 70,
    "physical_source_count": 4622,
    "source_sha256_count": 4622,
    "main_title_count": 4202,
    "main_body_count": 4204,
    "main_section_document_count": 2732,
    "main_no_section_count": 1472,
    "main_section_count": 111261,
    "main_table_document_count": 4202,
    "main_table_count": 1440210,
    "all_physical_table_count": 1551111,
    "provenance_item_count": 41659872,
    "provenance_missing_count": 0,
    "elided_alternative_representation_count": 3,
    "metadata_mismatch_count": 745,
}

EXPECTED_PARSE_STATUS_COUNTS = {"success": 2928, "partial": 1276, "failed": 0}
EXPECTED_GROUP_COUNTS = {
    "exchange": 1469,
    "holding": 1083,
    "periodic": 1054,
    "major": 598,
}
EXPECTED_LINEAGE_STATUS_COUNTS = {
    "linked_exact": 461,
    "linked_inferred": 120,
    "ambiguous": 91,
    "unresolved": 332,
}
EXPECTED_CHAIN_COUNTS = {"complete": 341, "partial": 66, "total": 407}


class FinalValidationError(RuntimeError):
    """Raised when Phase 5 refuses to publish a failed assembly."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _source_sha256(entry: SourceFile) -> str:
    digest = hashlib.sha256()
    with entry.open() as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_jsonl(
    path: Path,
    *,
    key: str,
    expected_schema: str | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FinalValidationError(
                    f"FINAL_VALIDATION_FAILED: invalid JSONL {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise FinalValidationError(
                    f"FINAL_VALIDATION_FAILED: non-object JSONL row {path}:{line_number}"
                )
            if expected_schema is not None and row.get("schema_version") != expected_schema:
                raise FinalValidationError(
                    "FINAL_VALIDATION_FAILED: schema mismatch "
                    f"{path}:{line_number} expected={expected_schema} observed={row.get('schema_version')}"
                )
            identity = str(row.get(key) or "")
            if not identity:
                raise FinalValidationError(
                    f"FINAL_VALIDATION_FAILED: missing {key} at {path}:{line_number}"
                )
            if identity in index:
                raise FinalValidationError(
                    f"FINAL_VALIDATION_FAILED: duplicate {key}={identity} in {path}"
                )
            rows.append(row)
            index[identity] = row
    return rows, index, digest.hexdigest()


def _warning_occurrences(record: Mapping[str, Any]) -> Counter[str]:
    result: Counter[str] = Counter()
    collections: list[object] = [record.get("warnings")]
    main = record.get("main_document")
    if isinstance(main, Mapping):
        collections.append(main.get("warnings"))
    for key in ("attachments", "alternative_representations"):
        values = record.get(key)
        if isinstance(values, Sequence) and not isinstance(
            values, (str, bytes, bytearray)
        ):
            for value in values:
                if isinstance(value, Mapping):
                    collections.append(value.get("warnings"))
    for values in collections:
        if not isinstance(values, Sequence) or isinstance(
            values, (str, bytes, bytearray)
        ):
            continue
        for warning in values:
            if isinstance(warning, Mapping) and warning.get("code"):
                result[str(warning["code"])] += 1
    return result


def _record_failure(
    failures: list[dict[str, Any]],
    code: str,
    *,
    expected: object,
    observed: object,
    details: object | None = None,
) -> None:
    failure = {
        "code": code,
        "expected": expected,
        "observed": observed,
    }
    if details is not None:
        failure["details"] = details
    failures.append(failure)


def _validate_hash(
    failures: list[dict[str, Any]],
    name: str,
    observed: str,
    expected_hashes: Mapping[str, str] | None,
) -> None:
    if expected_hashes is None or name not in expected_hashes:
        return
    expected = str(expected_hashes[name]).lower()
    if observed.lower() != expected:
        _record_failure(
            failures,
            "CANONICAL_ARTIFACT_HASH_MISMATCH",
            expected={"artifact": name, "sha256": expected},
            observed={"artifact": name, "sha256": observed.lower()},
        )


def _counter_dict(counter: Counter[str], *, include_zero: Sequence[str] = ()) -> dict[str, int]:
    values = dict(counter)
    for key in include_zero:
        values.setdefault(key, 0)
    return {key: int(values[key]) for key in sorted(values)}


def _artifact_info(
    *,
    project_root: Path,
    path: Path,
    schema_version: str,
    record_count: int,
    sha256: str,
    purpose: str,
) -> dict[str, Any]:
    try:
        relative = path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        relative = str(path.resolve())
    return {
        "path": relative,
        "schema_version": schema_version,
        "size_bytes": path.stat().st_size,
        "sha256": sha256,
        "record_count": record_count,
        "purpose": purpose,
    }


def _copy_sample_lines(
    parsed_path: Path,
    destination: Path,
    selected: Sequence[Mapping[str, Any]],
) -> tuple[str, int]:
    ordered = sorted(selected, key=lambda item: int(item["byte_offset"]))
    digest = hashlib.sha256()
    with parsed_path.open("rb") as source, destination.open("wb") as output:
        for item in ordered:
            source.seek(int(item["byte_offset"]))
            raw_line = source.read(int(item["line_size"]))
            observed = hashlib.sha256(raw_line).hexdigest()
            if observed != item["source_line_sha256"]:
                raise FinalValidationError(
                    "FINAL_VALIDATION_FAILED: sample source line changed for "
                    f"{item['doc_id']}"
                )
            output.write(raw_line)
            digest.update(raw_line)
    return digest.hexdigest(), destination.stat().st_size


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> tuple[str, int]:
    digest = hashlib.sha256()
    with path.open("wb") as stream:
        for row in rows:
            raw = _json_bytes(row)
            stream.write(raw)
            digest.update(raw)
    return digest.hexdigest(), path.stat().st_size


def _publish(artifacts: Sequence[tuple[Path, Path]]) -> None:
    backups: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for staged, final in artifacts:
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                backup = staged.parent / (staged.name + ".previous")
                os.replace(final, backup)
                backups.append((backup, final))
            os.replace(staged, final)
            published.append(final)
    except BaseException:
        for final in reversed(published):
            if final.exists():
                final.unlink()
        for backup, final in reversed(backups):
            if backup.exists():
                os.replace(backup, final)
        raise
    for backup, _ in backups:
        if backup.exists():
            backup.unlink()


def _write_failures_direct(report_dir: Path, failures: Sequence[Mapping[str, Any]]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    final = report_dir / "final_validation_failures.jsonl"
    handle, temporary_name = tempfile.mkstemp(
        prefix=".phase5-failures-", suffix=".jsonl", dir=report_dir
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        _write_jsonl(temporary, failures)
        os.replace(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)


def _known_limitations(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    extraction = summary["content_quality"]
    lineage = summary["lineage_quality"]
    return [
        {
            "code": "PARTIAL_PARSE_PRESENT",
            "count": summary["parse_status_counts"].get("partial", 0),
            "impact": "Documents remain searchable, but recovered markup should be shown with a parse caution.",
        },
        {
            "code": "NO_SECTION_BOUNDARIES",
            "count": extraction["main_no_section_count"],
            "impact": "Do not infer missing section hierarchy; use blocks/tables and provenance.",
        },
        {
            "code": "MAIN_TITLE_MISSING",
            "count": extraction["main_title_missing_count"],
            "impact": "Canonical report_nm remains available; an extracted title must not be invented.",
        },
        {
            "code": "TABLE_SPAN_NORMALIZATION_WARNING",
            "document_count": extraction["table_span_warning_document_count"],
            "occurrence_count": extraction["table_span_warning_occurrence_count"],
            "impact": "Use raw_rows and cell provenance where normalized spans are unavailable.",
        },
        {
            "code": "PDF_FALLBACK_USED",
            "document_count": extraction["pdf_fallback_document_count"],
            "impact": "PDF page locators are evidence; the HTML viewer may be non-substantive.",
        },
        {
            "code": "BODY_TEXT_NOT_CITATION",
            "impact": "body_text is a convenience view. Citations must resolve through block/table provenance and source SHA-256.",
        },
        {
            "code": "ALTERNATIVE_REPRESENTATION_CONTENT_ELIDED",
            "count": extraction["elided_alternative_representation_count"],
            "provenance_item_difference": extraction["elided_alternative_provenance_item_count"],
            "impact": "Three non-canonical PDF/HTML representations preserve source metadata and SHA-256 but intentionally do not duplicate parsed content.",
        },
        {
            "code": "AMBIGUOUS_CORRECTION_LINEAGE",
            "count": lineage["status_counts"].get("ambiguous", 0),
            "impact": "Do not choose a target or latest document automatically.",
        },
        {
            "code": "UNRESOLVED_CORRECTION_LINEAGE",
            "count": lineage["status_counts"].get("unresolved", 0),
            "impact": "The corpus does not contain enough evidence for a unique target.",
        },
        {
            "code": "PARTIAL_CORRECTION_CHAIN",
            "count": lineage["partial_chain_count"],
            "impact": "Latest/superseded booleans are null and automatic deduplication is forbidden.",
        },
        {
            "code": "EXTERNAL_LOOKUP_NOT_RUN",
            "impact": "Phase 4 corpus-only lineage may miss targets outside the supplied 4,204 disclosures.",
        },
    ]


def _report_markdown(summary: Mapping[str, Any]) -> str:
    content = summary["content_quality"]
    lineage = summary["lineage_quality"]
    joins = summary["joins"]
    artifacts = summary["artifacts"]
    gates = summary["quality_gates"]
    lines = [
        "# Phase 5 최종 데이터셋 품질 보고서",
        "",
        f"- 스키마 버전: `{summary['schema_version']}`",
        f"- 생성 시각(UTC): `{summary['generated_at_utc']}`",
        f"- 최종 상태: **{summary['status']}**",
        f"- Python: `{summary['execution']['python_version']}`",
        "",
        "## 1. Corpus 및 교차 단계 join",
        "",
        f"- 논리 공시: **{summary['corpus']['document_count']:,}건**",
        f"- 기업: **{summary['corpus']['company_count']:,}개**",
        f"- physical source: **{content['physical_source_count']:,}개**",
        f"- manifest/metadata/parsed/index join: **{joins['matched_count']:,}/{summary['corpus']['document_count']:,}**",
        f"- join failure: **{joins['failure_count']:,}건**",
        "",
        "## 2. Parsing 및 구조 품질",
        "",
        "| 항목 | 개수 |",
        "| --- | ---: |",
        f"| success | {summary['parse_status_counts'].get('success', 0):,} |",
        f"| partial | {summary['parse_status_counts'].get('partial', 0):,} |",
        f"| failed | {summary['parse_status_counts'].get('failed', 0):,} |",
        f"| main title 보유 문서 | {content['main_title_count']:,} |",
        f"| non-empty body 문서 | {content['main_body_count']:,} |",
        f"| section 보유 문서 | {content['main_section_document_count']:,} |",
        f"| section 미보유 문서 | {content['main_no_section_count']:,} |",
        f"| main table | {content['main_table_count']:,} |",
        f"| PDF fallback 문서 | {content['pdf_fallback_document_count']:,} |",
        f"| table span warning 문서 | {content['table_span_warning_document_count']:,} |",
        "",
        "partial 문서는 제외하지 않는다. `quality.requires_parse_caution=true`로 전달한다.",
        "section이 없는 문서는 원문 구조가 없는 경우이며 임의 section을 생성하지 않았다.",
        "",
        "## 3. Provenance",
        "",
        f"- serialized provenance items (directly revalidated): **{content['provenance_item_count']:,}**",
        f"- Phase 2 parsed provenance items (before alternative-content elision): **{content['phase2_reported_provenance_item_count']:,}**",
        f"- intentionally elided alternative provenance items: **{content['elided_alternative_provenance_item_count']:,}**",
        f"- provenance missing: **{content['provenance_missing_count']:,}**",
        f"- source SHA-256: **{content['source_sha256_count']:,}/{content['physical_source_count']:,}**",
        "",
        "`body_text`는 검색 편의 뷰이며 citation source가 아니다. 인용은 block/table/cell locator와 `source_files[].sha256`을 사용한다.",
        "",
        "## 4. Metadata 및 lineage",
        "",
        f"- canonical metadata invalid: **{summary['metadata_quality']['status_counts'].get('invalid', 0):,}건**",
        f"- source metadata mismatch: **{summary['metadata_quality']['mismatch_count']:,}회**",
        f"- lineage exact/inferred: **{lineage['status_counts'].get('linked_exact', 0):,}/{lineage['status_counts'].get('linked_inferred', 0):,}**",
        f"- ambiguous/unresolved: **{lineage['status_counts'].get('ambiguous', 0):,}/{lineage['status_counts'].get('unresolved', 0):,}**",
        f"- complete/partial chains: **{lineage['complete_chain_count']:,}/{lineage['partial_chain_count']:,}**",
        f"- graph invariant errors: **{lineage['invariant_error_count']:,}**",
        "",
        "`is_latest_in_complete_chain`과 `is_superseded_in_complete_chain`은 complete chain에서만 boolean이다. partial/ambiguous/unresolved에는 반드시 `null`이다.",
        "",
        "## 5. Artifact",
        "",
        "| artifact | records | size | SHA-256 |",
        "| --- | ---: | ---: | --- |",
    ]
    for name, artifact in artifacts.items():
        lines.append(
            f"| {name} | {artifact['record_count']:,} | {artifact['size_bytes']:,} | `{artifact['sha256']}` |"
        )
    lines.extend(
        [
            "",
            "기본 실행에서는 merged dataset을 생성하지 않았다. Role 2는 `doc_id`로 content, metadata, lineage, document index를 join한다.",
            "",
            "## 6. Quality gate",
            "",
            "| gate | 결과 |",
            "| --- | --- |",
        ]
    )
    for name, passed in gates.items():
        lines.append(f"| {name} | {'PASS' if passed else 'FAIL'} |")
    lines.extend(
        [
            "",
            "## 7. Role 2 인계",
            "",
            f"- 위치: `{summary['handoff']['path']}`",
            f"- sample: **{summary['handoff']['sample_count']}건** (최대 100건)",
            f"- sample coverage 누락: **{len(summary['handoff']['missing_coverage'])}개**",
            "- 대형 `parsed_disclosures.jsonl`은 복사하지 않았고 manifest 경로·해시로만 참조한다.",
            "",
        ]
    )
    return "\n".join(lines)


def _handoff_readme(summary: Mapping[str, Any]) -> str:
    return f"""# Role 2 — Search / DB / RAG handoff

이 디렉터리는 Phase 5 품질 gate를 통과한 개발 인계 패키지다.

- schema: `{PHASE5_SCHEMA_VERSION}`
- documents: `{summary['corpus']['document_count']}`
- sample documents: `{summary['handoff']['sample_count']}`
- join key: `doc_id` (보조 일관성 검사는 `rcept_no`)
- final status: `{summary['status']}`

`sample_disclosures.jsonl`은 원본 Phase 2 JSONL line의 byte-identical subset이고,
`sample_document_index.jsonl`은 같은 doc_id의 경량 인덱스다. 전체 본문은 이
패키지에 복사하지 않았다. `artifact_manifest.json`의 canonical 경로와 SHA-256을
검증한 뒤 프로젝트 루트의 `data/processed/parsed_disclosures.jsonl`을 사용한다.

검색 기본값은 모든 문서를 보존하는 것이다. 최신본 자동 dedup은 complete chain에만
허용된다. `requires_lineage_guard=true`인 문서를 임의로 숨기거나 원공시에 합치지 않는다.

인용에는 `body_text`를 사용하지 말고 block/table/cell provenance의 source locator와
`source_files[].sha256`을 사용한다.
"""


def _integration_notes() -> str:
    return """# Integration notes

## Join

`document_index.jsonl`, `metadata_normalized.jsonl`, `parsed_disclosures.jsonl`은
`doc_id`로 1:1 join한다. `rcept_no`는 반드시 함께 대조한다. correction lineage는
`is_correction=true`인 1,004건에만 한 행씩 존재한다.

## Filtering

회사·날짜·분류 필터는 `document_index.metadata`를 사용한다. canonical source는
manifest이고 원문 추출 metadata는 filter authority가 아니다. `corp_name`과
`listed_name`은 합치지 않는다.

## Latest-view safety

`is_latest_in_complete_chain=true`는 complete chain의 안전한 최신 member다.
`is_superseded_in_complete_chain=true`는 같은 complete chain의 이전 member다.
두 필드가 `null`이면 latest 여부가 확정되지 않은 것이다. 특히 partial,
ambiguous, unresolved 문서를 boolean false처럼 취급하면 안 된다.

권장 절차는 (1) 전체 결과를 기본으로 유지하고, (2) 사용자가 lineage-aware view를
명시했을 때 complete chain의 superseded member만 제거하며, (3)
`requires_lineage_guard=true` 문서는 그대로 노출하고 caution을 표시하는 것이다.

## Tables and citations

표 분류나 의미 라벨은 생성하지 않았다. `table_count`와 span warning만 구조 통계다.
span warning이 있으면 normalized grid보다 `raw_rows`와 cell provenance를 우선한다.
PDF fallback 문서는 page locator를 사용한다. `body_text`는 citation source가 아니다.
"""


def assemble_final_dataset(
    *,
    project_root: str | Path,
    data_root: str | Path,
    parsed_path: str | Path,
    metadata_path: str | Path,
    lineage_path: str | Path,
    chains_path: str | Path,
    output_dir: str | Path,
    report_dir: str | Path,
    handoff_dir: str | Path,
    data_contract_path: str | Path,
    parser_summary_path: str | Path | None = None,
    expected_hashes: Mapping[str, str] | None = LOCKED_INPUT_HASHES,
    expected_document_count: int = 4204,
    expected_correction_count: int = 1004,
    expected_chain_count: int = 407,
    sample_count: int = 90,
    strict_corpus_metrics: bool = True,
    enforce_sample_coverage: bool = True,
    materialize_merged: bool = False,
    progress_every: int = 100,
) -> dict[str, Any]:
    """Assemble and atomically publish Phase 5 artifacts."""

    started = _utc_now()
    root = Path(project_root).resolve()
    data_root = Path(data_root).resolve()
    parsed = Path(parsed_path).resolve()
    metadata_file = Path(metadata_path).resolve()
    lineage_file = Path(lineage_path).resolve()
    chains_file = Path(chains_path).resolve()
    output = Path(output_dir).resolve()
    reports = Path(report_dir).resolve()
    handoff = Path(handoff_dir).resolve()
    contract = Path(data_contract_path).resolve()
    parser_summary_file = (
        Path(parser_summary_path).resolve()
        if parser_summary_path is not None
        else root / "reports" / "parser_summary.json"
    )
    inputs = (parsed, metadata_file, lineage_file, chains_file, contract, parser_summary_file)
    for path in inputs:
        if not path.is_file():
            raise FileNotFoundError(f"Required Phase 5 input not found: {path}")
    initial_stats = {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in (parsed, metadata_file, lineage_file, chains_file)
    }

    failures: list[dict[str, Any]] = []
    if strict_corpus_metrics:
        for name, path in {
            "parsed_disclosures": parsed,
            "normalized_metadata": metadata_file,
            "correction_lineage": lineage_file,
            "correction_chains": chains_file,
        }.items():
            expected_size = LOCKED_INPUT_SIZES[name]
            if path.stat().st_size != expected_size:
                _record_failure(
                    failures,
                    "CANONICAL_ARTIFACT_SIZE_MISMATCH",
                    expected={"artifact": name, "size_bytes": expected_size},
                    observed={"artifact": name, "size_bytes": path.stat().st_size},
                )

    metadata_rows, metadata_by_id, metadata_sha = _load_jsonl(
        metadata_file, key="doc_id", expected_schema="3.0.0"
    )
    lineage_rows, lineage_by_id, lineage_sha = _load_jsonl(
        lineage_file, key="doc_id", expected_schema="4.0.0"
    )
    chains, chains_by_id, chains_sha = _load_jsonl(
        chains_file, key="chain_id", expected_schema="4.0.0"
    )
    del chains_by_id
    _validate_hash(failures, "normalized_metadata", metadata_sha, expected_hashes)
    _validate_hash(failures, "correction_lineage", lineage_sha, expected_hashes)
    _validate_hash(failures, "correction_chains", chains_sha, expected_hashes)

    with open_corpus_source(data_root) as source:
        manifest_entry = source.find_manifest()
        if manifest_entry is None:
            raise FinalValidationError("FINAL_VALIDATION_FAILED: manifest.jsonl not found")
        manifest_sha = _source_sha256(manifest_entry)
        manifest = load_manifest(manifest_entry)
        source_info = {
            "kind": source.kind,
            "path": str(source.location),
            "logical_root": source.logical_root,
            "manifest_relative_path": manifest_entry.relative_path,
            "manifest_sha256": manifest_sha,
        }
    _validate_hash(failures, "manifest", manifest_sha, expected_hashes)
    manifest_by_id = {
        str(row.get("doc_id") or ""): row for row in manifest.records
    }
    if len(manifest_by_id) != len(manifest.records) or "" in manifest_by_id:
        _record_failure(
            failures,
            "MANIFEST_IDENTIFIER_INVALID",
            expected="unique non-empty doc_id",
            observed=len(manifest_by_id),
        )

    correction_ids = {
        doc_id
        for doc_id, row in metadata_by_id.items()
        if isinstance(row.get("metadata"), Mapping)
        and row["metadata"].get("is_correction") is True
    }
    if set(lineage_by_id) != correction_ids:
        _record_failure(
            failures,
            "LINEAGE_METADATA_CARDINALITY_MISMATCH",
            expected=len(correction_ids),
            observed=len(lineage_by_id),
            details={
                "missing": sorted(correction_ids - set(lineage_by_id))[:20],
                "unexpected": sorted(set(lineage_by_id) - correction_ids)[:20],
            },
        )

    if len(metadata_rows) != expected_document_count:
        _record_failure(failures, "METADATA_COUNT_MISMATCH", expected=expected_document_count, observed=len(metadata_rows))
    if len(manifest.records) != expected_document_count:
        _record_failure(failures, "MANIFEST_COUNT_MISMATCH", expected=expected_document_count, observed=len(manifest.records))
    if len(lineage_rows) != expected_correction_count:
        _record_failure(failures, "LINEAGE_COUNT_MISMATCH", expected=expected_correction_count, observed=len(lineage_rows))
    if len(chains) != expected_chain_count:
        _record_failure(failures, "CHAIN_COUNT_MISMATCH", expected=expected_chain_count, observed=len(chains))

    metadata_index = MetadataIndex.build(metadata_rows)
    graph_validation = validate_lineage(lineage_rows, chains, metadata_index)
    if not graph_validation.get("passed"):
        _record_failure(
            failures,
            "PHASE4_GRAPH_INVARIANT_FAILED",
            expected=0,
            observed=graph_validation.get("error_count"),
            details=graph_validation.get("errors", [])[:20],
        )
    chain_membership = build_chain_membership(chains)

    stage = Path(tempfile.mkdtemp(prefix=".phase5-", dir=root))
    stage_data = stage / "data_final"
    stage_reports = stage / "reports"
    stage_handoff = stage / "role2"
    for directory in (stage_data, stage_reports, stage_handoff):
        directory.mkdir()
    staged_index = stage_data / DOCUMENT_INDEX_FILENAME
    staged_manifest = stage_data / ARTIFACT_MANIFEST_FILENAME
    staged_merged = stage_data / "merged_disclosures.jsonl"
    staged_failures = stage_reports / "final_validation_failures.jsonl"
    staged_summary = stage_reports / "final_quality_summary.json"
    staged_report = stage_reports / "final_quality_report.md"
    staged_limitations_json = stage_reports / "final_known_limitations.json"

    parsed_digest = hashlib.sha256()
    index_digest = hashlib.sha256()
    merged_digest = hashlib.sha256()
    descriptors: list[dict[str, Any]] = []
    index_rows: dict[str, dict[str, Any]] = {}
    parsed_ids: set[str] = set()
    parsed_receipts: set[str] = set()
    duplicate_parsed_ids: set[str] = set()
    duplicate_parsed_receipts: set[str] = set()
    unexpected_parsed_ids: set[str] = set()
    linkage_mismatch_ids: set[str] = set()
    canonical_mismatch_ids: set[str] = set()
    parse_status_counts: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()
    companies: set[str] = set()
    metadata_status_counts: Counter[str] = Counter()
    metadata_mismatch_count = 0
    main_title_count = 0
    main_body_count = 0
    main_section_document_count = 0
    main_section_count = 0
    main_table_document_count = 0
    main_table_count = 0
    physical_source_count = 0
    source_sha256_count = 0
    provenance_counts: Counter[str] = Counter()
    provenance_missing: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    table_span_warning_ids: set[str] = set()
    pdf_fallback_ids: set[str] = set()
    lineage_guard_count = 0
    invalid_complete_flag_ids: set[str] = set()
    elided_alternative_representation_count = 0
    index_count = 0
    merged_count = 0
    index_stream: BinaryIO | None = None
    merged_stream: BinaryIO | None = None

    try:
        index_stream = staged_index.open("wb")
        if materialize_merged:
            merged_stream = staged_merged.open("wb")
        with parsed.open("rb") as stream:
            line_number = 0
            while True:
                byte_offset = stream.tell()
                raw_line = stream.readline()
                if not raw_line:
                    break
                parsed_digest.update(raw_line)
                if not raw_line.strip():
                    continue
                line_number += 1
                try:
                    parsed_record = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    _record_failure(
                        failures,
                        "PARSED_JSONL_INVALID",
                        expected="valid JSON object",
                        observed={"line_number": line_number, "error": str(exc)},
                    )
                    continue
                if not isinstance(parsed_record, Mapping):
                    _record_failure(
                        failures,
                        "PARSED_JSONL_NON_OBJECT",
                        expected="object",
                        observed={"line_number": line_number, "type": type(parsed_record).__name__},
                    )
                    continue
                doc_id = str(parsed_record.get("doc_id") or "")
                receipt = str(parsed_record.get("rcept_no") or "")
                if doc_id in parsed_ids:
                    duplicate_parsed_ids.add(doc_id)
                    continue
                parsed_ids.add(doc_id)
                if receipt in parsed_receipts:
                    duplicate_parsed_receipts.add(receipt)
                parsed_receipts.add(receipt)
                metadata_row = metadata_by_id.get(doc_id)
                manifest_row = manifest_by_id.get(doc_id)
                if metadata_row is None or manifest_row is None:
                    unexpected_parsed_ids.add(doc_id)
                    continue
                if receipt != str(metadata_row.get("rcept_no") or "") or receipt != str(manifest_row.get("rcept_no") or ""):
                    linkage_mismatch_ids.add(doc_id)
                    continue
                metadata = metadata_row.get("metadata")
                if not isinstance(metadata, Mapping):
                    linkage_mismatch_ids.add(doc_id)
                    continue
                for field in (
                    "doc_id", "corp_code", "corp_name", "listed_name", "stock_code",
                    "industry", "sector", "doc_group", "doc_subtype", "report_nm",
                    "is_correction", "rcept_no", "rcept_dt", "flr_nm", "base_year", "base_month",
                ):
                    if metadata.get(field) != manifest_row.get(field):
                        canonical_mismatch_ids.add(doc_id)
                        break
                lineage_record = lineage_by_id.get(doc_id)
                membership = chain_membership.get(doc_id)
                try:
                    index_row = build_document_index_row(
                        metadata_row,
                        parsed_record,
                        lineage_record,
                        membership,
                        record_size_bytes=len(raw_line),
                    )
                except ValueError as exc:
                    _record_failure(
                        failures,
                        "DOCUMENT_INDEX_JOIN_FAILED",
                        expected="consistent Phase 2/3/4 row",
                        observed={"doc_id": doc_id, "error": str(exc)},
                    )
                    continue
                raw_index = _json_bytes(index_row)
                index_stream.write(raw_index)
                index_digest.update(raw_index)
                index_count += 1
                index_rows[doc_id] = index_row
                summary = index_row["content_summary"]
                quality = index_row["quality"]
                lineage = index_row["lineage"]
                parse_status_counts[str(summary.get("parse_status") or "failed")] += 1
                group_counts[str(metadata.get("doc_group") or "unknown")] += 1
                companies.add(str(metadata.get("corp_code") or ""))
                validation = metadata_row.get("metadata_validation")
                metadata_status = str(validation.get("status") or "invalid") if isinstance(validation, Mapping) else "invalid"
                metadata_status_counts[metadata_status] += 1
                if isinstance(validation, Mapping):
                    metadata_mismatch_count += int(validation.get("mismatch_count") or 0)
                main_title_count += summary["has_title"] is True
                main_body_count += summary["has_body"] is True
                main_section_document_count += summary["has_sections"] is True
                main_section_count += int(summary["section_count"])
                main_table_document_count += summary["has_tables"] is True
                main_table_count += int(summary["table_count"])
                physical_source_count += int(summary["source_file_count"])
                source_sha256_count += int(summary["source_sha256_count"])
                record_provenance, record_missing = count_provenance(parsed_record)
                provenance_counts.update(record_provenance)
                provenance_missing.update(record_missing)
                occurrences = _warning_occurrences(parsed_record)
                warning_counts.update(occurrences)
                alternatives = parsed_record.get("alternative_representations")
                if isinstance(alternatives, list):
                    elided_alternative_representation_count += sum(
                        isinstance(alternative, Mapping)
                        and alternative.get("content_included") is False
                        for alternative in alternatives
                    )
                if summary["has_table_span_warning"]:
                    table_span_warning_ids.add(doc_id)
                if summary["has_pdf_fallback"]:
                    pdf_fallback_ids.add(doc_id)
                lineage_guard_count += quality["requires_lineage_guard"] is True
                if lineage["is_complete_chain"] is not True and (
                    lineage["is_latest_in_complete_chain"] is not None
                    or lineage["is_superseded_in_complete_chain"] is not None
                ):
                    invalid_complete_flag_ids.add(doc_id)
                descriptors.append(
                    {
                        "doc_id": doc_id,
                        "rcept_no": receipt,
                        "byte_offset": byte_offset,
                        "line_size": len(raw_line),
                        "line_number": line_number,
                        "source_line_sha256": hashlib.sha256(raw_line).hexdigest(),
                        "index_row": index_row,
                        "traits": sample_traits(index_row),
                    }
                )
                if merged_stream is not None:
                    merged_row = {
                        "schema_version": PHASE5_SCHEMA_VERSION,
                        "doc_id": doc_id,
                        "rcept_no": receipt,
                        "metadata": dict(metadata),
                        "lineage": dict(lineage),
                        "quality": dict(quality),
                        "content": parsed_record,
                    }
                    raw_merged = _json_bytes(merged_row)
                    merged_stream.write(raw_merged)
                    merged_digest.update(raw_merged)
                    merged_count += 1
                if progress_every and index_count % progress_every == 0:
                    LOGGER.info("Assembled %d/%d final index rows", index_count, expected_document_count)
        index_stream.close()
        index_stream = None
        if merged_stream is not None:
            merged_stream.close()
            merged_stream = None

        parsed_sha = parsed_digest.hexdigest()
        _validate_hash(failures, "parsed_disclosures", parsed_sha, expected_hashes)
        expected_ids = set(metadata_by_id)
        missing_parsed = expected_ids - parsed_ids
        if duplicate_parsed_ids:
            _record_failure(failures, "PARSED_DUPLICATE_DOC_ID", expected=0, observed=len(duplicate_parsed_ids), details=sorted(duplicate_parsed_ids)[:20])
        if duplicate_parsed_receipts:
            _record_failure(failures, "PARSED_DUPLICATE_RCEPT_NO", expected=0, observed=len(duplicate_parsed_receipts), details=sorted(duplicate_parsed_receipts)[:20])
        if missing_parsed or unexpected_parsed_ids:
            _record_failure(
                failures,
                "CROSS_PHASE_JOIN_SET_MISMATCH",
                expected={"missing": 0, "unexpected": 0},
                observed={"missing": len(missing_parsed), "unexpected": len(unexpected_parsed_ids)},
                details={"missing_examples": sorted(missing_parsed)[:20], "unexpected_examples": sorted(unexpected_parsed_ids)[:20]},
            )
        if linkage_mismatch_ids:
            _record_failure(failures, "CROSS_PHASE_RCEPT_NO_MISMATCH", expected=0, observed=len(linkage_mismatch_ids), details=sorted(linkage_mismatch_ids)[:20])
        if canonical_mismatch_ids:
            _record_failure(failures, "MANIFEST_CANONICAL_METADATA_MISMATCH", expected=0, observed=len(canonical_mismatch_ids), details=sorted(canonical_mismatch_ids)[:20])
        if index_count != expected_document_count:
            _record_failure(failures, "DOCUMENT_INDEX_COUNT_MISMATCH", expected=expected_document_count, observed=index_count)
        if invalid_complete_flag_ids:
            _record_failure(failures, "UNSAFE_LATEST_BOOLEAN", expected=0, observed=len(invalid_complete_flag_ids), details=sorted(invalid_complete_flag_ids)[:20])

        content_quality = {
            "physical_source_count": physical_source_count,
            "source_sha256_count": source_sha256_count,
            "main_title_count": main_title_count,
            "main_title_missing_count": index_count - main_title_count,
            "main_body_count": main_body_count,
            "main_empty_body_count": index_count - main_body_count,
            "main_section_document_count": main_section_document_count,
            "main_no_section_count": index_count - main_section_document_count,
            "main_section_count": main_section_count,
            "main_table_document_count": main_table_document_count,
            "main_table_count": main_table_count,
            "all_physical_table_count": int(provenance_counts["table"]),
            "provenance_item_count": sum(provenance_counts.values()),
            "provenance_missing_count": sum(provenance_missing.values()),
            "provenance_counts_by_kind": _counter_dict(provenance_counts),
            "provenance_missing_by_kind": _counter_dict(provenance_missing),
            "elided_alternative_representation_count": elided_alternative_representation_count,
            "warning_occurrence_counts": _counter_dict(warning_counts),
            "pdf_fallback_document_count": len(pdf_fallback_ids),
            "pdf_fallback_doc_ids": sorted(pdf_fallback_ids),
            "table_span_warning_document_count": len(table_span_warning_ids),
            "table_span_warning_occurrence_count": warning_counts["TABLE_SPAN_NORMALIZATION_FAILED"],
            "table_span_warning_doc_ids": sorted(table_span_warning_ids),
        }
        lineage_status_counts = Counter(
            str(row.get("resolution", {}).get("status")) for row in lineage_rows
        )
        complete_chain_count = sum(chain.get("is_complete") is True for chain in chains)
        partial_chain_count = len(chains) - complete_chain_count
        lineage_quality = {
            "correction_count": len(correction_ids),
            "lineage_record_count": len(lineage_rows),
            "status_counts": _counter_dict(lineage_status_counts),
            "complete_chain_count": complete_chain_count,
            "partial_chain_count": partial_chain_count,
            "chain_count": len(chains),
            "lineage_guard_document_count": lineage_guard_count,
            "invalid_latest_boolean_count": len(invalid_complete_flag_ids),
            "invariants_passed": bool(graph_validation.get("passed")),
            "invariant_error_count": int(graph_validation.get("error_count") or 0),
            "future_target_count": int(graph_validation.get("future_target_count") or 0),
            "cross_company_link_count": int(graph_validation.get("cross_company_link_count") or 0),
            "evidence_less_link_count": int(graph_validation.get("evidence_less_link_count") or 0),
            "cycle_count": int(graph_validation.get("cycle_count") or 0),
        }

        if strict_corpus_metrics:
            observed_metrics = {
                "document_count": index_count,
                "company_count": len(companies - {""}),
                "physical_source_count": physical_source_count,
                "source_sha256_count": source_sha256_count,
                "main_title_count": main_title_count,
                "main_body_count": main_body_count,
                "main_section_document_count": main_section_document_count,
                "main_no_section_count": index_count - main_section_document_count,
                "main_section_count": main_section_count,
                "main_table_document_count": main_table_document_count,
                "main_table_count": main_table_count,
                "all_physical_table_count": int(provenance_counts["table"]),
                "provenance_item_count": sum(provenance_counts.values()),
                "provenance_missing_count": sum(provenance_missing.values()),
                "elided_alternative_representation_count": elided_alternative_representation_count,
                "metadata_mismatch_count": metadata_mismatch_count,
            }
            for name, expected in EXPECTED_CORPUS_METRICS.items():
                if observed_metrics[name] != expected:
                    _record_failure(failures, f"CORPUS_METRIC_MISMATCH:{name}", expected=expected, observed=observed_metrics[name])
            observed_parse = _counter_dict(parse_status_counts, include_zero=("success", "partial", "failed"))
            if observed_parse != EXPECTED_PARSE_STATUS_COUNTS:
                _record_failure(failures, "PARSE_STATUS_COUNT_MISMATCH", expected=EXPECTED_PARSE_STATUS_COUNTS, observed=observed_parse)
            if _counter_dict(group_counts) != EXPECTED_GROUP_COUNTS:
                _record_failure(failures, "DOC_GROUP_COUNT_MISMATCH", expected=EXPECTED_GROUP_COUNTS, observed=_counter_dict(group_counts))
            if _counter_dict(lineage_status_counts) != EXPECTED_LINEAGE_STATUS_COUNTS:
                _record_failure(failures, "LINEAGE_STATUS_COUNT_MISMATCH", expected=EXPECTED_LINEAGE_STATUS_COUNTS, observed=_counter_dict(lineage_status_counts))
            observed_chains = {"complete": complete_chain_count, "partial": partial_chain_count, "total": len(chains)}
            if observed_chains != EXPECTED_CHAIN_COUNTS:
                _record_failure(failures, "CHAIN_COUNT_MISMATCH", expected=EXPECTED_CHAIN_COUNTS, observed=observed_chains)

        selected, missing_coverage = select_representative_sample(
            descriptors,
            requested_count=sample_count,
            enforce_requirements=enforce_sample_coverage,
        )
        if enforce_sample_coverage and missing_coverage:
            _record_failure(failures, "SAMPLE_COVERAGE_MISSING", expected=0, observed=len(missing_coverage), details=missing_coverage)

        if any(
            initial_stats[path] != (path.stat().st_size, path.stat().st_mtime_ns)
            for path in initial_stats
        ):
            _record_failure(failures, "CANONICAL_INPUT_MUTATED_DURING_RUN", expected="unchanged", observed="changed")

        if failures:
            _write_failures_direct(reports, failures)
            raise FinalValidationError(
                f"FINAL_VALIDATION_FAILED: {len(failures)} quality-gate failure(s); see {reports / 'final_validation_failures.jsonl'}"
            )

        staged_sample = stage_handoff / "sample_disclosures.jsonl"
        staged_handoff_index = stage_handoff / DOCUMENT_INDEX_FILENAME
        staged_sample_index = stage_handoff / "sample_document_index.jsonl"
        staged_selection = stage_handoff / "sample_selection.jsonl"
        shutil.copyfile(staged_index, staged_handoff_index)
        sample_sha, sample_size = _copy_sample_lines(parsed, staged_sample, selected)
        selected_index_rows = [item["index_row"] for item in selected]
        sample_index_sha, sample_index_size = _write_jsonl(staged_sample_index, selected_index_rows)
        selection_rows = [
            {
                "schema_version": PHASE5_SCHEMA_VERSION,
                "doc_id": item["doc_id"],
                "rcept_no": item["rcept_no"],
                "source_line_number": item["line_number"],
                "source_byte_offset": item["byte_offset"],
                "source_line_size": item["line_size"],
                "source_line_sha256": item["source_line_sha256"],
                "selection_reasons": sorted(item["reasons"]),
                "traits": sorted(item["traits"]),
            }
            for item in selected
        ]
        selection_sha, selection_size = _write_jsonl(staged_selection, selection_rows)

        with parser_summary_file.open("r", encoding="utf-8") as stream:
            parser_summary = json.load(stream)
        parser_extraction = parser_summary.get("extraction", {})
        parser_report_provenance = parser_extraction.get("provenance_missing_count")
        if parser_report_provenance != 0:
            _record_failure(failures, "PHASE2_REPORTED_PROVENANCE_MISSING", expected=0, observed=parser_report_provenance)
        phase2_reported_provenance = int(parser_extraction.get("provenance_item_count") or 0)
        content_quality["phase2_reported_provenance_item_count"] = phase2_reported_provenance
        content_quality["elided_alternative_provenance_item_count"] = (
            phase2_reported_provenance - content_quality["provenance_item_count"]
        )
        if strict_corpus_metrics and (
            phase2_reported_provenance != 41_660_802
            or content_quality["elided_alternative_provenance_item_count"] != 930
        ):
            _record_failure(
                failures,
                "PHASE2_PROVENANCE_ELISION_ACCOUNTING_MISMATCH",
                expected={"phase2_reported": 41_660_802, "elided": 930},
                observed={
                    "phase2_reported": phase2_reported_provenance,
                    "elided": content_quality["elided_alternative_provenance_item_count"],
                },
            )
        if failures:
            _write_failures_direct(reports, failures)
            raise FinalValidationError(
                f"FINAL_VALIDATION_FAILED: {len(failures)} quality-gate failure(s)"
            )

        base_summary: dict[str, Any] = {
            "schema_version": PHASE5_SCHEMA_VERSION,
            "generated_at_utc": _utc_now().isoformat(),
            "status": "PASS",
            "execution": {
                "started_at_utc": started.isoformat(),
                "finished_at_utc": _utc_now().isoformat(),
                "duration_seconds": round((_utc_now() - started).total_seconds(), 2),
                "python_version": platform.python_version(),
                "python_executable": sys.executable,
                "platform": platform.platform(),
                "streaming_jsonl": True,
                "full_content_loaded_into_memory": False,
                "materialize_merged": materialize_merged,
            },
            "source": source_info,
            "corpus": {
                "document_count": index_count,
                "company_count": len(companies - {""}),
                "doc_group_counts": _counter_dict(group_counts),
            },
            "parse_status_counts": _counter_dict(parse_status_counts, include_zero=("success", "partial", "failed")),
            "joins": {
                "join_key": "doc_id",
                "secondary_check": "rcept_no",
                "manifest_count": len(manifest.records),
                "metadata_count": len(metadata_rows),
                "parsed_count": len(parsed_ids),
                "document_index_count": index_count,
                "matched_count": index_count,
                "failure_count": 0,
                "duplicate_doc_id_count": 0,
                "duplicate_rcept_no_count": 0,
                "canonical_metadata_mismatch_count": 0,
            },
            "content_quality": content_quality,
            "metadata_quality": {
                "status_counts": _counter_dict(metadata_status_counts),
                "mismatch_count": metadata_mismatch_count,
                "invalid_count": metadata_status_counts.get("invalid", 0),
                "canonical_source": "manifest.jsonl",
            },
            "lineage_quality": lineage_quality,
            "handoff": {
                "path": "handoff/role2",
                "sample_count": len(selected),
                "requested_sample_count": sample_count,
                "missing_coverage": missing_coverage,
                "sample_size_bytes": sample_size,
                "sample_sha256": sample_sha,
                "sample_document_index_size_bytes": sample_index_size,
                "sample_document_index_sha256": sample_index_sha,
                "sample_selection_size_bytes": selection_size,
                "sample_selection_sha256": selection_sha,
            },
        }

        limitations = _known_limitations(base_summary)
        staged_limitations_json.write_bytes(_json_bytes({"schema_version": PHASE5_SCHEMA_VERSION, "known_limitations": limitations}, pretty=True))
        staged_handoff_limitations = stage_handoff / "known_limitations.md"
        limitation_lines = ["# Known limitations", ""]
        for item in limitations:
            count = item.get("count", item.get("document_count"))
            suffix = f" ({count:,})" if isinstance(count, int) else ""
            limitation_lines.extend([f"## {item['code']}{suffix}", "", str(item["impact"]), ""])
        staged_handoff_limitations.write_text("\n".join(limitation_lines), encoding="utf-8", newline="\n")
        staged_handoff_contract = stage_handoff / "data_contract.md"
        shutil.copyfile(contract, staged_handoff_contract)
        staged_integration = stage_handoff / "integration_notes.md"
        staged_integration.write_text(_integration_notes(), encoding="utf-8", newline="\n")

        artifacts: dict[str, Any] = {
            "parsed_disclosures": _artifact_info(
                project_root=root,
                path=parsed,
                schema_version="2.0.0",
                record_count=len(parsed_ids),
                sha256=parsed_sha,
                purpose="Canonical evidence-preserving structured content; not copied into handoff/role2.",
            ),
            "normalized_metadata": _artifact_info(
                project_root=root,
                path=metadata_file,
                schema_version="3.0.0",
                record_count=len(metadata_rows),
                sha256=metadata_sha,
                purpose="Canonical manifest-authoritative metadata sidecar.",
            ),
            "correction_lineage": _artifact_info(
                project_root=root,
                path=lineage_file,
                schema_version="4.0.0",
                record_count=len(lineage_rows),
                sha256=lineage_sha,
                purpose="One evidence-preserving lineage resolution row per correction.",
            ),
            "correction_chains": _artifact_info(
                project_root=root,
                path=chains_file,
                schema_version="4.0.0",
                record_count=len(chains),
                sha256=chains_sha,
                purpose="Complete and partial correction-chain components.",
            ),
            "document_index": _artifact_info(
                project_root=root,
                path=staged_index,
                schema_version=PHASE5_SCHEMA_VERSION,
                record_count=index_count,
                sha256=index_digest.hexdigest(),
                purpose="Lightweight DB/search index joining canonical metadata, structure, lineage safety, and quality flags.",
            ),
        }
        artifacts["document_index"]["path"] = "data/final/document_index.jsonl"
        if materialize_merged:
            artifacts["merged_disclosures"] = _artifact_info(
                project_root=root,
                path=staged_merged,
                schema_version=PHASE5_SCHEMA_VERSION,
                record_count=merged_count,
                sha256=merged_digest.hexdigest(),
                purpose="Optional atomically materialized Phase 5 merged dataset.",
            )
            artifacts["merged_disclosures"]["path"] = "data/final/merged_disclosures.jsonl"
        base_summary["artifacts"] = artifacts
        quality_gates = {
            "canonical_hashes_match": True,
            "canonical_inputs_unchanged": True,
            "schema_versions_valid": True,
            "all_jsonl_valid": True,
            "manifest_metadata_parsed_index_one_to_one": True,
            "rcept_no_secondary_join_valid": True,
            "document_index_count_exact": index_count == expected_document_count,
            "metadata_invalid_zero": metadata_status_counts.get("invalid", 0) == 0,
            "parse_failed_zero": parse_status_counts.get("failed", 0) == 0,
            "source_sha256_complete": source_sha256_count == physical_source_count,
            "provenance_missing_zero": sum(provenance_missing.values()) == 0,
            "phase2_reported_provenance_missing_zero": parser_report_provenance == 0,
            "phase4_graph_invariants_pass": bool(graph_validation.get("passed")),
            "unsafe_latest_boolean_zero": len(invalid_complete_flag_ids) == 0,
            "sample_coverage_complete": not missing_coverage or not enforce_sample_coverage,
            "merged_not_generated_by_default": materialize_merged or not staged_merged.exists(),
        }
        base_summary["quality_gates"] = quality_gates
        if not all(quality_gates.values()):
            for gate, passed in quality_gates.items():
                if not passed:
                    _record_failure(failures, f"QUALITY_GATE_FAILED:{gate}", expected=True, observed=False)
            _write_failures_direct(reports, failures)
            raise FinalValidationError("FINAL_VALIDATION_FAILED: final quality gate did not pass")

        staged_summary.write_bytes(_json_bytes(base_summary, pretty=True))
        staged_report.write_text(_report_markdown(base_summary), encoding="utf-8", newline="\n")
        _write_jsonl(staged_failures, [])
        staged_readme = stage_handoff / "README.md"
        staged_readme.write_text(_handoff_readme(base_summary), encoding="utf-8", newline="\n")

        delivery = {
            "path": "handoff/role2",
            "copied_large_content_dataset": False,
            "files": {
                "document_index.jsonl": {"sha256": index_digest.hexdigest(), "size_bytes": staged_handoff_index.stat().st_size, "record_count": index_count},
                "sample_disclosures.jsonl": {"sha256": sample_sha, "size_bytes": sample_size, "record_count": len(selected)},
                "sample_document_index.jsonl": {"sha256": sample_index_sha, "size_bytes": sample_index_size, "record_count": len(selected)},
                "sample_selection.jsonl": {"sha256": selection_sha, "size_bytes": selection_size, "record_count": len(selected)},
            },
        }
        artifact_manifest = {
            "schema_version": PHASE5_SCHEMA_VERSION,
            "generated_at_utc": base_summary["generated_at_utc"],
            "status": "PASS",
            "document_count": index_count,
            "join_key": "doc_id",
            "secondary_check": "rcept_no",
            "canonical_metadata_source": "manifest.jsonl",
            "source": source_info,
            "artifacts": artifacts,
            "delivery": delivery,
            "known_limitations_path": "reports/final_known_limitations.json",
        }
        staged_manifest.write_bytes(_json_bytes(artifact_manifest, pretty=True))
        staged_handoff_manifest = stage_handoff / ARTIFACT_MANIFEST_FILENAME
        shutil.copyfile(staged_manifest, staged_handoff_manifest)

        final_artifacts = [
            (staged_index, output / DOCUMENT_INDEX_FILENAME),
            (staged_manifest, output / ARTIFACT_MANIFEST_FILENAME),
            (staged_summary, reports / "final_quality_summary.json"),
            (staged_report, reports / "final_quality_report.md"),
            (staged_failures, reports / "final_validation_failures.jsonl"),
            (staged_limitations_json, reports / "final_known_limitations.json"),
            (staged_readme, handoff / "README.md"),
            (staged_handoff_contract, handoff / "data_contract.md"),
            (staged_handoff_manifest, handoff / ARTIFACT_MANIFEST_FILENAME),
            (staged_handoff_index, handoff / DOCUMENT_INDEX_FILENAME),
            (staged_sample, handoff / "sample_disclosures.jsonl"),
            (staged_sample_index, handoff / "sample_document_index.jsonl"),
            (staged_selection, handoff / "sample_selection.jsonl"),
            (staged_handoff_limitations, handoff / "known_limitations.md"),
            (staged_integration, handoff / "integration_notes.md"),
        ]
        if materialize_merged:
            final_artifacts.append((staged_merged, output / "merged_disclosures.jsonl"))
        _publish(final_artifacts)
        shutil.rmtree(stage, ignore_errors=True)
        return base_summary
    except BaseException:
        if index_stream is not None:
            index_stream.close()
        if merged_stream is not None:
            merged_stream.close()
        shutil.rmtree(stage, ignore_errors=True)
        raise


__all__ = [
    "ARTIFACT_MANIFEST_FILENAME",
    "DOCUMENT_INDEX_FILENAME",
    "FinalValidationError",
    "LOCKED_INPUT_HASHES",
    "assemble_final_dataset",
]
