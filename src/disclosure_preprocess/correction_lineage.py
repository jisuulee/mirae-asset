"""Evidence-preserving correction extraction and deterministic lineage resolution."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any


PHASE4_SCHEMA_VERSION = "4.0.0"
RESOLVER_POLICY_VERSION = "corpus-deterministic-v1"


class ResolutionStatus(StrEnum):
    LINKED_EXACT = "linked_exact"
    LINKED_INFERRED = "linked_inferred"
    EXTERNAL_TARGET = "external_target"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class CorrectionKind(StrEnum):
    STATEMENT = "기재정정"
    ATTACHMENT = "첨부정정"
    ORDER = "정정명령"
    WITHDRAWAL = "철회"
    OTHER = "기타정정"
    UNKNOWN = "unknown"


_CORRECTION_PREFIX = re.compile(r"^\s*\[\s*([^\]]*?(?:정정|철회)[^\]]*)\s*\]\s*")
_SPACE = re.compile(r"\s+")
_REPORT_KEY = re.compile(r"[^0-9A-Za-z가-힣]+")
_RCEPT_NO = re.compile(r"(?<!\d)(\d{14})(?!\d)")
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
    re.compile(r"(?<!\d)(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"),
    re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"),
)


def normalize_text(value: object) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFC", str(value))
    text = _SPACE.sub(" ", text).strip()
    return text or None


def normalize_report_key(value: object) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    text = _CORRECTION_PREFIX.sub("", text)
    key = _REPORT_KEY.sub("", text).casefold()
    return key or None


def report_keys_compatible(left: object, right: object) -> bool:
    left_key = normalize_report_key(left)
    right_key = normalize_report_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    # Source labels commonly omit a period suffix or holding-report form.
    shorter, longer = sorted((left_key, right_key), key=len)
    return len(shorter) >= 4 and longer.startswith(shorter)


def normalize_source_date(value: object) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        try:
            parsed = date(*(int(part) for part in match.groups()))
        except ValueError:
            return None
        return parsed.isoformat()
    return None


def classify_correction_kind(report_name: object) -> CorrectionKind:
    text = normalize_text(report_name)
    if text is None:
        return CorrectionKind.UNKNOWN
    match = _CORRECTION_PREFIX.match(text)
    marker = normalize_text(match.group(1)) if match else None
    if marker:
        compact = marker.replace(" ", "")
        if "첨부" in compact and "정정" in compact:
            return CorrectionKind.ATTACHMENT
        if "명령" in compact and "정정" in compact:
            return CorrectionKind.ORDER
        if "기재" in compact and "정정" in compact:
            return CorrectionKind.STATEMENT
        if "철회" in compact:
            return CorrectionKind.WITHDRAWAL
        if "정정" in compact:
            return CorrectionKind.OTHER
    if "철회" in text:
        return CorrectionKind.WITHDRAWAL
    return CorrectionKind.UNKNOWN


def stable_chain_id(root_rcept_no: str) -> str:
    digest = hashlib.sha256(f"dart-correction-chain:{root_rcept_no}".encode()).hexdigest()
    return f"chain_{digest[:20]}"


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _components(parsed: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    result: list[tuple[str, Mapping[str, Any]]] = []
    main = parsed.get("main_document")
    if isinstance(main, Mapping):
        result.append(("main_document", main))
    for field in ("attachments", "alternative_representations"):
        values = parsed.get(field)
        if _is_sequence(values):
            for index, item in enumerate(values):
                if isinstance(item, Mapping):
                    result.append((f"{field}[{index}]", item))
    return result


def _source_context(component: Mapping[str, Any]) -> tuple[str | None, str | None]:
    source = component.get("source_file")
    source = source if isinstance(source, Mapping) else {}
    path = source.get("relative_path")
    role = source.get("role")
    return (str(path) if path is not None else None, str(role) if role is not None else None)


def _raw_cell(cell: Mapping[str, Any]) -> object:
    return cell.get("raw_text", cell.get("text"))


def _provenance(cell_or_block: Mapping[str, Any]) -> dict[str, Any] | None:
    value = cell_or_block.get("provenance")
    return dict(value) if isinstance(value, Mapping) else None


def _make_evidence(
    *,
    field: str,
    raw_label: object,
    raw_value: object,
    normalized_value: object,
    evidence_type: str,
    source_file: str | None,
    file_role: str | None,
    locator: Mapping[str, Any],
    provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "field": field,
        "raw_label": raw_label,
        "raw_value": raw_value,
        "normalized_value": normalized_value,
        "evidence_type": evidence_type,
        "source_file": source_file,
        "file_role": file_role,
        "locator": dict(locator),
        "provenance": provenance,
    }


def _label_kind(value: object) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    compact = _REPORT_KEY.sub("", text)
    # Order matters because these labels contain the broader report label.
    if "정정대상공시서류의최초제출일" in compact:
        return "original_submission_date"
    if "정정관련공시서류제출일" in compact:
        return "related_disclosure_date"
    if compact.endswith("정정일자") or compact == "정정일자":
        return "correction_date"
    if "정정대상공시서류" in compact:
        return "target_report_name"
    if "정정관련공시서류" in compact:
        return "target_report_name"
    if compact in {"정정사유", "3정정사유"}:
        return "correction_reason"
    if compact in {"정정사항", "3정정사항", "4정정사항"}:
        return "correction_items"
    return None


def _normalize_evidence_value(field: str, value: object) -> object:
    if field.endswith("_date"):
        return normalize_source_date(value)
    if field == "target_report_name":
        return normalize_report_key(value)
    return normalize_text(value)


def _value_after_colon(text: str) -> str | None:
    for separator in (":", "："):
        if separator in text:
            return normalize_text(text.split(separator, 1)[1])
    return None


_BLOCK_LABELS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "original_submission_date",
        re.compile(r"(?:\d+\s*[.]\s*)?정정대상\s*공시서류의\s*최초제출일\s*[:：]?\s*"),
    ),
    (
        "related_disclosure_date",
        re.compile(r"(?:\d+\s*[.]\s*)?정정관련\s*공시서류\s*제출일\s*[:：]?\s*"),
    ),
    ("correction_date", re.compile(r"정정일자\s*[:：]?\s*")),
    (
        "target_report_name",
        re.compile(r"(?:\d+\s*[.]\s*)?정정대상\s*공시서류(?!의\s*최초제출일)\s*[:：]?\s*"),
    ),
    (
        "target_report_name",
        re.compile(r"(?:\d+\s*[.]\s*)?정정관련\s*공시서류(?!\s*제출일)\s*[:：]?\s*"),
    ),
    ("correction_reason", re.compile(r"(?:3\s*[.]\s*)?정정사유\s*[:：]?\s*")),
    ("correction_items", re.compile(r"(?:3|4)\s*[.]\s*정정사항\s*[:：]?\s*")),
)


def _block_label_values(text: str) -> list[tuple[str, str, str | None]]:
    """Split a block at each correction label instead of its first colon."""

    matches: list[tuple[int, int, str, str]] = []
    for field, pattern in _BLOCK_LABELS:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), field, match.group(0)))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    filtered: list[tuple[int, int, str, str]] = []
    for item in matches:
        if filtered and item[0] < filtered[-1][1]:
            continue
        filtered.append(item)
    result: list[tuple[str, str, str | None]] = []
    for index, (_, end, field, label) in enumerate(filtered):
        next_start = filtered[index + 1][0] if index + 1 < len(filtered) else len(text)
        value = normalize_text(text[end:next_start])
        result.append((field, label, value))
    return result


def extract_correction_evidence(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Extract only source-explicit correction fields with exact locators."""

    evidence: list[dict[str, Any]] = []
    pattern_counts: defaultdict[str, int] = defaultdict(int)
    before_present = False
    after_present = False

    for component_name, component in _components(parsed):
        content = component.get("content")
        if not isinstance(content, Mapping):
            continue
        source_file, file_role = _source_context(component)

        tables = content.get("tables")
        if _is_sequence(tables):
            for table_index, table in enumerate(tables):
                if not isinstance(table, Mapping):
                    continue
                rows = table.get("raw_rows")
                if not _is_sequence(rows):
                    continue
                for row_index, row in enumerate(rows):
                    if not _is_sequence(row):
                        continue
                    cells = [cell for cell in row if isinstance(cell, Mapping)]
                    texts = [normalize_text(cell.get("text", _raw_cell(cell))) for cell in cells]
                    joined = " ".join(item for item in texts if item)
                    compact_joined = _REPORT_KEY.sub("", joined)
                    before_present = before_present or "정정전" in compact_joined
                    after_present = after_present or "정정후" in compact_joined
                    for cell_index, cell in enumerate(cells):
                        kind = _label_kind(cell.get("text", _raw_cell(cell)))
                        if kind is None:
                            continue
                        if kind == "correction_items":
                            evidence.append(
                                _make_evidence(
                                    field=kind,
                                    raw_label=_raw_cell(cell),
                                    raw_value=None,
                                    normalized_value=None,
                                    evidence_type="table_section_marker",
                                    source_file=source_file,
                                    file_role=file_role,
                                    locator={"component": component_name, "table_index": table_index, "row_index": row_index, "label_cell_index": cell_index},
                                    provenance=_provenance(cell),
                                )
                            )
                            pattern_counts[f"table:{kind}"] += 1
                            continue
                        # A reason column header is not a reason/value pair.
                        if kind == "correction_reason" and cell_index != 0:
                            continue
                        value_cell = next(
                            (candidate for candidate in cells[cell_index + 1 :] if normalize_text(candidate.get("text", _raw_cell(candidate)))),
                            None,
                        )
                        if value_cell is None:
                            continue
                        raw_value = _raw_cell(value_cell)
                        normalized = _normalize_evidence_value(kind, raw_value)
                        evidence.append(
                            _make_evidence(
                                field=kind,
                                raw_label=_raw_cell(cell),
                                raw_value=raw_value,
                                normalized_value=normalized,
                                evidence_type="table_cell_pair",
                                source_file=source_file,
                                file_role=file_role,
                                locator={
                                    "component": component_name,
                                    "table_index": table_index,
                                    "row_index": row_index,
                                    "label_cell_index": cell_index,
                                    "value_cell_index": cells.index(value_cell),
                                },
                                provenance=_provenance(value_cell),
                            )
                        )
                        pattern_counts[f"table:{kind}"] += 1

                # Periodic/major correction tables express reasons as a column.
                for header_row_index, header_row in enumerate(rows):
                    if not _is_sequence(header_row):
                        continue
                    header_cells = [cell for cell in header_row if isinstance(cell, Mapping)]
                    reason_columns = [
                        index for index, cell in enumerate(header_cells)
                        if _REPORT_KEY.sub("", normalize_text(cell.get("text", _raw_cell(cell))) or "") == "정정사유"
                    ]
                    for reason_column in reason_columns:
                        for row_index in range(header_row_index + 1, len(rows)):
                            row = rows[row_index]
                            if not _is_sequence(row):
                                continue
                            cells = [cell for cell in row if isinstance(cell, Mapping)]
                            if reason_column >= len(cells):
                                continue
                            value_cell = cells[reason_column]
                            raw_value = _raw_cell(value_cell)
                            normalized = normalize_text(value_cell.get("text", raw_value))
                            compact = _REPORT_KEY.sub("", normalized or "")
                            if not normalized or compact in {"정정사유", "정정전", "정정후"}:
                                continue
                            evidence.append(
                                _make_evidence(
                                    field="correction_reason",
                                    raw_label=_raw_cell(header_cells[reason_column]),
                                    raw_value=raw_value,
                                    normalized_value=normalized,
                                    evidence_type="table_column_value",
                                    source_file=source_file,
                                    file_role=file_role,
                                    locator={"component": component_name, "table_index": table_index, "header_row_index": header_row_index, "row_index": row_index, "value_cell_index": reason_column},
                                    provenance=_provenance(value_cell),
                                )
                            )
                            pattern_counts["table:correction_reason_column"] += 1

        blocks = content.get("block_sequence")
        if _is_sequence(blocks):
            pending: tuple[str, str, Mapping[str, Any], int] | None = None
            for block_index, block in enumerate(blocks):
                if not isinstance(block, Mapping):
                    continue
                raw_text = block.get("raw_text", block.get("text"))
                text = normalize_text(block.get("text", raw_text))
                if text is None:
                    continue
                compact = _REPORT_KEY.sub("", text)
                before_present = before_present or "정정전" in compact
                after_present = after_present or "정정후" in compact
                label_values = _block_label_values(text)
                if pending is not None and not label_values:
                    kind, label, label_block, label_index = pending
                    evidence.append(
                        _make_evidence(
                            field=kind,
                            raw_label=label,
                            raw_value=raw_text,
                            normalized_value=_normalize_evidence_value(kind, raw_text),
                            evidence_type="adjacent_block_label_value",
                            source_file=source_file,
                            file_role=file_role,
                            locator={"component": component_name, "label_block_index": label_index, "value_block_index": block_index},
                            provenance=_provenance(block),
                        )
                    )
                    pattern_counts[f"block:{kind}"] += 1
                    pending = None
                for kind, label, raw_value in label_values:
                    if raw_value is None and kind != "correction_items":
                        pending = (kind, label, block, block_index)
                        continue
                    evidence.append(
                        _make_evidence(
                            field=kind,
                            raw_label=label,
                            raw_value=raw_value,
                            normalized_value=_normalize_evidence_value(kind, raw_value),
                            evidence_type="block_section_marker" if kind == "correction_items" and raw_value is None else "block_label_value",
                            source_file=source_file,
                            file_role=file_role,
                            locator={"component": component_name, "block_index": block_index},
                            provenance=_provenance(block),
                        )
                    )
                    pattern_counts[f"block:{kind}"] += 1

    # Preserve all occurrences but expose deterministic primary values.
    by_field: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    unique: set[tuple[object, ...]] = set()
    deduplicated: list[dict[str, Any]] = []
    for item in evidence:
        key = (
            item["field"], item["raw_value"], item["source_file"],
            json.dumps(item["locator"], sort_keys=True, ensure_ascii=False),
        )
        if key in unique:
            continue
        unique.add(key)
        deduplicated.append(item)
        by_field[item["field"]].append(item)

    def primary(field: str) -> object:
        values = [item["normalized_value"] for item in by_field.get(field, []) if item["normalized_value"] is not None]
        return values[0] if values else None

    target_date_field = (
        "original_submission_date"
        if by_field.get("original_submission_date")
        else "related_disclosure_date"
    )
    return {
        "target_report_name_raw": (by_field.get("target_report_name") or [{}])[0].get("raw_value"),
        "target_report_key": primary("target_report_name"),
        "target_submission_date_raw": (by_field.get(target_date_field) or [{}])[0].get("raw_value"),
        "target_submission_date": primary(target_date_field),
        "target_date_semantics": target_date_field,
        "correction_date_raw": (by_field.get("correction_date") or [{}])[0].get("raw_value"),
        "correction_date": primary("correction_date"),
        "correction_reason_raw": (by_field.get("correction_reason") or [{}])[0].get("raw_value"),
        "correction_reason": primary("correction_reason"),
        "before_present": before_present,
        "after_present": after_present,
        "evidence": deduplicated,
        "evidence_count": len(deduplicated),
        "pattern_counts": dict(sorted(pattern_counts.items())),
    }


