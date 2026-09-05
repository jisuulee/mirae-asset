"""Validation-only parser for auxiliary JSON corpus files.

The 280 ``list_*.json`` files describe collection results; they are not
logical disclosures.  This parser therefore validates their JSON shape and
records compact schema hints without copying records into the disclosure IR.
"""

from __future__ import annotations

import json
from typing import Any

from disclosure_preprocess.models import (
    ParseResult,
    ParseStatus,
    ParseWarning,
    Provenance,
    SourceFileRecord,
    WarningSeverity,
)

from .base import BaseParser, BinaryPayload, ParseContext, decode_text_payload


JSON_PARSE_FAILED = "JSON_PARSE_FAILED"
JSON_ROOT_TYPE_UNSUPPORTED = "JSON_ROOT_TYPE_UNSUPPORTED"
JSON_RECORD_SCHEMA_MIXED = "JSON_RECORD_SCHEMA_MIXED"

_RECORD_CONTAINER_KEYS = ("list", "records", "items", "data")


def _provenance(context: ParseContext) -> Provenance:
    return Provenance(
        source_file=context.relative_path,
        file_role=context.role,
        source_index=0,
        dom_path="$",
        tag="json",
    )


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _record_view(root: dict[str, Any] | list[Any]) -> tuple[list[Any], str]:
    if isinstance(root, list):
        return root, "$"
    for key in _RECORD_CONTAINER_KEYS:
        candidate = root.get(key)
        if isinstance(candidate, list):
            return candidate, f"$.{key}"
    # A root object is itself a valid single JSON record.  We do not guess
    # that an arbitrary nested array is the authoritative record container.
    return [root], "$"


def _schema_hints(
    root: dict[str, Any] | list[Any], records: list[Any], record_container: str
) -> dict[str, Any]:
    object_records = [record for record in records if isinstance(record, dict)]
    key_sets = [set(record) for record in object_records]
    key_union = set().union(*key_sets) if key_sets else set()
    key_intersection = set.intersection(*key_sets) if key_sets else set()

    value_types: dict[str, list[str]] = {}
    for key in sorted(key_union):
        value_types[key] = sorted(
            {
                _json_type_name(record[key])
                for record in object_records
                if key in record
            }
        )

    hints: dict[str, Any] = {
        "record_container": record_container,
        "object_record_count": len(object_records),
        "non_object_record_count": len(records) - len(object_records),
        "record_field_union": sorted(key_union),
        "record_field_intersection": sorted(key_intersection),
        "record_field_types": value_types,
    }
    if isinstance(root, dict):
        hints["root_keys"] = sorted(root)
        hints["root_array_fields"] = sorted(
            key for key, value in root.items() if isinstance(value, list)
        )
    return hints


class JsonParser(BaseParser):
    """Strictly decode and validate JSON without producing disclosure text."""

    parser_name = "json"

    def parse(
        self,
        payload: BinaryPayload,
        context: ParseContext,
        source_file: SourceFileRecord,
    ) -> ParseResult:
        source_file.parser = self.parser_name
        decoded = decode_text_payload(payload.data, context)
        source_file.declared_charset = decoded.declared_charset
        source_file.encoding = decoded.encoding
        warnings = list(decoded.warnings)
        provenance = _provenance(context)

        if not decoded.succeeded:
            return ParseResult(
                source_file=source_file,
                parse_status=ParseStatus.FAILED,
                warnings=warnings,
                parser_name=self.parser_name,
                source_metadata={"auxiliary_only": True},
            )

        try:
            root = json.loads(decoded.text)
        except json.JSONDecodeError as error:
            warnings.append(
                ParseWarning(
                    code=JSON_PARSE_FAILED,
                    message="The auxiliary file is not valid JSON",
                    severity=WarningSeverity.ERROR,
                    provenance=provenance,
                    details={
                        "line": error.lineno,
                        "column": error.colno,
                        "character_offset": error.pos,
                    },
                )
            )
            return ParseResult(
                source_file=source_file,
                parse_status=ParseStatus.FAILED,
                warnings=warnings,
                parser_name=self.parser_name,
                source_metadata={"auxiliary_only": True},
            )

        if not isinstance(root, (dict, list)):
            root_type = _json_type_name(root)
            warnings.append(
                ParseWarning(
                    code=JSON_ROOT_TYPE_UNSUPPORTED,
                    message="Auxiliary JSON must have an object or array root",
                    severity=WarningSeverity.ERROR,
                    provenance=provenance,
                    details={"root_type": root_type},
                )
            )
            return ParseResult(
                source_file=source_file,
                parse_status=ParseStatus.FAILED,
                warnings=warnings,
                parser_name=self.parser_name,
                source_metadata={
                    "auxiliary_only": True,
                    "root_type": root_type,
                    "record_count": 0,
                },
            )

        records, record_container = _record_view(root)
        schema_hints = _schema_hints(root, records, record_container)
        non_object_count = schema_hints["non_object_record_count"]
        status = ParseStatus.SUCCESS
        if non_object_count:
            warnings.append(
                ParseWarning(
                    code=JSON_RECORD_SCHEMA_MIXED,
                    message="Some auxiliary JSON records are not objects",
                    provenance=provenance,
                    details={
                        "record_count": len(records),
                        "non_object_record_count": non_object_count,
                    },
                )
            )
            status = ParseStatus.PARTIAL

        return ParseResult(
            source_file=source_file,
            parse_status=status,
            content=None,
            warnings=warnings,
            parser_name=self.parser_name,
            source_metadata={
                "auxiliary_only": True,
                "root_type": _json_type_name(root),
                "record_count": len(records),
                "schema_hints": schema_hints,
            },
        )


# The longer name is useful to callers that want to make the non-disclosure
# semantics explicit while retaining the conventional factory class name.
JsonAuxiliaryParser = JsonParser


__all__ = [
    "JSON_PARSE_FAILED",
    "JSON_RECORD_SCHEMA_MIXED",
    "JSON_ROOT_TYPE_UNSUPPORTED",
    "JsonAuxiliaryParser",
    "JsonParser",
]
