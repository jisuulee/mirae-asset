"""Conservative Phase 3 canonical metadata validation and comparison helpers."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from disclosure_preprocess.utils import normalize_nfc


PHASE3_SCHEMA_VERSION = "3.0.0"
CONTENT_SCHEMA_VERSION = "2.0.0"
COMPARISON_POLICY_VERSION = "phase3-metadata-comparison-v1"
SAMPLE_POLICY_VERSION = "phase3-handoff-sample-v1"

CANONICAL_METADATA_FIELDS: tuple[str, ...] = (
    "doc_id",
    "corp_code",
    "corp_name",
    "listed_name",
    "stock_code",
    "industry",
    "sector",
    "doc_group",
    "doc_subtype",
    "report_nm",
    "is_correction",
    "rcept_no",
    "rcept_dt",
    "flr_nm",
    "base_year",
    "base_month",
)

IDENTIFIER_FIELDS = frozenset({"doc_id", "corp_code", "stock_code", "rcept_no"})
TEXT_FIELDS = frozenset(
    {
        "corp_name",
        "listed_name",
        "industry",
        "sector",
        "doc_group",
        "doc_subtype",
        "report_nm",
        "rcept_dt",
        "flr_nm",
    }
)
NULLABLE_FIELDS = frozenset({"doc_subtype", "base_year", "base_month"})
REQUIRED_FIELDS = frozenset(CANONICAL_METADATA_FIELDS) - NULLABLE_FIELDS
SAFE_FILTER_FIELDS: tuple[str, ...] = (
    "doc_id",
    "rcept_no",
    "corp_code",
    "corp_name",
    "listed_name",
    "stock_code",
    "doc_group",
    "doc_subtype",
    "rcept_date",
    "is_correction",
)
EXPECTED_DOC_GROUPS = frozenset({"periodic", "major", "exchange", "holding"})

SOURCE_TO_CANONICAL_FIELD = {
    "rcept_no": "rcept_no",
    "company_name": "corp_name",
    "document_name": "report_nm",
}


class MetadataValidationStatus(StrEnum):
    VALID = "valid"
    VALID_WITH_WARNING = "valid_with_warning"
    INVALID = "invalid"


class MismatchReason(StrEnum):
    DISPLAY_NAME_DIFFERENCE = "DISPLAY_NAME_DIFFERENCE"
    UNICODE_NORMALIZATION_DIFFERENCE = "UNICODE_NORMALIZATION_DIFFERENCE"
    WHITESPACE_DIFFERENCE = "WHITESPACE_DIFFERENCE"
    DATE_FORMAT_DIFFERENCE = "DATE_FORMAT_DIFFERENCE"
    REPORT_NAME_FORMAT_DIFFERENCE = "REPORT_NAME_FORMAT_DIFFERENCE"
    SOURCE_VALUE_MISSING = "SOURCE_VALUE_MISSING"
    MANIFEST_VALUE_MISSING = "MANIFEST_VALUE_MISSING"
    SOURCE_VALUE_DIFFERENT = "SOURCE_VALUE_DIFFERENT"
    PARSER_EXTRACTION_AMBIGUOUS = "PARSER_EXTRACTION_AMBIGUOUS"
    UNKNOWN_MISMATCH = "UNKNOWN_MISMATCH"


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def normalize_comparison_text(value: object) -> str | None:
    """Return an NFC and whitespace-normalized comparison view.

    The returned value never replaces either canonical or source raw values.
    """

    if value is None:
        return None
    text = normalize_nfc(str(value)).replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(text.split())


def _compact_text(value: object) -> str:
    normalized = normalize_comparison_text(value)
    return "" if normalized is None else "".join(normalized.split()).casefold()


def _company_display_key(value: object) -> str:
    key = _compact_text(value)
    designators = ("주식회사", "(주)", "㈜")
    changed = True
    while changed and key:
        changed = False
        for designator in designators:
            if key.startswith(designator):
                key = key[len(designator) :]
                changed = True
            if key.endswith(designator):
                key = key[: -len(designator)]
                changed = True
    return key


def _parse_flexible_date(value: object) -> str | None:
    if value is None:
        return None
    text = normalize_nfc(str(value)).strip()
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def format_rcept_date(value: object) -> str | None:
    """Strictly format manifest ``rcept_dt`` without consulting rcept_no."""

    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def metadata_warning(
    code: str,
    message: str,
    *,
    severity: str = "warning",
    field: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "severity": severity,
        "field": field,
        "details": dict(details or {}),
    }


def build_canonical_metadata(
    manifest_record: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Copy manifest authority values and add only strict rcept_date formatting."""

    metadata = {
        "authority": "manifest",
        **{
            field: manifest_record.get(field)
            for field in CANONICAL_METADATA_FIELDS
        },
    }
    warnings = validate_canonical_metadata(metadata)
    metadata["rcept_date"] = format_rcept_date(metadata.get("rcept_dt"))
    return metadata, warnings


