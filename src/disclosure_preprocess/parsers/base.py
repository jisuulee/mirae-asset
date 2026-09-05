"""Shared parser contracts, bounded per-file reads, and strict decoding."""

from __future__ import annotations

import codecs
import hashlib
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, BinaryIO

from disclosure_preprocess.format_detector import detect_declared_charset
from disclosure_preprocess.models import (
    ParseResult,
    ParseStatus,
    ParseWarning,
    Provenance,
    SourceFileRecord,
    WarningSeverity,
)


ENCODING_DECLARATION_MISMATCH = "ENCODING_DECLARATION_MISMATCH"
ENCODING_DECLARATION_UNKNOWN = "ENCODING_DECLARATION_UNKNOWN"
ENCODING_FALLBACK_USED = "ENCODING_FALLBACK_USED"
TEXT_DECODING_FAILED = "TEXT_DECODING_FAILED"
PARSER_DEPENDENCY_MISSING = "PARSER_DEPENDENCY_MISSING"
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"


@dataclass(slots=True)
class ParseContext:
    """Facts known before a physical source file is parsed."""

    relative_path: str
    detected_format: str
    role: str = "unknown"
    original_path: str | None = None
    size_bytes: int | None = None
    is_canonical: bool = False
    manifest_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def extension(self) -> str:
        return PurePosixPath(self.relative_path).suffix.lower()


@dataclass(slots=True)
class BinaryPayload:
    """One physical file held only for the duration of its parse."""

    data: bytes
    sha256: str


@dataclass(slots=True)
class DecodedText:
    text: str | None
    encoding: str | None
    declared_charset: str | None
    warnings: list[ParseWarning] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.text is not None


def read_binary_stream(
    stream: BinaryIO, *, chunk_size: int = 1024 * 1024
) -> BinaryPayload:
    """Read and hash one physical file without retaining corpus-wide bytes."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    digest = hashlib.sha256()
    buffer = io.BytesIO()
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
        buffer.write(chunk)
    return BinaryPayload(data=buffer.getvalue(), sha256=digest.hexdigest())


def make_source_file_record(
    context: ParseContext, payload: BinaryPayload
) -> SourceFileRecord:
    return SourceFileRecord(
        relative_path=context.relative_path,
        original_path=context.original_path,
        role=context.role,
        detected_format=context.detected_format,
        size_bytes=(
            context.size_bytes
            if context.size_bytes is not None
            else len(payload.data)
        ),
        sha256=payload.sha256,
        declared_charset=detect_declared_charset(payload.data[: 64 * 1024]),
        encoding=None,
        is_canonical=context.is_canonical,
    )


def _provenance(context: ParseContext) -> Provenance:
    return Provenance(
        source_file=context.relative_path,
        file_role=context.role,
        source_index=0,
    )


def _codec_name(value: str) -> str | None:
    try:
        return codecs.lookup(value).name
    except LookupError:
        return None


def decode_text_payload(data: bytes, context: ParseContext) -> DecodedText:
    """Decode with strict UTF-8 first and only explicit strict fallbacks.

    No ``ignore`` or replacement error handler is used.  A non-UTF-8
    declaration does not control decoding when the bytes are valid UTF-8;
    this is important for the 1,469 exchange files whose declarations are
    contradicted by their byte content.
    """

    declared = detect_declared_charset(data[: 64 * 1024])
    warnings: list[ParseWarning] = []
    provenance = _provenance(context)

    utf8_failure: UnicodeDecodeError | None = None
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        utf8_failure = exc
        text = None
    else:
        if declared and not data.isascii():
            declared_name = _codec_name(declared)
            if declared_name is None:
                warnings.append(
                    ParseWarning(
                        code=ENCODING_DECLARATION_UNKNOWN,
                        message=f"Unknown declared charset: {declared}",
                        provenance=provenance,
                        details={"declared_charset": declared},
                    )
                )
            elif declared_name not in {"utf-8", "utf-8-sig"}:
                warnings.append(
                    ParseWarning(
                        code=ENCODING_DECLARATION_MISMATCH,
                        message=(
                            "The declared charset conflicts with strict UTF-8 "
                            "byte validation; UTF-8 was used"
                        ),
                        provenance=provenance,
                        details={
                            "declared_charset": declared,
                            "decoded_encoding": "utf-8-sig",
                        },
                    )
                )
        return DecodedText(text, "utf-8-sig", declared, warnings)

    candidates: list[str] = []
    if declared:
        if _codec_name(declared) is None:
            warnings.append(
                ParseWarning(
                    code=ENCODING_DECLARATION_UNKNOWN,
                    message=f"Unknown declared charset: {declared}",
                    provenance=provenance,
                    details={"declared_charset": declared},
                )
            )
        else:
            candidates.append(declared)
    candidates.extend(["cp949", "euc-kr"])

    attempted: list[str] = ["utf-8-sig"]
    for candidate in candidates:
        canonical = _codec_name(candidate)
        if canonical is None or canonical in {
            _codec_name(item) for item in attempted if _codec_name(item)
        }:
            continue
        attempted.append(candidate)
        try:
            text = data.decode(candidate, errors="strict")
        except UnicodeDecodeError:
            continue
        warnings.append(
            ParseWarning(
                code=ENCODING_FALLBACK_USED,
                message=f"Strict UTF-8 failed; strict {canonical} decoding was used",
                provenance=provenance,
                details={
                    "declared_charset": declared,
                    "decoded_encoding": canonical,
                    "utf8_error_start": getattr(utf8_failure, "start", None),
                },
            )
        )
        return DecodedText(text, canonical, declared, warnings)

    warnings.append(
        ParseWarning(
            code=TEXT_DECODING_FAILED,
            message="The source could not be decoded with any strict candidate",
            severity=WarningSeverity.ERROR,
            provenance=provenance,
            details={
                "declared_charset": declared,
                "attempted_encodings": attempted,
                "utf8_error_start": getattr(utf8_failure, "start", None),
            },
        )
    )
    return DecodedText(None, None, declared, warnings)


class BaseParser(ABC):
    """Interface implemented by every physical-format parser."""

    parser_name = "base"

    @abstractmethod
    def parse(
        self,
        payload: BinaryPayload,
        context: ParseContext,
        source_file: SourceFileRecord,
    ) -> ParseResult:
        raise NotImplementedError

    def failed_result(
        self,
        source_file: SourceFileRecord,
        warning: ParseWarning,
        *,
        source_metadata: dict[str, Any] | None = None,
    ) -> ParseResult:
        source_file.parser = self.parser_name
        return ParseResult(
            source_file=source_file,
            parse_status=ParseStatus.FAILED,
            warnings=[warning],
            parser_name=self.parser_name,
            source_metadata=source_metadata or {},
        )


__all__ = [
    "BaseParser",
    "BinaryPayload",
    "DecodedText",
    "ENCODING_DECLARATION_MISMATCH",
    "ENCODING_DECLARATION_UNKNOWN",
    "ENCODING_FALLBACK_USED",
    "PARSER_DEPENDENCY_MISSING",
    "ParseContext",
    "TEXT_DECODING_FAILED",
    "UNSUPPORTED_FORMAT",
    "decode_text_payload",
    "make_source_file_record",
    "read_binary_stream",
]
