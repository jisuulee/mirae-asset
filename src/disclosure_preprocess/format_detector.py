"""Content-first file-format and encoding inspection utilities.

The corpus contains files whose suffix does not describe their content (most
notably HTML documents stored with an ``.xml`` suffix).  Detection in this
module therefore uses a small prefix of each file and treats the suffix only
as supporting evidence.
"""

from __future__ import annotations

import codecs
import csv
import io
import re
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Literal, TypeAlias


DetectedFormat: TypeAlias = Literal[
    "xml", "html", "json", "pdf", "csv", "xlsx", "markdown", "unknown"
]

# 64 KiB is large enough to include normal HTML/XML prologs and the first ZIP
# member names in an XLSX file, but remains bounded independently of file size.
DEFAULT_PREFIX_BYTES = 64 * 1024
_TEXT_SAMPLE_BYTES = 16 * 1024
_UTF8_BOM = codecs.BOM_UTF8

_HTML_DOCTYPE_RE = re.compile(rb"<!doctype\s+html\b", re.IGNORECASE)
_HTML_ROOT_RE = re.compile(rb"<html(?:\s|>)", re.IGNORECASE)
_HTML_FRAGMENT_ROOT_RE = re.compile(rb"<(?:head|body)(?:\s|>)", re.IGNORECASE)
_XML_DECLARATION_RE = re.compile(rb"<\?xml(?:\s|\?)", re.IGNORECASE)
_DOCUMENT_ROOT_RE = re.compile(rb"<DOCUMENT(?:\s|>)")
_GENERIC_XML_ROOT_RE = re.compile(
    rb"<(?![!?])(?:[A-Za-z_]|[^\x00-\x7f])"
    rb"(?:[A-Za-z0-9_.:-]|[^\x00-\x7f])*(?:\s|/?>)"
)
_XML_ENCODING_RE = re.compile(
    rb"<\?xml\b[^>]{0,512}?\bencoding\s*=\s*(['\"])([A-Za-z0-9._:+-]+)\1",
    re.IGNORECASE,
)
_META_TAG_RE = re.compile(rb"<meta\b[^>]{0,2048}>", re.IGNORECASE)
_CHARSET_RE = re.compile(
    rb"\bcharset\s*=\s*(?:['\"]\s*)?([A-Za-z0-9._:+-]+)", re.IGNORECASE
)
_MARKDOWN_BLOCK_RE = re.compile(
    r"(?m)^(?: {0,3}#{1,6}[ \t]+\S| {0,3}(?:[-+*]|\d+[.)])[ \t]+\S|"
    r" {0,3}>[ \t]+\S| {0,3}```| {0,3}~~~)"
)
_MARKDOWN_LINK_RE = re.compile(r"!?(?:\[[^\]\r\n]+\])\([^\)\r\n]+\)")
_MARKDOWN_TABLE_SEPARATOR_RE = re.compile(
    r"(?m)^\s*\|?(?:\s*:?-{3,}:?\s*\|){1,}\s*:?-{3,}:?\s*\|?\s*$"
)


@dataclass(frozen=True, slots=True)
class EncodingInspection:
    """Encoding facts that can be established without loading a whole file.

    ``declaration_mismatch`` is deliberately nullable.  When a document is
    neither declared as UTF-8 nor UTF-8-decodable, a byte-level check cannot
    prove which legacy encoding was actually used.
    """

    declared_charset: str | None
    utf8_decodable: bool
    contains_non_ascii: bool
    declaration_mismatch: bool | None


def read_prefix(
    path: str | PathLike[str], *, max_bytes: int = DEFAULT_PREFIX_BYTES
) -> bytes:
    """Read at most ``max_bytes`` from *path*.

    This helper intentionally does not use :meth:`Path.read_bytes`, which
    would load an entire disclosure into memory.
    """

    if max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero")
    with Path(path).open("rb") as file:
        return file.read(max_bytes)


def detect_format(
    path: str | PathLike[str], *, prefix_bytes: int = DEFAULT_PREFIX_BYTES
) -> DetectedFormat:
    """Detect the physical format at *path* from a bounded content prefix."""

    file_path = Path(path)
    prefix = read_prefix(file_path, max_bytes=prefix_bytes)
    return detect_format_from_bytes(prefix, extension=file_path.suffix)


def detect_file_format(
    path: str | PathLike[str], *, prefix_bytes: int = DEFAULT_PREFIX_BYTES
) -> DetectedFormat:
    """Compatibility-friendly alias for :func:`detect_format`."""

    return detect_format(path, prefix_bytes=prefix_bytes)


