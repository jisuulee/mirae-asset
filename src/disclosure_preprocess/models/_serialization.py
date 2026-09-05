"""Small JSON-serialization helpers shared by the parser IR models."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


def to_json_value(value: Any) -> Any:
    """Recursively convert model values to values accepted by ``json.dumps``.

    This helper itself performs no textual normalization.  A parser's
    ``raw_*`` strings are decoded, source-derived views; XML/HTML entity and
    newline handling means they must not be described as byte-for-byte copies.
    """

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_json_value(getattr(value, field.name))
            for field in fields(value)
            if not field.metadata.get("json_skip", False)
        }
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    return value


class JsonSerializable:
    """Mixin that gives dataclass IR objects an explicit ``to_dict`` API."""

    def to_dict(self) -> dict[str, Any]:
        converted = to_json_value(self)
        if not isinstance(converted, dict):  # pragma: no cover - defensive
            raise TypeError("A model must serialize to a JSON object")
        return converted


__all__ = ["JsonSerializable", "to_json_value"]