@dataclass(slots=True)
class MetadataIndex:
    by_doc_id: dict[str, dict[str, Any]]
    by_rcept_no: dict[str, dict[str, Any]]
    by_company_date: dict[tuple[str, str], list[dict[str, Any]]]

    @classmethod
    def build(cls, rows: Sequence[Mapping[str, Any]]) -> "MetadataIndex":
        by_doc_id: dict[str, dict[str, Any]] = {}
        by_rcept_no: dict[str, dict[str, Any]] = {}
        by_company_date: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else row
            item = dict(metadata)
            doc_id = str(item.get("doc_id") or row.get("doc_id") or "")
            rcept_no = str(item.get("rcept_no") or row.get("rcept_no") or "")
            if not doc_id or not rcept_no:
                continue
            if doc_id in by_doc_id or rcept_no in by_rcept_no:
                raise ValueError(f"Duplicate canonical identifier: {doc_id}/{rcept_no}")
            item["doc_id"] = doc_id
            item["rcept_no"] = rcept_no
            by_doc_id[doc_id] = item
            by_rcept_no[rcept_no] = item
            company = str(item.get("corp_code") or "")
            rcept_date = str(item.get("rcept_date") or "")
            if company and rcept_date:
                by_company_date[(company, rcept_date)].append(item)
        for candidates in by_company_date.values():
            candidates.sort(key=lambda row: (str(row.get("rcept_date") or ""), str(row["rcept_no"])))
        return cls(by_doc_id, by_rcept_no, dict(by_company_date))