def detect_format_from_bytes(
    prefix: bytes, *, extension: str | None = None
) -> DetectedFormat:
    """Classify a file prefix, using *extension* only as auxiliary evidence.

    The function never labels arbitrary bytes based solely on a suffix.  For
    example, an ``.xlsx`` suffix additionally requires a ZIP signature, and
    an ``.xml`` suffix cannot override an HTML root element.
    """

    if not prefix:
        return "unknown"

    suffix = _normalise_extension(extension)
    stripped = _strip_text_prefix(prefix)

    if prefix.startswith(b"%PDF-") or stripped.startswith(b"%PDF-"):
        return "pdf"

    if _has_zip_signature(prefix):
        if _contains_xlsx_markers(prefix):
            return "xlsx"
        return "unknown"

    # HTML is checked before XML because XHTML can have an XML declaration,
    # and because DART HTML is frequently stored under an .xml suffix.
    markup_sample = stripped[:_TEXT_SAMPLE_BYTES]
    if _looks_like_html(markup_sample, suffix):
        return "html"

    if _looks_like_xml(markup_sample, suffix):
        return "xml"

    text = _decode_text_sample(stripped[:_TEXT_SAMPLE_BYTES])
    if text is None:
        return "unknown"
    text = text.lstrip("\ufeff \t\r\n")
    if not text:
        return "unknown"

    if _looks_like_json(text):
        return "json"

    # A Markdown pipe table is also delimiter-shaped, so strong Markdown
    # syntax must be considered before CSV heuristics.
    if _looks_like_markdown(text, suffix):
        return "markdown"

    if _looks_like_csv(text, suffix):
        return "csv"

    return "unknown"


def detect_declared_charset(prefix: bytes) -> str | None:
    """Return an XML/HTML charset declaration found in *prefix*.

    The returned spelling is lower-case with surrounding whitespace removed;
    it is not otherwise rewritten, so reports retain recognisable values such
    as ``euc-kr``.
    """

    sample = prefix[:DEFAULT_PREFIX_BYTES]
    xml_match = _XML_ENCODING_RE.search(sample)
    if xml_match:
        return xml_match.group(2).decode("ascii").lower()

    for meta_match in _META_TAG_RE.finditer(sample):
        charset_match = _CHARSET_RE.search(meta_match.group(0))
        if charset_match:
            return charset_match.group(1).decode("ascii").lower()
    return None


def detect_declared_charset_from_file(
    path: str | PathLike[str], *, prefix_bytes: int = DEFAULT_PREFIX_BYTES
) -> str | None:
    """Read a bounded prefix and return its declared charset, if present."""

    return detect_declared_charset(read_prefix(path, max_bytes=prefix_bytes))


def is_utf8_decodable(
    source: bytes | bytearray | memoryview | str | PathLike[str],
    *,
    chunk_size: int = DEFAULT_PREFIX_BYTES,
) -> bool:
    """Check strict UTF-8 decodability of bytes or a file, streaming files.

    A final decoder flush is important: it rejects a file ending midway
    through a multibyte sequence.
    """

    if isinstance(source, (bytes, bytearray, memoryview)):
        try:
            bytes(source).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return False
        return True

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        with Path(source).open("rb") as file:
            while chunk := file.read(chunk_size):
                decoder.decode(chunk, final=False)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def inspect_encoding(
    path: str | PathLike[str], *, chunk_size: int = DEFAULT_PREFIX_BYTES
) -> EncodingInspection:
    """Inspect declared charset and actual UTF-8 validity of *path* safely."""

    file_path = Path(path)
    declared = detect_declared_charset_from_file(
        file_path, prefix_bytes=DEFAULT_PREFIX_BYTES
    )
    utf8_ok, contains_non_ascii = _stream_utf8_facts(file_path, chunk_size=chunk_size)
    mismatch = _declaration_mismatch(
        declared,
        utf8_decodable=utf8_ok,
        contains_non_ascii=contains_non_ascii,
    )
    return EncodingInspection(
        declared_charset=declared,
        utf8_decodable=utf8_ok,
        contains_non_ascii=contains_non_ascii,
        declaration_mismatch=mismatch,
    )


def _normalise_extension(extension: str | None) -> str:
    if not extension:
        return ""
    value = extension.strip().lower()
    if not value:
        return ""
    # Accept either '.xml', 'xml', or a complete filename for convenience.
    if "/" in value or "\\" in value or ("." in value and not value.startswith(".")):
        value = Path(value).suffix
    elif not value.startswith("."):
        value = f".{value}"
    return value


def _strip_text_prefix(data: bytes) -> bytes:
    if data.startswith(_UTF8_BOM):
        data = data[len(_UTF8_BOM) :]
    return data.lstrip(b" \t\r\n")


def _has_zip_signature(data: bytes) -> bool:
    return data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))


def _contains_xlsx_markers(data: bytes) -> bool:
    lowered = data.lower()
    return (
        b"[content_types].xml" in lowered
        and (
            b"xl/" in lowered
            or b"spreadsheetml" in lowered
            or b"application/vnd.openxmlformats-officedocument.spreadsheetml" in lowered
        )
    )


