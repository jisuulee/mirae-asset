"""Streaming Phase 4 correction-lineage pipeline."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from disclosure_preprocess.correction_lineage import (
    PHASE4_SCHEMA_VERSION,
    MetadataIndex,
    ResolutionStatus,
    apply_chain_backtrack,
    build_chains,
    classify_correction_kind,
    extract_correction_evidence,
    guard_resolution_cycles,
    report_keys_compatible,
    resolve_one,
    validate_lineage,
)
from disclosure_preprocess.opendart import OpenDartClient, OpenDartError


LOGGER = logging.getLogger(__name__)

LINEAGE_FILENAME = "correction_lineage.jsonl"
CHAINS_FILENAME = "correction_chains.jsonl"


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


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            stream.write(_json_bytes(row))


def _sha256(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _load_metadata(path: Path) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid metadata JSONL line {line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Metadata JSONL line {line_number} is not an object")
            rows.append(row)
    return rows, digest.hexdigest()


def _warning_codes(parsed: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    values = parsed.get("warnings")
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
        for warning in values:
            if isinstance(warning, Mapping) and warning.get("code"):
                result.add(str(warning["code"]))
    return result


def _counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _percentile(values: Sequence[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def _profile(
    corrections: Sequence[Mapping[str, Any]],
    parsed_stats: Mapping[str, Mapping[str, Any]],
    extracted: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    groups: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    fields: Counter[str] = Counter()
    patterns_documents: Counter[str] = Counter()
    patterns_occurrences: Counter[str] = Counter()
    date_semantics: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    sizes: list[int] = []
    before_count = 0
    after_count = 0
    invalid_date_evidence_count = 0
    normalized_target_report_count = 0
    normalized_target_date_count = 0
    documents_without_evidence_count = 0
    explicit_receipt_evidence_document_count = 0
    examples: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in corrections:
        metadata = row["metadata"]
        doc_id = str(row["doc_id"])
        evidence = extracted[doc_id]
        stat = parsed_stats[doc_id]
        groups[str(metadata.get("doc_group"))] += 1
        statuses[str(stat["parse_status"])] += 1
        kind = str(evidence.get("correction_kind") or "unknown")
        kinds[kind] += 1
        sizes.append(int(stat["record_size_bytes"]))
        for warning in stat["warning_codes"]:
            warning_counts[str(warning)] += 1
        observed_fields = {str(item["field"]) for item in evidence["evidence"]}
        documents_without_evidence_count += not bool(evidence["evidence"])
        explicit_receipt_evidence_document_count += any(
            "접수번호" in str(item.get("raw_label") or "")
            and any(character.isdigit() for character in str(item.get("raw_value") or ""))
            for item in evidence["evidence"]
        )
        for field in observed_fields:
            fields[field] += 1
        observed_patterns = set(evidence["pattern_counts"])
        for pattern in observed_patterns:
            patterns_documents[pattern] += 1
        for pattern, count in evidence["pattern_counts"].items():
            patterns_occurrences[pattern] += int(count)
        date_semantics[str(evidence.get("target_date_semantics"))] += 1
        before_count += bool(evidence.get("before_present"))
        after_count += bool(evidence.get("after_present"))
        for item in evidence["evidence"]:
            if str(item["field"]).endswith("_date") and item.get("raw_value") and item.get("normalized_value") is None:
                invalid_date_evidence_count += 1
        reason = evidence.get("correction_reason")
        if reason:
            reason_counts[str(reason)] += 1
        target = evidence.get("target_report_name_raw")
        if target:
            target_counts[str(target)] += 1
        normalized_target_report_count += evidence.get("target_report_key") is not None
        normalized_target_date_count += evidence.get("target_submission_date") is not None
        for field in ("target_report_name", "original_submission_date", "related_disclosure_date", "correction_date", "correction_reason"):
            if len(examples[field]) >= 8:
                continue
            found = next((item for item in evidence["evidence"] if item["field"] == field), None)
            if found:
                examples[field].append({"doc_id": doc_id, "report_nm": metadata.get("report_nm"), "evidence": found})

    return {
        "schema_version": PHASE4_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "correction_count": len(corrections),
        "doc_group_counts": _counter(groups),
        "parse_status_counts": _counter(statuses),
        "correction_kind_counts": _counter(kinds),
        "evidence_field_document_counts": _counter(fields),
        "pattern_document_counts": _counter(patterns_documents),
        "pattern_occurrence_counts": _counter(patterns_occurrences),
        "target_date_semantics_counts": _counter(date_semantics),
        "before_document_count": before_count,
        "after_document_count": after_count,
        "documents_without_target_report": len(corrections) - normalized_target_report_count,
        "documents_without_target_date": len(corrections) - normalized_target_date_count,
        "documents_without_profiled_evidence": documents_without_evidence_count,
        "explicit_receipt_evidence_document_count": explicit_receipt_evidence_document_count,
        "invalid_date_evidence_count": invalid_date_evidence_count,
        "warning_document_counts": _counter(warning_counts),
        "parsed_record_size_bytes": {
            "min": min(sizes) if sizes else 0,
            "median": _percentile(sizes, 0.5),
            "p95": _percentile(sizes, 0.95),
            "max": max(sizes) if sizes else 0,
        },
        "top_target_report_values": [{"value": value, "count": count} for value, count in target_counts.most_common(30)],
        "top_correction_reason_values": [{"value": value, "count": count} for value, count in reason_counts.most_common(30)],
        "examples": dict(examples),
    }


def _apply_opendart(
    records: list[dict[str, Any]],
    index: MetadataIndex,
    client: OpenDartClient,
) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for record in records:
        if record["resolution"]["status"] not in {ResolutionStatus.AMBIGUOUS, ResolutionStatus.UNRESOLVED}:
            continue
        evidence = record["correction_evidence"]
        target_date = evidence.get("target_submission_date")
        corp_code = record["metadata_snapshot"].get("corp_code")
        if not target_date or not corp_code:
            continue
        log: dict[str, Any] = {
            "doc_id": record["doc_id"],
            "corp_code": corp_code,
            "target_date": target_date,
            "status": None,
            "cache_key": None,
            "candidate_count": 0,
            "error_code": None,
        }
        try:
            result = client.list_filings(corp_code=str(corp_code), begin_date=str(target_date), end_date=str(target_date))
            log["source"] = result["source"]
            log["cache_key"] = result["cache_key"]
            response = result["response"]
            items = response.get("list") if isinstance(response, Mapping) else None
            filings = [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []
            compatible = [
                item for item in filings
                if str(item.get("rcept_no") or "") != record["rcept_no"]
                and report_keys_compatible(item.get("report_nm"), evidence.get("target_report_name_raw") or record["metadata_snapshot"].get("report_nm"))
            ]
            log["candidate_count"] = len(compatible)
            if len(compatible) == 1:
                target = compatible[0]
                target_receipt = str(target.get("rcept_no") or "")
                record["resolution"].update({
                    "status": str(ResolutionStatus.LINKED_EXACT if target_receipt in index.by_rcept_no else ResolutionStatus.EXTERNAL_TARGET),
                    "method": "opendart_company_date_report",
                    "confidence": 1.0,
                    "target_rcept_no": target_receipt,
                    "target_doc_id": index.by_rcept_no.get(target_receipt, {}).get("doc_id"),
                    "candidate_count": 1,
                    "candidates": [{"rcept_no": target_receipt, "report_nm": target.get("report_nm"), "source": "OpenDART"}],
                    "external_lookup": {"enabled": True, "status": "matched", "cache_key": result["cache_key"]},
                })
                log["status"] = "matched"
            elif len(compatible) > 1:
                record["resolution"].update({"status": str(ResolutionStatus.AMBIGUOUS), "method": "opendart_multiple_candidates", "external_lookup": {"enabled": True, "status": "ambiguous", "cache_key": result["cache_key"]}})
                log["status"] = "ambiguous"
            else:
                record["resolution"]["external_lookup"] = {"enabled": True, "status": "no_match", "cache_key": result["cache_key"]}
                log["status"] = "no_match"
        except OpenDartError as exc:
            record["resolution"]["external_lookup"] = {"enabled": True, "status": "error", "error_code": exc.code}
            log["status"] = "error"
            log["error_code"] = exc.code
        logs.append(log)
    return logs


def _resolution_summary(records: Sequence[Mapping[str, Any]], chains: Sequence[Mapping[str, Any]], validation: Mapping[str, Any]) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    by_group: defaultdict[str, Counter[str]] = defaultdict(Counter)
    kinds: Counter[str] = Counter()
    for record in records:
        status = str(record["resolution"]["status"])
        statuses[status] += 1
        methods[str(record["resolution"].get("method") or "none")] += 1
        by_group[str(record["metadata_snapshot"].get("doc_group"))][status] += 1
        kinds[str(record["correction_kind"])] += 1
    lengths = Counter(str(chain["member_count"]) for chain in chains)
    root_count = sum(record.get("lineage", {}).get("root_rcept_no") is not None for record in records)
    safe_root_count = sum(
        record.get("lineage", {}).get("root_rcept_no") is not None
        and record["resolution"]["status"] in {ResolutionStatus.LINKED_EXACT, ResolutionStatus.LINKED_INFERRED}
        for record in records
    )
    parent_count = sum(record.get("lineage", {}).get("parent_rcept_no") is not None for record in records)
    total = len(records) or 1
    return {
        "correction_count": len(records),
        "status_counts": _counter(statuses),
        "method_counts": _counter(methods),
        "status_by_doc_group": {group: _counter(values) for group, values in sorted(by_group.items())},
        "correction_kind_counts": _counter(kinds),
        "linked_count": statuses[ResolutionStatus.LINKED_EXACT] + statuses[ResolutionStatus.LINKED_INFERRED] + statuses[ResolutionStatus.EXTERNAL_TARGET],
        "ambiguous_count": statuses[ResolutionStatus.AMBIGUOUS],
        "unresolved_count": statuses[ResolutionStatus.UNRESOLVED],
        "external_target_count": statuses[ResolutionStatus.EXTERNAL_TARGET],
        "chain_count": len(chains),
        "chain_length_distribution": _counter(lengths),
        "maximum_chain_length": max((int(chain["member_count"]) for chain in chains), default=0),
        "complete_chain_count": sum(chain.get("is_complete") is True for chain in chains),
        "partial_chain_count": sum(chain.get("is_complete") is False for chain in chains),
        "chain_assigned_correction_count": sum(record.get("lineage", {}).get("chain_id") is not None for record in records),
        "root_populated_count": root_count,
        "root_populated_percent": round(root_count * 100 / total, 2),
        "safe_root_count": safe_root_count,
        "safe_root_percent": round(safe_root_count * 100 / total, 2),
        "parent_confirmed_count": parent_count,
        "parent_confirmed_percent": round(parent_count * 100 / total, 2),
        "complete_chain_correction_count": sum(int(chain["correction_count"]) for chain in chains if chain.get("is_complete") is True),
        "invariants": dict(validation),
    }


def _exception_analysis(records: Sequence[Mapping[str, Any]], index: MetadataIndex) -> dict[str, Any]:
    company_dates: defaultdict[str, list[str]] = defaultdict(list)
    for metadata in index.by_doc_id.values():
        if metadata.get("corp_code") and metadata.get("rcept_date"):
            company_dates[str(metadata["corp_code"])].append(str(metadata["rcept_date"]))
    unresolved_reasons: Counter[str] = Counter()
    ambiguous_candidates: Counter[str] = Counter()
    ambiguous_reasons: Counter[str] = Counter()
    unresolved_examples: list[dict[str, Any]] = []
    ambiguous_examples: list[dict[str, Any]] = []
    for record in records:
        status = record["resolution"]["status"]
        if status == ResolutionStatus.UNRESOLVED:
            evidence = record["correction_evidence"]
            target_date = evidence.get("target_submission_date")
            if not target_date:
                reason = "TARGET_DATE_INVALID_OR_MISSING"
            else:
                dates = company_dates.get(str(record["metadata_snapshot"].get("corp_code") or ""), [])
                if dates and (str(target_date) < min(dates) or str(target_date) > max(dates)):
                    reason = "TARGET_DATE_OUTSIDE_COMPANY_CORPUS_RANGE"
                else:
                    reason = "NO_COMPANY_DATE_RECORD_IN_CORPUS"
            unresolved_reasons[reason] += 1
            if len(unresolved_examples) < 30:
                unresolved_examples.append({
                    "doc_id": record["doc_id"],
                    "doc_group": record["metadata_snapshot"].get("doc_group"),
                    "report_nm": record["metadata_snapshot"].get("report_nm"),
                    "target_report_raw": evidence.get("target_report_name_raw"),
                    "target_date_raw": evidence.get("target_submission_date_raw"),
                    "target_date": target_date,
                    "reason_code": reason,
                })
        elif status == ResolutionStatus.AMBIGUOUS:
            count = int(record["resolution"].get("candidate_count") or 0)
            ambiguous_candidates[str(count)] += 1
            reason = (
                "GLOBAL_TARGET_CYCLE"
                if record["resolution"].get("method") == "cycle_guard_ambiguous"
                else "MULTIPLE_DETERMINISTIC_CANDIDATES"
            )
            ambiguous_reasons[reason] += 1
            if len(ambiguous_examples) < 30:
                ambiguous_examples.append({
                    "doc_id": record["doc_id"],
                    "doc_group": record["metadata_snapshot"].get("doc_group"),
                    "report_nm": record["metadata_snapshot"].get("report_nm"),
                    "target_date": record["correction_evidence"].get("target_submission_date"),
                    "candidate_rcept_nos": [candidate.get("rcept_no") for candidate in record["resolution"].get("candidates", [])],
                    "candidate_count": count,
                    "reason_code": reason,
                })
    manual_samples: list[dict[str, Any]] = []
    seen_buckets: set[tuple[str, str]] = set()
    for record in sorted(records, key=lambda row: str(row["doc_id"])):
        bucket = (str(record["metadata_snapshot"].get("doc_group")), str(record["resolution"]["status"]))
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)
        manual_samples.append({
            "doc_id": record["doc_id"],
            "doc_group": bucket[0],
            "status": bucket[1],
            "method": record["resolution"].get("method"),
            "target_rcept_no": record["resolution"].get("target_rcept_no"),
            "root_rcept_no": record["lineage"].get("root_rcept_no"),
            "target_report_raw": record["correction_evidence"].get("target_report_name_raw"),
            "target_date_raw": record["correction_evidence"].get("target_submission_date_raw"),
            "target_date": record["correction_evidence"].get("target_submission_date"),
            "evidence_count": record["correction_evidence"].get("evidence_count"),
        })
    return {
        "unresolved_reason_counts": _counter(unresolved_reasons),
        "ambiguous_candidate_count_distribution": _counter(ambiguous_candidates),
        "ambiguous_reason_counts": _counter(ambiguous_reasons),
        "unresolved_examples": unresolved_examples,
        "ambiguous_examples": ambiguous_examples,
        "deterministic_manual_review_samples": manual_samples,
        "suspected_wrong_link_count": 0,
        "suspected_wrong_link_doc_ids": [],
        "suspected_wrong_link_basis": "cross-company, future-target, missing-target, cycle, and chain invariants all passed",
    }


def _profile_markdown(profile: Mapping[str, Any]) -> str:
    lines = [
        "# Phase 4 correction structure profile", "",
        f"- 정정공시: **{profile['correction_count']:,}건**", "",
        "## 문서군", "", "| doc_group | 건수 |", "| --- | ---: |",
    ]
    lines.extend(f"| {key} | {value:,} |" for key, value in profile["doc_group_counts"].items())
    lines.extend(["", "## 실제 정정 증거 필드", "", "| 필드 | 문서 수 |", "| --- | ---: |"])
    lines.extend(f"| `{key}` | {value:,} |" for key, value in profile["evidence_field_document_counts"].items())
    lines.extend([
        "", "## 관측 결론", "",
        f"- `정정대상 공시서류의 최초제출일` 계열: **{profile['target_date_semantics_counts'].get('original_submission_date', 0):,}건**",
        f"- `정정관련 공시서류제출일` 계열: **{profile['target_date_semantics_counts'].get('related_disclosure_date', 0):,}건**",
        f"- 대상 보고서 미추출: **{profile['documents_without_target_report']:,}건**",
        f"- 대상 날짜 미추출: **{profile['documents_without_target_date']:,}건**",
        f"- 정정 전/후 표식: **{profile['before_document_count']:,}/{profile['after_document_count']:,}건**",
        "", "날짜와 보고서명은 원문과 정규화값을 함께 보존한다. 본문 전체에서 우연히 나타난 숫자는 명시적 접수번호로 간주하지 않는다.", "",
    ])
    lines.extend(["## 위치별 패턴", "", "| 패턴 | 문서 수 | 발생 수 |", "| --- | ---: | ---: |"])
    pattern_names = sorted(set(profile["pattern_document_counts"]) | set(profile["pattern_occurrence_counts"]))
    lines.extend(
        f"| `{name}` | {profile['pattern_document_counts'].get(name, 0):,} | {profile['pattern_occurrence_counts'].get(name, 0):,} |"
        for name in pattern_names
    )
    lines.extend(["", "## Parse status", "", "| status | 건수 |", "| --- | ---: |"])
    lines.extend(f"| `{key}` | {value:,} |" for key, value in profile["parse_status_counts"].items())
    lines.extend(["", "## 정정 종류", "", "| kind | 건수 |", "| --- | ---: |"])
    lines.extend(f"| `{key}` | {value:,} |" for key, value in profile["correction_kind_counts"].items())
    lines.extend(["", "대표 원문/정규화값과 provenance는 `correction_structure_summary.json`의 `examples`에 수록했다.", ""])
    return "\n".join(lines)


def _report_markdown(summary: Mapping[str, Any]) -> str:
    resolution = summary["resolution"]
    lines = [
        "# Phase 4 correction lineage report", "",
        f"- schema: `{summary['schema_version']}`",
        f"- 실행 모드: `{summary['execution']['mode']}`",
        f"- 정정공시 입력/출력: **{summary['corpus']['correction_count']:,}/{summary['corpus']['lineage_record_count']:,}건**",
        "", "## 연결 결과", "", "| status | 건수 |", "| --- | ---: |",
    ]
    for key in ("linked_exact", "linked_inferred", "external_target", "ambiguous", "unresolved"):
        lines.append(f"| `{key}` | {resolution['status_counts'].get(key, 0):,} |")
    lines.extend([
        "", "## Chain", "",
        f"- chain 수: **{resolution['chain_count']:,}**",
        f"- complete / partial chain: **{resolution['complete_chain_count']:,} / {resolution['partial_chain_count']:,}**",
        f"- status gate 적용 후 안전한 root: **{resolution['safe_root_count']:,}건 ({resolution['safe_root_percent']:.2f}%)**",
        f"- parent 확인: **{resolution['parent_confirmed_count']:,}건 ({resolution['parent_confirmed_percent']:.2f}%)**",
        f"- 최대 chain 길이(원공시 포함): **{resolution['maximum_chain_length']:,}**",
        f"- invariant: **{'통과' if resolution['invariants']['passed'] else '실패'}**",
        f"- cross-company / evidence-less / cycle: **{resolution['invariants']['cross_company_link_count']} / {resolution['invariants']['evidence_less_link_count']} / {resolution['invariants']['cycle_count']}**",
        "", "## Resolver method", "", "| method | 건수 |", "| --- | ---: |",
    ])
    lines.extend(f"| `{key}` | {value:,} |" for key, value in resolution["method_counts"].items())
    lines.extend([
        "", "## 미확정 원인", "", "| unresolved reason | 건수 |", "| --- | ---: |",
    ])
    lines.extend(f"| `{key}` | {value:,} |" for key, value in summary["exception_analysis"]["unresolved_reason_counts"].items())
    lines.extend([
        "", "| ambiguous candidate 수 | 문서 수 |", "| ---: | ---: |",
    ])
    lines.extend(f"| {key} | {value:,} |" for key, value in summary["exception_analysis"]["ambiguous_candidate_count_distribution"].items())
    lines.extend(["", "| ambiguous reason | 건수 |", "| --- | ---: |"])
    lines.extend(f"| `{key}` | {value:,} |" for key, value in summary["exception_analysis"].get("ambiguous_reason_counts", {}).items())
    lines.extend([
        "", "## 판정 원칙", "",
        "명시적 접수번호, 회사+명시 날짜+보고서명, 회사+날짜+공시분류의 순서로만 연결한다. 단일 후보가 아닐 때는 `ambiguous`, 후보가 없으면 `unresolved`이며 가장 가까운 이전 공시를 임의 선택하지 않는다.",
        "", "## 알려진 한계", "",
        "- corpus 밖 원공시는 corpus-only 모드에서 확인할 수 없다.",
        "- 같은 회사·날짜·유형에 복수 후보가 남으면 자동 연결하지 않는다.",
        "- `is_correction=true`는 정정 여부일 뿐 원공시 연결을 뜻하지 않는다.", "",
        "상세 후보와 근거는 `correction_ambiguous.jsonl`, `correction_unresolved.jsonl`에, 상태·문서군별 결정론적 검토 샘플은 `correction_summary.json`에 보존했다.", "",
    ])
    return "\n".join(lines)


def _publish(stage: Path, artifacts: Sequence[tuple[Path, Path]]) -> None:
    backup = stage / "previous-generation"
    backup.mkdir()
    prior: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for index, (_, final) in enumerate(artifacts):
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                target = backup / f"{index:03d}-{final.name}"
                os.replace(final, target)
                prior[final] = target
        for staged, final in artifacts:
            os.replace(staged, final)
            published.append(final)
    except BaseException:
        for final in reversed(published):
            final.unlink(missing_ok=True)
        for final, old in prior.items():
            if old.exists():
                os.replace(old, final)
        raise
    shutil.rmtree(stage)


def resolve_correction_corpus(
    *,
    metadata_path: str | Path,
    parsed_path: str | Path,
    output_dir: str | Path,
    report_dir: str | Path,
    expected_document_count: int | None = 4204,
    expected_correction_count: int | None = 1004,
    progress_every: int = 100,
    enable_opendart: bool = False,
    opendart_api_key: str | None = None,
    opendart_cache_dir: str | Path | None = None,
    opendart_transport: Any = None,
) -> dict[str, Any]:
    metadata_path = Path(metadata_path).resolve()
    parsed_path = Path(parsed_path).resolve()
    output_dir = Path(output_dir).resolve()
    report_dir = Path(report_dir).resolve()
    if metadata_path == parsed_path:
        raise ValueError("metadata and parsed inputs must be distinct")
    protected = {metadata_path, parsed_path}
    finals = {
        "lineage": output_dir / LINEAGE_FILENAME,
        "chains": output_dir / CHAINS_FILENAME,
        "profile_json": report_dir / "correction_structure_summary.json",
        "profile_md": report_dir / "correction_structure_profile.md",
        "summary_json": report_dir / "correction_summary.json",
        "report_md": report_dir / "correction_report.md",
        "ambiguous": report_dir / "correction_ambiguous.jsonl",
        "unresolved": report_dir / "correction_unresolved.jsonl",
        "external": report_dir / "correction_external_lookup.jsonl",
    }
    if any(path in protected for path in finals.values()):
        raise ValueError("Phase 4 outputs must not overwrite Phase 2/3 inputs")
    if enable_opendart and not opendart_api_key:
        raise ValueError("--enable-opendart requires OPEN_DART_API_KEY")

    metadata_size = metadata_path.stat().st_size
    parsed_size = parsed_path.stat().st_size
    metadata_rows, metadata_sha = _load_metadata(metadata_path)
    if expected_document_count is not None and len(metadata_rows) != expected_document_count:
        raise RuntimeError(f"metadata count {len(metadata_rows)} != expected {expected_document_count}")
    index = MetadataIndex.build(metadata_rows)
    corrections = [row for row in metadata_rows if row.get("metadata", {}).get("is_correction") is True]
    if expected_correction_count is not None and len(corrections) != expected_correction_count:
        raise RuntimeError(f"correction count {len(corrections)} != expected {expected_correction_count}")
    correction_by_id = {str(row["doc_id"]): row for row in corrections}

    parsed_digest = hashlib.sha256()
    parsed_count = 0
    parsed_stats: dict[str, dict[str, Any]] = {}
    extracted: dict[str, dict[str, Any]] = {}
    with parsed_path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            parsed_digest.update(raw_line)
            if not raw_line.strip():
                continue
            parsed_count += 1
            try:
                parsed = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid parsed JSONL line {line_number}: {exc}") from exc
            doc_id = str(parsed.get("doc_id") or "")
            metadata = index.by_doc_id.get(doc_id)
            if metadata is None:
                raise RuntimeError(f"Parsed content has no Phase 3 metadata: {doc_id}")
            if str(parsed.get("rcept_no") or "") != str(metadata.get("rcept_no") or ""):
                raise RuntimeError(f"rcept_no mismatch for {doc_id}")
            if doc_id in correction_by_id:
                evidence = extract_correction_evidence(parsed)
                evidence["correction_kind"] = str(
                    classify_correction_kind(
                        correction_by_id[doc_id].get("metadata", {}).get("report_nm")
                    )
                )
                extracted[doc_id] = evidence
                parsed_stats[doc_id] = {
                    "parse_status": parsed.get("parse_status"),
                    "warning_codes": sorted(_warning_codes(parsed)),
                    "record_size_bytes": len(raw_line),
                    "line_number": line_number,
                }
                if progress_every and len(extracted) % progress_every == 0:
                    LOGGER.info("Profiled %d/%d correction disclosures", len(extracted), len(corrections))
    if parsed_count != len(metadata_rows):
        raise RuntimeError(f"parsed count {parsed_count} != metadata count {len(metadata_rows)}")
    missing = sorted(set(correction_by_id) - set(extracted))
    if missing:
        raise RuntimeError(f"Correction content join failed for {len(missing)} documents: {missing[:10]}")

    profile = _profile(corrections, parsed_stats, extracted)
    records = [resolve_one(row, extracted[str(row["doc_id"])], index) for row in corrections]
    records.sort(key=lambda row: str(row["doc_id"]))
    external_logs: list[dict[str, Any]] = []
    if enable_opendart:
        client = OpenDartClient(
            str(opendart_api_key),
            opendart_cache_dir or output_dir.parent / "cache" / "opendart",
            transport=opendart_transport,
        )
        external_logs = _apply_opendart(records, index, client)
    guarded_cycles = guard_resolution_cycles(records)
    apply_chain_backtrack(records, index)
    chains = build_chains(records, index)
    validation = validate_lineage(records, chains, index)
    if not validation["passed"]:
        raise RuntimeError(f"Phase 4 invariant validation failed: {validation['errors'][:5]}")
    resolution = _resolution_summary(records, chains, validation)
    exception_analysis = _exception_analysis(records, index)
    summary = {
        "schema_version": PHASE4_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "execution": {
            "mode": "opendart-enabled" if enable_opendart else "corpus-only",
            "opendart_enabled": enable_opendart,
            "api_key_present": bool(opendart_api_key),
            "external_lookup_attempt_count": len(external_logs),
        },
        "corpus": {
            "metadata_record_count": len(metadata_rows),
            "parsed_record_count": parsed_count,
            "correction_count": len(corrections),
            "correction_content_join_count": len(extracted),
            "lineage_record_count": len(records),
            "metadata_path": str(metadata_path),
            "metadata_size_bytes": metadata_size,
            "metadata_sha256": metadata_sha,
            "parsed_path": str(parsed_path),
            "parsed_size_bytes": parsed_size,
            "parsed_sha256": parsed_digest.hexdigest(),
        },
        "profile": profile,
        "resolution": resolution,
        "cycle_guard": {
            "detected_and_demoted_cycle_count": len(guarded_cycles),
            "detected_and_demoted_cycles": guarded_cycles,
        },
        "exception_analysis": exception_analysis,
        "quality_gates": {
            "metadata_input_unchanged": metadata_path.stat().st_size == metadata_size,
            "parsed_input_unchanged": parsed_path.stat().st_size == parsed_size,
            "all_corrections_joined": len(extracted) == len(corrections),
            "one_lineage_row_per_correction": len(records) == len(corrections),
            "invariants_passed": validation["passed"],
        },
        "known_limitations": [
            "Corpus-only mode cannot identify targets outside the 4,204-document corpus.",
            "Multiple candidates are retained as ambiguous; proximity is never used as an automatic tie-breaker.",
            "Correction reason extraction is descriptive evidence and is not used as a semantic linkage signal.",
        ],
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(f"Phase 4 quality gate failed: {summary['quality_gates']}")

    stage = Path(tempfile.mkdtemp(prefix=".phase4-", dir=output_dir.parent))
    try:
        staged = {name: stage / final.name for name, final in finals.items()}
        _write_jsonl(staged["lineage"], records)
        _write_jsonl(staged["chains"], chains)
        staged["profile_json"].write_bytes(_json_bytes(profile, pretty=True))
        staged["profile_md"].write_text(_profile_markdown(profile), encoding="utf-8", newline="\n")
        staged["summary_json"].write_bytes(_json_bytes(summary, pretty=True))
        staged["report_md"].write_text(_report_markdown(summary), encoding="utf-8", newline="\n")
        _write_jsonl(staged["ambiguous"], [row for row in records if row["resolution"]["status"] == ResolutionStatus.AMBIGUOUS])
        _write_jsonl(staged["unresolved"], [row for row in records if row["resolution"]["status"] == ResolutionStatus.UNRESOLVED])
        _write_jsonl(staged["external"], external_logs)
        artifacts = [(staged[name], final) for name, final in finals.items()]
        _publish(stage, artifacts)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return summary


__all__ = ["resolve_correction_corpus"]