def validate_canonical_metadata(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []

    for field in CANONICAL_METADATA_FIELDS:
        value = metadata.get(field)
        if value is None:
            if field in REQUIRED_FIELDS:
                warnings.append(
                    metadata_warning(
                        "CANONICAL_VALUE_MISSING",
                        "Required manifest metadata is null",
                        severity="error",
                        field=field,
                    )
                )
            continue
        if isinstance(value, str) and not value.strip():
            warnings.append(
                metadata_warning(
                    "CANONICAL_VALUE_EMPTY",
                    "Manifest metadata is an empty or whitespace-only string",
                    severity="error" if field in REQUIRED_FIELDS else "warning",
                    field=field,
                )
            )

    for field in IDENTIFIER_FIELDS:
        value = metadata.get(field)
        if value is not None and not isinstance(value, str):
            warnings.append(
                metadata_warning(
                    "IDENTIFIER_TYPE_INVALID",
                    "Identifier metadata must remain a JSON string",
                    severity="error",
                    field=field,
                    details={"observed_type": type(value).__name__},
                )
            )

    for field in TEXT_FIELDS:
        value = metadata.get(field)
        if value is not None and not isinstance(value, str):
            warnings.append(
                metadata_warning(
                    "CANONICAL_TYPE_INVALID",
                    "Canonical text metadata must be a JSON string or an allowed null",
                    severity="error",
                    field=field,
                    details={"observed_type": type(value).__name__},
                )
            )

    patterns = {
        "doc_id": r"[a-z][a-z0-9]*_[0-9]{14}",
        "corp_code": r"[0-9]{8}",
        "stock_code": r"[0-9]{6}",
        "rcept_no": r"[0-9]{14}",
        "rcept_dt": r"[0-9]{8}",
    }
    for field, pattern in patterns.items():
        value = metadata.get(field)
        if isinstance(value, str) and value and not re.fullmatch(pattern, value):
            warnings.append(
                metadata_warning(
                    "CANONICAL_FORMAT_INVALID",
                    "Manifest metadata does not match the Phase 3 format contract",
                    severity="error",
                    field=field,
                    details={"pattern": pattern},
                )
            )

    rcept_dt = metadata.get("rcept_dt")
    if rcept_dt is not None and format_rcept_date(rcept_dt) is None:
        warnings.append(
            metadata_warning(
                "RCEPT_DATE_INVALID",
                "rcept_dt is not a valid YYYYMMDD calendar date; rcept_date is null",
                severity="error",
                field="rcept_dt",
            )
        )

    rcept_no = metadata.get("rcept_no")
    if (
        isinstance(rcept_no, str)
        and len(rcept_no) >= 8
        and isinstance(rcept_dt, str)
        and len(rcept_dt) == 8
        and rcept_no[:8] != rcept_dt
    ):
        warnings.append(
            metadata_warning(
                "RCEPT_DATE_PREFIX_DIFFERENCE",
                "rcept_no prefix differs from authoritative rcept_dt; rcept_dt was retained",
                severity="info",
                field="rcept_dt",
                details={"rcept_no_prefix": rcept_no[:8], "rcept_dt": rcept_dt},
            )
        )

    doc_group = metadata.get("doc_group")
    if isinstance(doc_group, str) and doc_group not in EXPECTED_DOC_GROUPS:
        warnings.append(
            metadata_warning(
                "DOC_GROUP_UNRECOGNIZED",
                "doc_group is outside the observed Phase 3 corpus vocabulary",
                severity="error",
                field="doc_group",
                details={"allowed": sorted(EXPECTED_DOC_GROUPS)},
            )
        )

    doc_id = metadata.get("doc_id")
    if isinstance(doc_id, str) and isinstance(doc_group, str) and isinstance(rcept_no, str):
        expected_doc_id = f"{doc_group}_{rcept_no}"
        if doc_id != expected_doc_id:
            warnings.append(
                metadata_warning(
                    "DOC_ID_COMPONENT_MISMATCH",
                    "doc_id does not equal doc_group + '_' + rcept_no",
                    severity="error",
                    field="doc_id",
                    details={"expected_doc_id": expected_doc_id},
                )
            )

    if not isinstance(metadata.get("is_correction"), bool):
        warnings.append(
            metadata_warning(
                "IS_CORRECTION_TYPE_INVALID",
                "is_correction must be a JSON boolean",
                severity="error",
                field="is_correction",
            )
        )

    for field in ("base_year", "base_month"):
        value = metadata.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            warnings.append(
                metadata_warning(
                    "CANONICAL_TYPE_INVALID",
                    "Periodic base metadata must be an integer or null",
                    severity="error",
                    field=field,
                    details={"observed_type": type(value).__name__},
                )
            )
    base_month = metadata.get("base_month")
    if isinstance(base_month, int) and not isinstance(base_month, bool) and not 1 <= base_month <= 12:
        warnings.append(
            metadata_warning(
                "BASE_MONTH_INVALID",
                "base_month must be between 1 and 12 when present",
                severity="error",
                field="base_month",
            )
        )

    for field in ("corp_name", "listed_name", "industry", "sector", "report_nm", "flr_nm"):
        value = metadata.get(field)
        if isinstance(value, str) and value != unicodedata.normalize("NFC", value):
            warnings.append(
                metadata_warning(
                    "UNICODE_NFC_NORMALIZATION_AVAILABLE",
                    "Canonical raw value is not NFC; raw was retained and NFC is comparison-only",
                    severity="info",
                    field=field,
                )
            )

    return warnings


def validation_status(
    canonical_warnings: Sequence[Mapping[str, Any]], mismatch_count: int
) -> MetadataValidationStatus:
    if any(str(item.get("severity")) == "error" for item in canonical_warnings):
        return MetadataValidationStatus.INVALID
    if canonical_warnings or mismatch_count:
        return MetadataValidationStatus.VALID_WITH_WARNING
    return MetadataValidationStatus.VALID


def classify_mismatch_reason(
    *,
    field: str,
    canonical_value: object,
    source_value: object,
    listed_name_value: object = None,
    company_identity_ambiguous: bool = False,
) -> tuple[MismatchReason, str, bool]:
    """Classify one observed mismatch without changing either raw value."""

    if source_value is None or (isinstance(source_value, str) and not source_value.strip()):
        return MismatchReason.SOURCE_VALUE_MISSING, "warning", False
    if canonical_value is None or (
        isinstance(canonical_value, str) and not canonical_value.strip()
    ):
        return MismatchReason.MANIFEST_VALUE_MISSING, "warning", False

    canonical_raw = str(canonical_value)
    source_raw = str(source_value)
    if canonical_raw == source_raw:
        return MismatchReason.UNKNOWN_MISMATCH, "info", True

    canonical_nfc = normalize_nfc(canonical_raw)
    source_nfc = normalize_nfc(source_raw)
    if canonical_nfc == source_nfc:
        return MismatchReason.UNICODE_NORMALIZATION_DIFFERENCE, "info", True

    canonical_normalized = normalize_comparison_text(canonical_raw)
    source_normalized = normalize_comparison_text(source_raw)
    if canonical_normalized == source_normalized:
        return MismatchReason.WHITESPACE_DIFFERENCE, "info", True

    if field == "rcept_dt":
        canonical_date = _parse_flexible_date(canonical_raw)
        source_date = _parse_flexible_date(source_raw)
        if canonical_date is not None and canonical_date == source_date:
            return MismatchReason.DATE_FORMAT_DIFFERENCE, "info", True

    if field == "report_nm" and _compact_text(canonical_raw) == _compact_text(source_raw):
        return MismatchReason.REPORT_NAME_FORMAT_DIFFERENCE, "info", True

    if field == "corp_name":
        if company_identity_ambiguous:
            return MismatchReason.PARSER_EXTRACTION_AMBIGUOUS, "warning", False
        listed_key = (
            _company_display_key(listed_name_value)
            if listed_name_value is not None
            else ""
        )
        if _company_display_key(canonical_raw) == _company_display_key(
            source_raw
        ) or (listed_key and listed_key == _company_display_key(source_raw)):
            return MismatchReason.DISPLAY_NAME_DIFFERENCE, "info", False

    if field in CANONICAL_METADATA_FIELDS:
        return MismatchReason.SOURCE_VALUE_DIFFERENT, "warning", False
    return MismatchReason.UNKNOWN_MISMATCH, "warning", False


def stable_mismatch_id(
    *,
    doc_id: str,
    rcept_no: str,
    source_file: str | None,
    file_role: str | None,
    canonical_field: str,
    source_field: str,
    occurrence_index: int,
) -> str:
    value = "\x1f".join(
        (
            doc_id,
            rcept_no,
            source_file or "",
            file_role or "",
            canonical_field,
            source_field,
            str(occurrence_index),
        )
    )
    return "mismatch_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def extract_source_observations(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Extract only evidence-bearing metadata observations from Phase 2 components."""

    components: list[Mapping[str, Any]] = []
    main = parsed.get("main_document")
    if isinstance(main, Mapping):
        components.append(main)
    attachments = parsed.get("attachments")
    if _is_sequence(attachments):
        components.extend(item for item in attachments if isinstance(item, Mapping))
    alternatives = parsed.get("alternative_representations")
    if _is_sequence(alternatives):
        components.extend(item for item in alternatives if isinstance(item, Mapping))

    observations: list[dict[str, Any]] = []
    for component in components:
        source_file = component.get("source_file")
        source_record = source_file if isinstance(source_file, Mapping) else {}
        metadata = component.get("source_metadata")
        source_metadata = metadata if isinstance(metadata, Mapping) else {}
        values: dict[str, Any] = {}
        for source_field, canonical_field in SOURCE_TO_CANONICAL_FIELD.items():
            raw_key = f"{source_field}_raw"
            provenance_key = f"{source_field}_provenance"
            if source_field not in source_metadata and raw_key not in source_metadata:
                continue
            raw_value = source_metadata.get(raw_key, source_metadata.get(source_field))
            values[source_field] = {
                "canonical_field": canonical_field,
                "raw": raw_value,
                "normalized": normalize_comparison_text(raw_value),
                "provenance": source_metadata.get(provenance_key),
            }
        if not values:
            continue
        observations.append(
            {
                "source_file": source_record.get("relative_path"),
                "file_role": source_record.get("role"),
                "detected_format": source_record.get("detected_format"),
                "values": values,
            }
        )
    return {"observations": observations}


__all__ = [
    "CANONICAL_METADATA_FIELDS",
    "COMPARISON_POLICY_VERSION",
    "CONTENT_SCHEMA_VERSION",
    "EXPECTED_DOC_GROUPS",
    "IDENTIFIER_FIELDS",
    "MetadataValidationStatus",
    "MismatchReason",
    "NULLABLE_FIELDS",
    "PHASE3_SCHEMA_VERSION",
    "REQUIRED_FIELDS",
    "SAFE_FILTER_FIELDS",
    "SAMPLE_POLICY_VERSION",
    "SOURCE_TO_CANONICAL_FIELD",
    "TEXT_FIELDS",
    "build_canonical_metadata",
    "classify_mismatch_reason",
    "extract_source_observations",
    "format_rcept_date",
    "metadata_warning",
    "normalize_comparison_text",
    "stable_mismatch_id",
    "validate_canonical_metadata",
    "validation_status",
]