def _looks_like_html(sample: bytes, suffix: str) -> bool:
    candidate = sample
    if _XML_DECLARATION_RE.match(candidate):
        declaration_end = candidate.find(b"?>")
        if declaration_end < 0:
            return False
        candidate = candidate[declaration_end + 2 :].lstrip()

    for _ in range(4):
        if candidate.startswith(b"<!--"):
            comment_end = candidate.find(b"-->")
            if comment_end < 0:
                return False
            candidate = candidate[comment_end + 3 :].lstrip()
            continue
        if _HTML_DOCTYPE_RE.match(candidate):
            return True
        break

    if _HTML_ROOT_RE.match(candidate):
        return True
    return suffix in {".html", ".htm"} and bool(
        _HTML_FRAGMENT_ROOT_RE.match(candidate)
    )


def _looks_like_xml(sample: bytes, suffix: str) -> bool:
    if _XML_DECLARATION_RE.match(sample) or _DOCUMENT_ROOT_RE.match(sample):
        return True

    # Skip comments and a doctype when locating a root without a declaration.
    candidate = sample
    for _ in range(4):
        if candidate.startswith(b"<!--"):
            end = candidate.find(b"-->")
            if end < 0:
                return suffix == ".xml"
            candidate = candidate[end + 3 :].lstrip()
            continue
        if candidate[:9].lower() == b"<!doctype":
            end = candidate.find(b">")
            if end < 0:
                return suffix == ".xml"
            candidate = candidate[end + 1 :].lstrip()
            continue
        break
    return bool(_GENERIC_XML_ROOT_RE.match(candidate))


def _decode_text_sample(data: bytes) -> str | None:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # ASCII punctuation remains one-to-one under Latin-1, which is enough
        # for CSV/Markdown structure detection in legacy Korean encodings.
        return data.decode("latin-1")


def _looks_like_json(text: str) -> bool:
    first = text[:1]
    if first not in {"{", "["}:
        return False
    # Check the first grammar token rather than requiring the complete value:
    # the supplied bytes may intentionally be only a prefix of a large file.
    # This also avoids treating Markdown links such as ``[label](url)`` as
    # JSON merely because they begin with a square bracket.
    if first == "{":
        return bool(re.match(r'^\{\s*(?:\}|\")', text)) and (
            text.rstrip() == "{}" or ":" in text
        )
    return bool(
        re.match(
            r'^\[\s*(?:\]|[\[{\"\-0-9]|true\b|false\b|null\b)',
            text,
        )
    )


def _looks_like_markdown(text: str, suffix: str) -> bool:
    if _MARKDOWN_BLOCK_RE.search(text):
        return True
    if _MARKDOWN_LINK_RE.search(text) or _MARKDOWN_TABLE_SEPARATOR_RE.search(text):
        return True
    if suffix in {".md", ".markdown", ".mdown", ".mkd"}:
        return _is_probably_text(text)
    return False


def _looks_like_csv(text: str, suffix: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()][:20]
    if not lines:
        return False

    # csv.Sniffer alone has false positives on prose, so require a plausible
    # delimiter and consistent multi-column rows.
    for delimiter in (",", "\t", ";"):
        try:
            rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
        except csv.Error:
            continue
        widths = [len(row) for row in rows]
        if len(widths) >= 2 and min(widths) > 1 and len(set(widths)) == 1:
            return True
        if suffix in {".csv", ".tsv"} and len(widths) == 1 and widths[0] > 1:
            return True

    # A one-column CSV has no content-level signature.  Its suffix can break
    # the tie only after the bytes have been established as ordinary text.
    return suffix in {".csv", ".tsv"} and _is_probably_text(text)


def _is_probably_text(text: str) -> bool:
    if not text:
        return False
    control_count = sum(
        1 for character in text if ord(character) < 32 and character not in "\t\r\n\f"
    )
    return control_count / len(text) < 0.01


def _stream_utf8_facts(path: Path, *, chunk_size: int) -> tuple[bool, bool]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    utf8_ok = True
    contains_non_ascii = False
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            contains_non_ascii = contains_non_ascii or not chunk.isascii()
            if utf8_ok:
                try:
                    decoder.decode(chunk, final=False)
                except UnicodeDecodeError:
                    utf8_ok = False
        if utf8_ok:
            try:
                decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                utf8_ok = False
    return utf8_ok, contains_non_ascii


def _declaration_mismatch(
    declared: str | None,
    *,
    utf8_decodable: bool,
    contains_non_ascii: bool,
) -> bool | None:
    if declared is None:
        return None

    try:
        declared_codec = codecs.lookup(declared).name
    except LookupError:
        return True

    declares_utf8 = declared_codec in {"utf-8", "utf-8-sig"}
    if declares_utf8:
        return not utf8_decodable
    if not contains_non_ascii:
        # ASCII is valid in UTF-8 and in the legacy ASCII-compatible encodings
        # normally encountered in DART; the declaration is not contradicted.
        return False
    if utf8_decodable:
        return True
    return None


__all__ = [
    "DEFAULT_PREFIX_BYTES",
    "DetectedFormat",
    "EncodingInspection",
    "detect_declared_charset",
    "detect_declared_charset_from_file",
    "detect_file_format",
    "detect_format",
    "detect_format_from_bytes",
    "inspect_encoding",
    "is_utf8_decodable",
    "read_prefix",
]
