"""Shared evidence-preserving extraction for DART XML and tolerant HTML."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from lxml import etree, html as lxml_html

from disclosure_preprocess.models import (
    BlockType,
    ContentBlock,
    DocumentContent,
    ListItem,
    ParseResult,
    ParseStatus,
    ParseWarning,
    Provenance,
    Section,
    SourceFileRecord,
    Table,
    TableCell,
    WarningSeverity,
)
from disclosure_preprocess.normalizers import normalize_table
from disclosure_preprocess.normalizers.text import normalize_text

from .base import (
    BaseParser,
    BinaryPayload,
    ParseContext,
    decode_text_payload,
)


EMPTY_BODY = "EMPTY_BODY"
MARKUP_PARSE_FAILED = "MARKUP_PARSE_FAILED"
MARKUP_RECOVERY_APPLIED = "MARKUP_RECOVERY_APPLIED"
MALFORMED_TABLE = "MALFORMED_TABLE"
SECTION_HIERARCHY_UNCERTAIN = "SECTION_HIERARCHY_UNCERTAIN"
TITLE_NOT_FOUND = "TITLE_NOT_FOUND"

_XML_ENCODING_RE = re.compile(
    r"(<\?xml\b[^?>]*?\bencoding\s*=\s*)(['\"])[^'\"]+\2",
    re.IGNORECASE,
)
_BARE_AMPERSAND_RE = re.compile(
    r"&(?!(?:#(?:[0-9]+|[xX][0-9A-Fa-f]+)|[A-Za-z_:][A-Za-z0-9_.:-]*);)"
)
_PROTECTED_XML_START_RE = re.compile(
    r"<!\[CDATA\[|<!--|<\?|<!doctype\b",
    re.IGNORECASE,
)
_SECTION_LEVEL_RE = re.compile(r"^section[-_]?([1-9][0-9]*)$", re.IGNORECASE)
_STYLE_HIDDEN_RE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)(?:\s*!important)?\s*(?:;|$)",
    re.IGNORECASE,
)

_SKIP_TAGS = {"script", "style", "noscript", "template"}
_HEADING_TAGS = {
    "title",
    "subtitle",
    "sub-title",
    "section-title",
    "cover-title",
}
_PARAGRAPH_TAGS = {"p", "paragraph", "text", "pre", "blockquote"}
_TEXT_CONTAINER_TAGS = {"div", "center", "article", "aside"}
_INLINE_TAGS = {
    "a",
    "abbr",
    "b",
    "big",
    "cite",
    "code",
    "del",
    "em",
    "font",
    "i",
    "ins",
    "label",
    "mark",
    "q",
    "s",
    "small",
    "span",
    "strike",
    "strong",
    "sub",
    "sup",
    "u",
    "var",
}
_LIST_TAGS = {"ul", "ol"}
_BLOCK_TAGS = (
    _HEADING_TAGS
    | _PARAGRAPH_TAGS
    | _TEXT_CONTAINER_TAGS
    | _LIST_TAGS
    | {"table", "h1", "h2", "h3", "h4", "h5", "h6"}
)
_METADATA_ONLY_TAGS = {
    "document-name",
    "document_name",
    "company-name",
    "company_name",
    "corp-name",
    "corp_name",
    "formula-version",
    "formula_version",
    "form-name",
    "form_name",
}
_SOURCE_METADATA_TAGS = {
    "document_name": {"document-name", "document_name"},
    "company_name": {"company-name", "company_name", "corp-name", "corp_name"},
    "rcept_no": {"rcept-no", "rcept_no", "receipt-no", "receipt_no"},
    "form_name": {"form-name", "form_name"},
    "formula_version": {"formula-version", "formula_version"},
}


def _local_name(element: etree._Element) -> str:
    if not isinstance(element.tag, str):
        return ""
    try:
        return etree.QName(element).localname.casefold()
    except ValueError:
        return str(element.tag).casefold()


def _attributes(element: etree._Element) -> dict[str, str]:
    return {str(key).casefold(): str(value) for key, value in element.attrib.items()}


def _is_hidden(element: etree._Element) -> bool:
    tag = _local_name(element)
    if tag in _SKIP_TAGS:
        return True
    attrs = _attributes(element)
    if "hidden" in attrs:
        return True
    if attrs.get("aria-hidden", "").strip().casefold() == "true":
        return True
    if tag == "input" and attrs.get("type", "").strip().casefold() == "hidden":
        return True
    return bool(_STYLE_HIDDEN_RE.search(attrs.get("style", "")))


def _is_effectively_hidden(element: etree._Element) -> bool:
    current: etree._Element | None = element
    while current is not None:
        if _is_hidden(current):
            return True
        current = current.getparent()
    return False


def _raw_text(
    element: etree._Element,
    *,
    exclude_nested_tables: bool = False,
    exclude_nested_lists: bool = False,
) -> str:
    """Return decoded DOM text without applying the safe normalized view."""

    pieces: list[str] = []

    def append_block_boundary() -> None:
        if pieces and pieces[-1] and not pieces[-1][-1].isspace():
            pieces.append("\n")

    def visit(node: etree._Element, *, is_root: bool = False) -> None:
        if not is_root and _is_hidden(node):
            return
        if node.text:
            pieces.append(node.text)
        for child in node:
            tag = _local_name(child)
            is_block_boundary = (
                tag in _BLOCK_TAGS
                or tag == "section"
                or _SECTION_LEVEL_RE.match(tag) is not None
            )
            if is_block_boundary:
                append_block_boundary()
            if tag == "br":
                pieces.append("\n")
            elif exclude_nested_tables and tag == "table":
                pass
            elif exclude_nested_lists and tag in _LIST_TAGS:
                pass
            elif not _is_hidden(child):
                visit(child)
            if child.tail:
                if is_block_boundary and not child.tail[0].isspace():
                    append_block_boundary()
                pieces.append(child.tail)

    visit(element, is_root=True)
    return "".join(pieces)


def _escape_xml_bare_ampersands(text: str) -> tuple[str, int]:
    """Escape bare ``&`` outside CDATA/comments/PIs/DOCTYPE for XML parsing.

    The replacement exists only in the parser input.  lxml decodes ``&amp;``
    back to the source character in extracted text.  CDATA and comments are
    copied verbatim, preventing a literal ``&`` there from becoming the text
    ``&amp;``.
    """

    # Work in large slices.  Appending one Python string per source character
    # can use gigabytes of transient memory for the largest DART files.
    output: list[str] = []
    index = 0
    replacement_count = 0
    length = len(text)
    while index < length:
        protected = _PROTECTED_XML_START_RE.search(text, index)
        protected_start = protected.start() if protected is not None else length
        ordinary = text[index:protected_start]
        if ordinary:
            escaped, count = _BARE_AMPERSAND_RE.subn("&amp;", ordinary)
            output.append(escaped)
            replacement_count += count
        if protected is None:
            break

        index = protected_start
        token = protected.group(0).casefold()
        if token == "<![cdata[":
            end = text.find("]]>", index + len(protected.group(0)))
            end = length if end < 0 else end + 3
            output.append(text[index:end])
            index = end
            continue
        if token == "<!--":
            end = text.find("-->", index + 4)
            end = length if end < 0 else end + 3
            output.append(text[index:end])
            index = end
            continue
        if token == "<?":
            end = text.find("?>", index + 2)
            end = length if end < 0 else end + 2
            output.append(text[index:end])
            index = end
            continue
        if token.startswith("<!doctype"):
            cursor = protected.end()
            subset_depth = 0
            quote: str | None = None
            while cursor < length:
                character = text[cursor]
                if quote is not None:
                    if character == quote:
                        quote = None
                elif character in {"'", '"'}:
                    quote = character
                elif character == "[":
                    subset_depth += 1
                elif character == "]" and subset_depth:
                    subset_depth -= 1
                elif character == ">" and subset_depth == 0:
                    cursor += 1
                    break
                cursor += 1
            output.append(text[index:cursor])
            index = cursor
            continue
    return "".join(output), replacement_count


def _nearest_ancestor_table(element: etree._Element) -> etree._Element | None:
    parent = element.getparent()
    while parent is not None:
        if _local_name(parent) == "table":
            return parent
        parent = parent.getparent()
    return None


def _structural_attributes(element: etree._Element) -> dict[str, str]:
    attrs = _attributes(element)
    keys = {
        "rowspan",
        "colspan",
        "scope",
        "headers",
        "axis",
        "atoc",
        "aclass",
        "class",
        "id",
    }
    return {key: attrs[key] for key in sorted(keys & attrs.keys())}


def _table_has_source_text(table: Table) -> bool:
    return any(
        cell.raw_text.strip()
        for row in table.raw_rows
        for cell in row
    )


class _Extractor:
    def __init__(
        self,
        root: etree._Element,
        context: ParseContext,
        *,
        markup_kind: str,
    ) -> None:
        self.root = root
        self.tree = root.getroottree()
        self.context = context
        self.markup_kind = markup_kind
        self.blocks: list[ContentBlock] = []
        self.tables: list[Table] = []
        self.sections: list[Section] = []
        self.warnings: list[ParseWarning] = []
        self._current_section: Section | None = None
        self._uncertain_section_warning_added = False

    def provenance(
        self,
        element: etree._Element,
        *,
        source_index: int | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Provenance:
        try:
            path = self.tree.getpath(element)
        except (ValueError, AttributeError):
            path = None
        details = dict(attributes or {})
        source_line = getattr(element, "sourceline", None)
        if source_line is not None:
            details["source_line"] = source_line
        return Provenance(
            source_file=self.context.relative_path,
            file_role=self.context.role,
            source_index=source_index,
            dom_path=path,
            tag=_local_name(element) or None,
            attributes=details,
        )

    def _append_block(self, block: ContentBlock) -> None:
        block.block_id = block.block_id or f"b{len(self.blocks):06d}"
        if block.provenance is not None:
            block.provenance.source_index = len(self.blocks)
        self.blocks.append(block)
        if self._current_section is not None:
            self._current_section.block_ids.append(block.block_id)
            self._current_section.end_block_index = len(self.blocks) - 1

    def _heading_level(self, element: etree._Element) -> int | None:
        tag = _local_name(element)
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            return int(tag[1])
        parent = element.getparent()
        while parent is not None:
            match = _SECTION_LEVEL_RE.match(_local_name(parent))
            if match:
                return int(match.group(1))
            parent = parent.getparent()
        attrs = _attributes(element)
        for key in ("level", "depth"):
            value = attrs.get(key)
            if value and value.isdigit() and int(value) > 0:
                return int(value)
        return None

    def _emit_heading(self, element: etree._Element) -> None:
        raw = _raw_text(element)
        text = normalize_text(raw)
        if not text:
            return
        level = self._heading_level(element)
        provenance = self.provenance(element)
        block = ContentBlock(
            block_type=BlockType.HEADING,
            raw_text=raw,
            text=text,
            level=level,
            provenance=provenance,
            attributes=_structural_attributes(element),
        )
        if self._current_section is not None:
            self._current_section.end_block_index = len(self.blocks) - 1
        section = Section(
            section_id=f"s{len(self.sections):06d}",
            title=text,
            raw_title=raw,
            level=level,
            start_block_index=len(self.blocks),
            provenance=provenance,
        )
        self.sections.append(section)
        self._current_section = section
        self._append_block(block)
        if level is None and not self._uncertain_section_warning_added:
            self.warnings.append(
                ParseWarning(
                    code=SECTION_HIERARCHY_UNCERTAIN,
                    message=(
                        "A heading was preserved but its hierarchy level was "
                        "not explicit in the source"
                    ),
                    provenance=provenance,
                )
            )
            self._uncertain_section_warning_added = True

    def _emit_text_fragment(
        self,
        element: etree._Element,
        raw: str,
        *,
        fragment_index: int,
    ) -> None:
        text = normalize_text(raw)
        if not text:
            return
        attributes = {"text_fragment_index": fragment_index}
        self._append_block(
            ContentBlock(
                block_type=BlockType.PARAGRAPH,
                raw_text=raw,
                text=text,
                provenance=self.provenance(element, attributes=attributes),
                attributes=attributes,
            )
        )

    def _has_structural_descendant(self, element: etree._Element) -> bool:
        structural = _BLOCK_TAGS | {"pgbrk", "page-break", "pagebreak"}
        return any(
            isinstance(descendant.tag, str)
            and _local_name(descendant) in structural
            and not _is_hidden(descendant)
            for descendant in element.iterdescendants()
        )

    def _walk_mixed(self, element: etree._Element) -> None:
        """Emit inline text fragments around nested structural blocks in order."""

        buffer: list[str] = []
        fragment_index = 0

        def flush() -> None:
            nonlocal fragment_index
            if not buffer:
                return
            raw = "".join(buffer)
            buffer.clear()
            self._emit_text_fragment(
                element,
                raw,
                fragment_index=fragment_index,
            )
            fragment_index += 1

        if element.text:
            buffer.append(element.text)
        for child in element:
            if not isinstance(child.tag, str):
                if child.tail:
                    buffer.append(child.tail)
                continue
            tag = _local_name(child)
            if _is_hidden(child):
                if child.tail:
                    buffer.append(child.tail)
                continue
            if tag == "br":
                buffer.append("\n")
            elif tag in _INLINE_TAGS and not self._has_structural_descendant(child):
                buffer.append(_raw_text(child))
            else:
                flush()
                self.walk(child)
            if child.tail:
                buffer.append(child.tail)
        flush()

    def _direct_list_items(self, element: etree._Element) -> list[etree._Element]:
        items: list[etree._Element] = []
        for descendant in element.iterdescendants():
            if _local_name(descendant) != "li":
                continue
            parent = descendant.getparent()
            nearest_list = None
            while parent is not None:
                if _local_name(parent) in _LIST_TAGS:
                    nearest_list = parent
                    break
                parent = parent.getparent()
            if nearest_list is element:
                items.append(descendant)
        return items

    def _emit_list(self, element: etree._Element) -> None:
        """Emit list-item fragments around nested lists/tables in source order."""

        ordered = _local_name(element) == "ol"
        list_path = self.tree.getpath(element)
        for item_index, item_element in enumerate(self._direct_list_items(element)):
            buffer: list[str] = []
            fragment_index = 0

            def append_boundary() -> None:
                if buffer and buffer[-1] and not buffer[-1][-1].isspace():
                    buffer.append("\n")

            def flush() -> None:
                nonlocal fragment_index
                if not buffer:
                    return
                raw = "".join(buffer)
                buffer.clear()
                text = normalize_text(raw)
                if not text:
                    return
                attributes = {
                    "list_dom_path": list_path,
                    "item_index": item_index,
                    "fragment_index": fragment_index,
                    "is_continuation": fragment_index > 0,
                }
                item_provenance = self.provenance(
                    item_element,
                    attributes=attributes,
                )
                self._append_block(
                    ContentBlock(
                        block_type=BlockType.LIST,
                        items=[
                            ListItem(
                                raw_text=raw,
                                text=text,
                                provenance=item_provenance,
                            )
                        ],
                        provenance=item_provenance,
                        attributes={"ordered": ordered, **attributes},
                    )
                )
                fragment_index += 1

            def visit(node: etree._Element) -> None:
                if node.text:
                    buffer.append(node.text)
                for child in node:
                    if not isinstance(child.tag, str):
                        if child.tail:
                            buffer.append(child.tail)
                        continue
                    tag = _local_name(child)
                    if _is_hidden(child):
                        if child.tail:
                            buffer.append(child.tail)
                        continue
                    if tag == "br":
                        buffer.append("\n")
                    elif tag in (_LIST_TAGS | {"table"}):
                        flush()
                        self.walk(child)
                    else:
                        is_block_boundary = (
                            tag in _BLOCK_TAGS
                            or tag == "section"
                            or _SECTION_LEVEL_RE.match(tag) is not None
                        )
                        if is_block_boundary:
                            append_boundary()
                        visit(child)
                    if child.tail:
                        if (
                            tag in _BLOCK_TAGS
                            and not child.tail[0].isspace()
                        ):
                            append_boundary()
                        buffer.append(child.tail)

            visit(item_element)
            flush()

    def _nested_content_sequence(
        self,
        element: etree._Element,
        *,
        reference_tags: set[str],
    ) -> list[dict[str, Any]]:
        """Preserve text/reference ordering inside nested table or list content."""

        sequence: list[dict[str, Any]] = []
        text_buffer: list[str] = []

        def flush_text() -> None:
            if text_buffer:
                sequence.append({"type": "text", "raw_text": "".join(text_buffer)})
                text_buffer.clear()

        def visit(node: etree._Element) -> None:
            if node.text:
                text_buffer.append(node.text)
            for child in node:
                if not isinstance(child.tag, str):
                    if child.tail:
                        text_buffer.append(child.tail)
                    continue
                tag = _local_name(child)
                if _is_hidden(child):
                    if child.tail:
                        text_buffer.append(child.tail)
                    continue
                if tag == "br":
                    text_buffer.append("\n")
                elif tag in reference_tags:
                    flush_text()
                    sequence.append(
                        {
                            "type": "nested_table" if tag == "table" else "nested_list",
                            "dom_path": self.tree.getpath(child),
                            "tag": tag,
                        }
                    )
                else:
                    visit(child)
                if child.tail:
                    text_buffer.append(child.tail)

        visit(element)
        flush_text()
        return sequence

    def _table_rows(self, table: etree._Element) -> Iterable[etree._Element]:
        for candidate in table.iterdescendants():
            if _local_name(candidate) == "tr" and _nearest_ancestor_table(candidate) is table:
                yield candidate

    def _row_cells(
        self, row: etree._Element, table: etree._Element
    ) -> Iterable[etree._Element]:
        for candidate in row.iterdescendants():
            if (
                _local_name(candidate) in {"td", "th", "te", "tu"}
                and _nearest_ancestor_table(candidate) is table
            ):
                nearest_row = candidate.getparent()
                while nearest_row is not None and _local_name(nearest_row) != "tr":
                    nearest_row = nearest_row.getparent()
                if nearest_row is row:
                    yield candidate

    @staticmethod
    def _span(element: etree._Element, name: str) -> int:
        raw = _attributes(element).get(name)
        if raw is None:
            return 1
        try:
            return int(raw.strip())
        except (TypeError, ValueError, OverflowError):
            return 0

    def _emit_table(
        self,
        element: etree._Element,
        *,
        parent_table_id: str | None = None,
        parent_cell_path: str | None = None,
    ) -> None:
        table_index = len(self.tables)
        table_id = f"t{table_index:06d}"
        raw_rows: list[list[TableCell]] = []
        for row_index, row in enumerate(self._table_rows(element)):
            cells: list[TableCell] = []
            for cell_index, cell in enumerate(self._row_cells(row, element)):
                raw = _raw_text(cell, exclude_nested_tables=True)
                structural: dict[str, Any] = dict(_structural_attributes(cell))
                content_sequence = self._nested_content_sequence(
                    cell, reference_tags={"table"}
                )
                if any(item["type"] == "nested_table" for item in content_sequence):
                    structural["content_sequence"] = content_sequence
                cells.append(
                    TableCell(
                        raw_text=raw,
                        text=normalize_text(raw),
                        rowspan=self._span(cell, "rowspan"),
                        colspan=self._span(cell, "colspan"),
                        is_header=_local_name(cell) == "th",
                        source_row_index=row_index,
                        source_cell_index=cell_index,
                        provenance=self.provenance(
                            cell,
                            attributes=structural,
                        ),
                        attributes=structural,
                    )
                )
            if cells:
                raw_rows.append(cells)

        caption_element = next(
            (
                child
                for child in element.iterdescendants()
                if _local_name(child) == "caption"
                and _nearest_ancestor_table(child) is element
            ),
            None,
        )
        raw_caption = _raw_text(caption_element) if caption_element is not None else None
        caption = normalize_text(raw_caption) if raw_caption is not None else None
        table_attributes: dict[str, Any] = dict(_structural_attributes(element))
        if parent_table_id is not None:
            table_attributes["parent_table_id"] = parent_table_id
        if parent_cell_path is not None:
            table_attributes["parent_cell_dom_path"] = parent_cell_path
        table = Table(
            table_id=table_id,
            raw_rows=raw_rows,
            caption=caption or None,
            raw_caption=raw_caption,
            provenance=self.provenance(element),
            attributes=table_attributes,
        )
        grid_result = normalize_table(table)
        table.normalized_rows = (
            grid_result.to_text_rows() if grid_result.grid else None
        )
        self.warnings.extend(grid_result.warnings)
        if not raw_rows:
            self.warnings.append(
                ParseWarning(
                    code=MALFORMED_TABLE,
                    message="A table element contained no extractable rows and cells",
                    provenance=table.provenance,
                )
            )
        self.tables.append(table)
        self._append_block(
            ContentBlock(
                block_type=BlockType.TABLE,
                table_id=table_id,
                provenance=table.provenance,
            )
        )

        # Nested tables are separate evidence objects with their own DOM paths.
        for nested in element.iterdescendants():
            if _local_name(nested) == "table" and _nearest_ancestor_table(nested) is element:
                parent_cell = nested.getparent()
                while parent_cell is not None and _local_name(parent_cell) not in {
                    "td",
                    "th",
                    "te",
                    "tu",
                }:
                    if parent_cell is element:
                        parent_cell = None
                        break
                    parent_cell = parent_cell.getparent()
                self._emit_table(
                    nested,
                    parent_table_id=table_id,
                    parent_cell_path=(
                        self.tree.getpath(parent_cell)
                        if parent_cell is not None
                        else None
                    ),
                )

    def walk(self, element: etree._Element) -> None:
        if not isinstance(element.tag, str) or _is_hidden(element):
            return
        tag = _local_name(element)
        if tag in _METADATA_ONLY_TAGS:
            return
        if tag == "table":
            self._emit_table(element)
            return
        if tag in {"pgbrk", "page-break", "pagebreak"}:
            self._append_block(
                ContentBlock(
                    block_type=BlockType.PAGE_BREAK,
                    provenance=self.provenance(element),
                )
            )
            return
        if tag in _LIST_TAGS:
            self._emit_list(element)
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or (
            tag in _HEADING_TAGS and tag != "title"
        ):
            self._emit_heading(element)
            return
        if tag == "title" and self.markup_kind == "xml":
            self._emit_heading(element)
            return
        if tag in _PARAGRAPH_TAGS:
            self._walk_mixed(element)
            return
        if tag in _TEXT_CONTAINER_TAGS or tag in _INLINE_TAGS:
            self._walk_mixed(element)
            return
        self._walk_mixed(element)

    def extract(self, body: etree._Element) -> DocumentContent:
        self.walk(body)
        if self._current_section is not None:
            self._current_section.end_block_index = len(self.blocks) - 1

        raw_title, title_provenance = self._extract_title()
        title = normalize_text(raw_title) if raw_title else None
        if not title:
            self.warnings.append(
                ParseWarning(
                    code=TITLE_NOT_FOUND,
                    message="No source title was found; no title was invented",
                    provenance=Provenance(
                        source_file=self.context.relative_path,
                        file_role=self.context.role,
                    ),
                )
            )

        content = DocumentContent(
            title=title or None,
            raw_title=raw_title,
            sections=self.sections,
            tables=self.tables,
            block_sequence=self.blocks,
            title_provenance=title_provenance,
            provenance=Provenance(
                source_file=self.context.relative_path,
                file_role=self.context.role,
                dom_path=self.tree.getpath(body),
                tag=_local_name(body),
            ),
            attributes={"markup_kind": self.markup_kind},
        )
        content.derive_body_text()
        if not content.body_text.strip() and not any(
            _table_has_source_text(table) for table in content.tables
        ):
            self.warnings.append(
                ParseWarning(
                    code=EMPTY_BODY,
                    message="No body text or table cells were extracted",
                    provenance=content.provenance,
                )
            )
        return content

    def _extract_title(self) -> tuple[str | None, Provenance | None]:
        preferred = (
            {"document-name", "document_name"}
            if self.markup_kind == "xml"
            else {"title"}
        )
        for element in self.root.iter():
            if _local_name(element) in preferred and not _is_effectively_hidden(element):
                raw = _raw_text(element)
                if normalize_text(raw):
                    return raw, self.provenance(element)
        first_heading = next(
            (block for block in self.blocks if str(block.block_type) == "heading"),
            None,
        )
        if first_heading is not None:
            return first_heading.raw_text, first_heading.provenance
        return None, None

    def source_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {"root_tag": _local_name(self.root)}
        selected_tags = {
            "blockquote",
            "div",
            "iframe",
            "p",
            "pre",
            "script",
            "section",
            "span",
            "table",
        }
        tag_counts: Counter[str] = Counter(
            tag
            for element in self.root.iter()
            if isinstance(element.tag, str)
            for tag in [_local_name(element)]
            if tag in selected_tags
        )
        metadata["selected_tag_counts"] = dict(sorted(tag_counts.items()))
        wanted = {tag: key for key, tags in _SOURCE_METADATA_TAGS.items() for tag in tags}
        for element in self.root.iter():
            key = wanted.get(_local_name(element))
            if key is None or key in metadata or _is_effectively_hidden(element):
                continue
            raw = _raw_text(element)
            value = normalize_text(raw)
            if value:
                metadata[key] = value
                metadata[f"{key}_raw"] = raw
                metadata[f"{key}_provenance"] = self.provenance(element).to_dict()
        return metadata


class MarkupParser(BaseParser):
    """Base implementation for XML or HTML trees."""

    markup_kind = "xml"
    parser_name = "lxml-markup"
    _last_bare_ampersand_count = 0

    def _parse_root(
        self, text: str
    ) -> tuple[etree._Element, list[etree._LogEntry]]:
        if self.markup_kind == "html":
            parser = lxml_html.HTMLParser(
                recover=True,
                no_network=True,
                huge_tree=True,
                remove_comments=False,
            )
            root = lxml_html.document_fromstring(text, parser=parser)
            return root, list(parser.error_log)

        prepared = _XML_ENCODING_RE.sub(
            lambda match: f'{match.group(1)}"utf-8"', text, count=1
        )
        prepared, self._last_bare_ampersand_count = _escape_xml_bare_ampersands(
            prepared
        )
        parser = etree.XMLParser(
            recover=True,
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=True,
            remove_comments=False,
        )
        root = etree.fromstring(prepared.encode("utf-8"), parser=parser)
        if root is None:
            raise etree.XMLSyntaxError("No XML root element", 0, 0, 0)
        return root, list(parser.error_log)

    def _find_body(self, root: etree._Element) -> etree._Element:
        for element in root.iter():
            if _local_name(element) == "body":
                return element
        return root

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
        if not decoded.succeeded:
            return ParseResult(
                source_file=source_file,
                parse_status=ParseStatus.FAILED,
                warnings=decoded.warnings,
                parser_name=self.parser_name,
            )

        warnings = list(decoded.warnings)
        try:
            root, error_log = self._parse_root(decoded.text or "")
        except (etree.LxmlError, ValueError, TypeError) as exc:
            warnings.append(
                ParseWarning(
                    code=MARKUP_PARSE_FAILED,
                    message=f"{self.markup_kind.upper()} parsing failed: {exc}",
                    severity=WarningSeverity.ERROR,
                    provenance=Provenance(
                        source_file=context.relative_path,
                        file_role=context.role,
                    ),
                    details={"exception_type": type(exc).__name__},
                )
            )
            return ParseResult(
                source_file=source_file,
                parse_status=ParseStatus.FAILED,
                warnings=warnings,
                parser_name=self.parser_name,
            )

        if self.markup_kind == "xml" and self._last_bare_ampersand_count:
            warnings.append(
                ParseWarning(
                    code="BARE_AMPERSAND_PRESERVED",
                    message=(
                        "Bare ampersands were escaped only in the parser input "
                        "so their literal '&' characters remain in extracted text"
                    ),
                    severity=WarningSeverity.INFO,
                    provenance=Provenance(
                        source_file=context.relative_path,
                        file_role=context.role,
                    ),
                    details={
                        "replacement_count": self._last_bare_ampersand_count
                    },
                )
            )

        significant_errors = [
            entry
            for entry in error_log
            if str(entry.level_name).upper() in {"ERROR", "FATAL"}
        ]
        if significant_errors:
            warnings.append(
                ParseWarning(
                    code=MARKUP_RECOVERY_APPLIED,
                    message=(
                        f"The tolerant {self.markup_kind.upper()} parser recovered "
                        f"from {len(significant_errors)} syntax issue(s)"
                    ),
                    provenance=Provenance(
                        source_file=context.relative_path,
                        file_role=context.role,
                    ),
                    details={
                        "error_count": len(significant_errors),
                        "examples": [
                            {
                                "line": entry.line,
                                "column": entry.column,
                                "message": str(entry.message),
                            }
                            for entry in significant_errors[:10]
                        ],
                    },
                )
            )

        extractor = _Extractor(
            root,
            context,
            markup_kind=self.markup_kind,
        )
        content = extractor.extract(self._find_body(root))
        warnings.extend(extractor.warnings)
        source_metadata = extractor.source_metadata()
        source_metadata["syntax_issue_count"] = len(significant_errors)

        partial_codes = {
            EMPTY_BODY,
            MALFORMED_TABLE,
            MARKUP_RECOVERY_APPLIED,
            "TABLE_SPAN_NORMALIZATION_FAILED",
        }
        status = (
            ParseStatus.PARTIAL
            if any(warning.code in partial_codes for warning in warnings)
            else ParseStatus.SUCCESS
        )
        return ParseResult(
            source_file=source_file,
            parse_status=status,
            content=content,
            warnings=warnings,
            parser_name=self.parser_name,
            source_metadata=source_metadata,
        )


__all__ = [
    "EMPTY_BODY",
    "MALFORMED_TABLE",
    "MARKUP_PARSE_FAILED",
    "MARKUP_RECOVERY_APPLIED",
    "MarkupParser",
    "SECTION_HIERARCHY_UNCERTAIN",
    "TITLE_NOT_FOUND",
]