def _candidate_descriptor(candidate: Mapping[str, Any], correction: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    same_company = candidate.get("corp_code") == correction.get("corp_code")
    same_group = candidate.get("doc_group") == correction.get("doc_group")
    subtype = correction.get("doc_subtype")
    same_subtype = subtype is None or candidate.get("doc_subtype") == subtype
    report_match = report_keys_compatible(candidate.get("report_nm"), evidence.get("target_report_name_raw") or correction.get("report_nm"))
    return {
        "doc_id": candidate.get("doc_id"),
        "rcept_no": candidate.get("rcept_no"),
        "rcept_date": candidate.get("rcept_date"),
        "report_nm": candidate.get("report_nm"),
        "doc_group": candidate.get("doc_group"),
        "doc_subtype": candidate.get("doc_subtype"),
        "is_correction": candidate.get("is_correction"),
        "checks": {
            "same_company": same_company,
            "same_doc_group": same_group,
            "compatible_doc_subtype": same_subtype,
            "compatible_report_name": report_match,
        },
    }


def resolve_one(
    correction_row: Mapping[str, Any],
    extracted: Mapping[str, Any],
    index: MetadataIndex,
) -> dict[str, Any]:
    correction = correction_row.get("metadata") if isinstance(correction_row.get("metadata"), Mapping) else correction_row
    doc_id = str(correction.get("doc_id") or correction_row.get("doc_id") or "")
    rcept_no = str(correction.get("rcept_no") or correction_row.get("rcept_no") or "")
    target_date = extracted.get("target_submission_date")
    target_report = extracted.get("target_report_name_raw")
    stages: list[dict[str, Any]] = []

    explicit_values: list[str] = []
    for item in extracted.get("evidence", []):
        if not isinstance(item, Mapping):
            continue
        label = normalize_text(item.get("raw_label")) or ""
        if "접수번호" not in label:
            continue
        explicit_values.extend(_RCEPT_NO.findall(str(item.get("raw_value") or "")))
    explicit_values = sorted(set(explicit_values))
    explicit_candidates = [index.by_rcept_no[value] for value in explicit_values if value in index.by_rcept_no and value != rcept_no]
    stages.append({"stage": "explicit_receipt_number", "input": explicit_values, "candidate_count": len(explicit_candidates)})
    if len(explicit_candidates) == 1:
        candidates = explicit_candidates
        status = ResolutionStatus.LINKED_EXACT
        method = "explicit_receipt_number"
    else:
        candidates = []
        status = ResolutionStatus.UNRESOLVED
        method = None

    date_candidates: list[dict[str, Any]] = []
    if not candidates and target_date:
        date_candidates = [
            item for item in index.by_company_date.get((str(correction.get("corp_code") or ""), str(target_date)), [])
            if item.get("rcept_no") != rcept_no
        ]
        report_candidates = [
            item for item in date_candidates
            if target_report is not None
            and report_keys_compatible(item.get("report_nm"), target_report)
        ]
        report_stage = {"stage": "company_date_report", "input": {"target_date": target_date, "target_report": target_report}, "candidate_count": len(report_candidates)}
        if target_report is None:
            report_stage["skipped_reason"] = "TARGET_REPORT_MISSING"
        stages.append(report_stage)
        if len(report_candidates) == 1:
            candidates = report_candidates
            status = ResolutionStatus.LINKED_EXACT
            method = "company_date_report"

    if not candidates and target_date:
        typed = [
            item for item in date_candidates
            if item.get("doc_group") == correction.get("doc_group")
            and (correction.get("doc_subtype") is None or item.get("doc_subtype") == correction.get("doc_subtype"))
        ]
        # "최초제출일" explicitly denotes the non-correction root.
        if extracted.get("target_date_semantics") == "original_submission_date":
            non_corrections = [item for item in typed if item.get("is_correction") is False]
            if non_corrections:
                typed = non_corrections
        stages.append({"stage": "company_date_classification", "input": {"doc_group": correction.get("doc_group"), "doc_subtype": correction.get("doc_subtype")}, "candidate_count": len(typed)})
        if len(typed) == 1:
            candidates = typed
            status = ResolutionStatus.LINKED_INFERRED
            method = "company_date_classification"
        elif len(typed) > 1:
            candidates = typed
            status = ResolutionStatus.AMBIGUOUS
            method = "multiple_company_date_classification_candidates"
        elif len(report_candidates) > 1:
            candidates = report_candidates
            status = ResolutionStatus.AMBIGUOUS
            method = "multiple_company_date_report_candidates"

    if not target_date:
        stages.append({"stage": "company_date_report", "candidate_count": 0, "skipped_reason": "TARGET_DATE_MISSING"})

    target = candidates[0] if status in {ResolutionStatus.LINKED_EXACT, ResolutionStatus.LINKED_INFERRED} and len(candidates) == 1 else None
    candidate_details = [_candidate_descriptor(item, correction, extracted) for item in candidates]
    return {
        "schema_version": PHASE4_SCHEMA_VERSION,
        "doc_id": doc_id,
        "rcept_no": rcept_no,
        "is_correction": True,
        "correction_kind": str(classify_correction_kind(correction.get("report_nm"))),
        "resolution": {
            "status": str(status),
            "method": method,
            "policy_version": RESOLVER_POLICY_VERSION,
            "confidence": 1.0 if status == ResolutionStatus.LINKED_EXACT else 0.8 if status == ResolutionStatus.LINKED_INFERRED else 0.0,
            "target_rcept_no": target.get("rcept_no") if target else None,
            "target_doc_id": target.get("doc_id") if target else None,
            "candidate_count": len(candidate_details),
            "candidates": candidate_details,
            "stages": stages,
            "external_lookup": {"enabled": False, "status": "not_requested"},
        },
        "lineage": {
            "parent_rcept_no": None,
            "root_rcept_no": None,
            "latest_rcept_no": None,
            "chain_id": None,
            "sequence": None,
        },
        "correction_evidence": dict(extracted),
        "metadata_snapshot": {
            key: correction.get(key)
            for key in ("corp_code", "corp_name", "listed_name", "stock_code", "doc_group", "doc_subtype", "report_nm", "rcept_dt", "rcept_date", "is_correction")
        },
        "content_reference": {"dataset": "parsed_disclosures.jsonl", "join_key": "doc_id"},
    }


def apply_chain_backtrack(records: list[dict[str, Any]], index: MetadataIndex) -> None:
    """Audit uncertain candidates through already explicit target chains.

    This stage never chooses among multiple target receipts.  It may identify a
    unanimous non-correction root while leaving target status ambiguous.
    """

    by_receipt = {record["rcept_no"]: record for record in records}

    def root_for_receipt(receipt: str) -> str | None:
        seen: set[str] = set()
        cursor = receipt
        while cursor in by_receipt:
            if cursor in seen:
                return None
            seen.add(cursor)
            target = by_receipt[cursor]["resolution"].get("target_rcept_no")
            if not target:
                return None
            cursor = str(target)
        metadata = index.by_rcept_no.get(cursor)
        if metadata is None or metadata.get("is_correction") is True:
            return None
        return cursor

    for record in records:
        candidate_roots = sorted(
            {
                root
                for candidate in record["resolution"].get("candidates", [])
                if (root := root_for_receipt(str(candidate.get("rcept_no") or ""))) is not None
            }
        )
        outcome = "no_unique_root"
        if len(candidate_roots) == 1:
            outcome = "unanimous_root_only"
            if record["resolution"]["status"] in {
                ResolutionStatus.AMBIGUOUS,
                ResolutionStatus.UNRESOLVED,
            }:
                record["lineage"]["root_rcept_no"] = candidate_roots[0]
        record["resolution"]["stages"].append(
            {
                "stage": "chain_backtrack",
                "candidate_root_count": len(candidate_roots),
                "candidate_roots": candidate_roots,
                "outcome": outcome,
                "target_selected": False,
            }
        )


def guard_resolution_cycles(records: list[dict[str, Any]]) -> list[list[str]]:
    """Demote every directed target cycle to ambiguous; never break it by order."""

    by_receipt = {str(record["rcept_no"]): record for record in records}
    cycles: list[list[str]] = []
    processed: set[str] = set()
    for start in sorted(by_receipt):
        if start in processed:
            continue
        path: list[str] = []
        position: dict[str, int] = {}
        cursor = start
        while cursor in by_receipt and cursor not in processed:
            if cursor in position:
                cycle = path[position[cursor] :]
                cycles.append(cycle)
                break
            position[cursor] = len(path)
            path.append(cursor)
            target = by_receipt[cursor]["resolution"].get("target_rcept_no")
            if not target:
                break
            cursor = str(target)
        processed.update(path)

    for cycle in cycles:
        cycle_set = set(cycle)
        for receipt in cycle:
            record = by_receipt[receipt]
            selected_target = record["resolution"].get("target_rcept_no")
            record["resolution"].update(
                {
                    "status": str(ResolutionStatus.AMBIGUOUS),
                    "method": "cycle_guard_ambiguous",
                    "confidence": 0.0,
                    "target_rcept_no": None,
                    "target_doc_id": None,
                }
            )
            record["resolution"]["stages"].append(
                {
                    "stage": "cycle_guard",
                    "outcome": "demoted_to_ambiguous",
                    "cycle_rcept_nos": sorted(cycle_set),
                    "previous_selected_target": selected_target,
                    "target_selected": False,
                }
            )
    return cycles


def build_chains(records: list[dict[str, Any]], index: MetadataIndex) -> list[dict[str, Any]]:
    """Follow target edges, then assign deterministic parent/root/latest semantics."""

    by_receipt = {record["rcept_no"]: record for record in records}

    def root_for(record: Mapping[str, Any]) -> str | None:
        target = record["resolution"].get("target_rcept_no")
        if not target:
            return None
        seen = {str(record["rcept_no"])}
        cursor = str(target)
        while cursor in by_receipt:
            if cursor in seen:
                return None
            seen.add(cursor)
            next_target = by_receipt[cursor]["resolution"].get("target_rcept_no")
            if not next_target:
                break
            cursor = str(next_target)
        target_metadata = index.by_rcept_no.get(cursor)
        if target_metadata is None or target_metadata.get("is_correction") is True:
            return None
        return cursor

    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        root = root_for(record)
        if root is not None:
            grouped[root].append(record)

    chains: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for root, members in sorted(grouped.items()):
        root_metadata = index.by_rcept_no[root]
        members.sort(key=lambda row: (str(row["metadata_snapshot"].get("rcept_date") or ""), str(row["rcept_no"])))
        all_receipts = [root, *(str(row["rcept_no"]) for row in members)]
        latest = all_receipts[-1]
        chain_id = stable_chain_id(root)
        previous = root
        for sequence, member in enumerate(members, start=1):
            member["lineage"].update({
                "parent_rcept_no": previous,
                "root_rcept_no": root,
                "latest_rcept_no": latest,
                "chain_id": chain_id,
                "sequence": sequence,
            })
            assigned.add(str(member["rcept_no"]))
            previous = str(member["rcept_no"])
        chains.append({
            "schema_version": PHASE4_SCHEMA_VERSION,
            "chain_id": chain_id,
            "root_rcept_no": root,
            "root_doc_id": root_metadata.get("doc_id"),
            "latest_rcept_no": latest,
            "corp_code": root_metadata.get("corp_code"),
            "doc_group": root_metadata.get("doc_group"),
            "member_count": len(all_receipts),
            "correction_count": len(members),
            "is_complete": True,
            "anchor_rcept_no": root,
            "members": [
                {"sequence": 0, "rcept_no": root, "doc_id": root_metadata.get("doc_id"), "is_correction": False},
                *[
                    {"sequence": index_value, "rcept_no": member["rcept_no"], "doc_id": member["doc_id"], "is_correction": True}
                    for index_value, member in enumerate(members, start=1)
                ],
            ],
        })

    # Preserve linked components whose ultimate original is absent/uncertain.
    adjacency: defaultdict[str, set[str]] = defaultdict(set)
    for record in records:
        receipt = str(record["rcept_no"])
        target = record["resolution"].get("target_rcept_no")
        if receipt in assigned or not target or str(target) not in by_receipt:
            continue
        target_receipt = str(target)
        if target_receipt in assigned:
            continue
        adjacency[receipt].add(target_receipt)
        adjacency[target_receipt].add(receipt)
    visited: set[str] = set()
    for start in sorted(adjacency):
        if start in visited:
            continue
        stack = [start]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            stack.extend(adjacency[current] - visited)
        members = [by_receipt[receipt] for receipt in component]
        members.sort(key=lambda row: (str(row["metadata_snapshot"].get("rcept_date") or ""), str(row["rcept_no"])))
        anchor = str(members[0]["rcept_no"])
        latest = str(members[-1]["rcept_no"])
        chain_id = stable_chain_id(f"partial:{anchor}")
        for member in members:
            target = member["resolution"].get("target_rcept_no")
            member["lineage"].update({
                "parent_rcept_no": str(target) if target and str(target) in component else None,
                "root_rcept_no": None,
                "latest_rcept_no": latest,
                "chain_id": chain_id,
                "sequence": None,
            })
            assigned.add(str(member["rcept_no"]))
        chains.append({
            "schema_version": PHASE4_SCHEMA_VERSION,
            "chain_id": chain_id,
            "root_rcept_no": None,
            "root_doc_id": None,
            "anchor_rcept_no": anchor,
            "latest_rcept_no": latest,
            "corp_code": members[0]["metadata_snapshot"].get("corp_code"),
            "doc_group": members[0]["metadata_snapshot"].get("doc_group"),
            "member_count": len(members),
            "correction_count": len(members),
            "is_complete": False,
            "members": [
                {"sequence": None, "rcept_no": member["rcept_no"], "doc_id": member["doc_id"], "is_correction": True}
                for member in members
            ],
        })
    chains.sort(key=lambda row: str(row["chain_id"]))
    return chains


def validate_lineage(records: Sequence[Mapping[str, Any]], chains: Sequence[Mapping[str, Any]], index: MetadataIndex) -> dict[str, Any]:
    errors: list[str] = []
    correction_ids: set[str] = set()
    linked_count = 0
    linked_without_chain_count = 0
    future_target_count = 0
    cross_company_link_count = 0
    evidence_less_link_count = 0
    by_receipt = {str(record["rcept_no"]): record for record in records}
    for record in records:
        doc_id = str(record.get("doc_id") or "")
        if doc_id in correction_ids:
            errors.append(f"duplicate correction doc_id: {doc_id}")
        correction_ids.add(doc_id)
        if index.by_doc_id.get(doc_id, {}).get("is_correction") is not True:
            errors.append(f"non-correction in lineage output: {doc_id}")
        status = record.get("resolution", {}).get("status")
        target = record.get("resolution", {}).get("target_rcept_no")
        if status in {ResolutionStatus.LINKED_EXACT, ResolutionStatus.LINKED_INFERRED}:
            linked_count += 1
            if not target or target not in index.by_rcept_no:
                errors.append(f"linked target absent from corpus: {doc_id}/{target}")
            if not record.get("lineage", {}).get("chain_id"):
                linked_without_chain_count += 1
                errors.append(f"linked row has no chain: {doc_id}")
            target_metadata = index.by_rcept_no.get(str(target or ""), {})
            correction_metadata = index.by_doc_id.get(doc_id, {})
            if target_metadata.get("corp_code") != correction_metadata.get("corp_code"):
                cross_company_link_count += 1
                errors.append(f"cross-company target: {doc_id}/{target}")
            correction_evidence = record.get("correction_evidence", {})
            if not correction_evidence.get("evidence") or not correction_evidence.get("target_submission_date"):
                evidence_less_link_count += 1
                errors.append(f"linked without target evidence: {doc_id}/{target}")
            if str(target_metadata.get("rcept_date") or "") > str(index.by_doc_id.get(doc_id, {}).get("rcept_date") or ""):
                future_target_count += 1
                errors.append(f"target date is after correction date: {doc_id}/{target}")
        lineage = record.get("lineage", {})
        if lineage.get("root_rcept_no"):
            root = index.by_rcept_no.get(str(lineage["root_rcept_no"]))
            if root is None or root.get("is_correction") is True:
                errors.append(f"root is absent or correction: {doc_id}")

    seen_chain_ids: set[str] = set()
    for chain in chains:
        chain_id = str(chain.get("chain_id") or "")
        if chain_id in seen_chain_ids:
            errors.append(f"duplicate chain_id: {chain_id}")
        seen_chain_ids.add(chain_id)
        members = chain.get("members") or []
        sequences = [item.get("sequence") for item in members]
        if chain.get("is_complete") is True:
            if sequences != list(range(len(members))):
                errors.append(f"non-contiguous chain sequence: {chain_id}")
        elif any(sequence is not None for sequence in sequences):
            errors.append(f"partial chain has asserted sequence: {chain_id}")
        if members and chain.get("latest_rcept_no") != members[-1].get("rcept_no"):
            errors.append(f"latest mismatch: {chain_id}")

    # Directed correction target graph must be acyclic.
    cycle_count = 0
    for receipt in by_receipt:
        seen: set[str] = set()
        cursor = receipt
        while cursor in by_receipt:
            if cursor in seen:
                cycle_count += 1
                errors.append(f"correction cycle detected at: {cursor}")
                break
            seen.add(cursor)
            target = by_receipt[cursor]["resolution"].get("target_rcept_no")
            if not target:
                break
            cursor = str(target)
    return {
        "passed": not errors,
        "error_count": len(errors),
        "errors": errors[:100],
        "correction_count": len(records),
        "linked_count": linked_count,
        "chain_count": len(chains),
        "unique_correction_doc_id_count": len(correction_ids),
        "linked_without_chain_count": linked_without_chain_count,
        "future_target_count": future_target_count,
        "cross_company_link_count": cross_company_link_count,
        "evidence_less_link_count": evidence_less_link_count,
        "cycle_count": cycle_count,
    }


__all__ = [
    "CorrectionKind", "MetadataIndex", "PHASE4_SCHEMA_VERSION",
    "RESOLVER_POLICY_VERSION", "ResolutionStatus", "build_chains",
    "apply_chain_backtrack", "classify_correction_kind", "extract_correction_evidence",
    "guard_resolution_cycles",
    "normalize_report_key", "normalize_source_date", "resolve_one",
    "stable_chain_id", "validate_lineage",
]
