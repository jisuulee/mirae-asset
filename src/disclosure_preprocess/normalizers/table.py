"""Span-aware table grid normalization with explicit failure reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from disclosure_preprocess.models._serialization import JsonSerializable
from disclosure_preprocess.models.document import ParseWarning, WarningSeverity
from disclosure_preprocess.models.provenance import Provenance
from disclosure_preprocess.models.table import Table, TableCell

from .text import normalize_text


TABLE_SPAN_NORMALIZATION_FAILED = "TABLE_SPAN_NORMALIZATION_FAILED"


@dataclass(slots=True)
class TableGridCell(JsonSerializable):
    """One normalized grid slot linked back to its original source cell."""

    raw_text: str
    text: str
    origin_row: int
    origin_column: int
    source_row_index: int | None
    source_cell_index: int | None
    rowspan: int
    colspan: int
    is_span_continuation: bool
    is_header: bool
    provenance: Provenance | None = None


@dataclass(slots=True)
class TableGridResult(JsonSerializable):
    success: bool
    grid: list[list[TableGridCell | None]] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)

    def to_text_rows(self, *, repeat_spans: bool = False) -> list[list[str | None]]:
        """Return a rectangular string view without silently duplicating spans.

        By default, continuation slots are ``None``.  Callers may explicitly
        request repeated values; each grid slot still retains its origin and
        continuation flag in ``grid``.
        """

        rows: list[list[str | None]] = []
        for row in self.grid:
            rows.append(
                [
                    None
                    if slot is None
                    or (slot.is_span_continuation and not repeat_spans)
                    else slot.text
                    for slot in row
                ]
            )
        return rows


def _span_value(
    value: object,
    *,
    name: str,
    row_index: int,
    cell_index: int,
    warnings: list[ParseWarning],
    provenance: Provenance | None,
    max_span: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > max_span
    ):
        warnings.append(
            ParseWarning(
                code=TABLE_SPAN_NORMALIZATION_FAILED,
                message=f"Invalid {name}; used 1 for grid recovery",
                severity=WarningSeverity.WARNING,
                provenance=provenance,
                details={
                    "row_index": row_index,
                    "cell_index": cell_index,
                    "span_name": name,
                    "span_value": value,
                    "recovery_value": 1,
                    "maximum_span": max_span,
                },
            )
        )
        return 1
    return value


def _ensure_grid_size(
    grid: list[list[TableGridCell | None]], row_count: int, column_count: int
) -> None:
    while len(grid) < row_count:
        grid.append([])
    for row in grid:
        if len(row) < column_count:
            row.extend([None] * (column_count - len(row)))


def _rectangle_is_free(
    grid: list[list[TableGridCell | None]],
    row: int,
    column: int,
    rowspan: int,
    colspan: int,
) -> bool:
    _ensure_grid_size(grid, row + rowspan, column + colspan)
    return all(
        grid[target_row][target_column] is None
        for target_row in range(row, row + rowspan)
        for target_column in range(column, column + colspan)
    )


def expand_table_grid(
    rows: Iterable[Iterable[TableCell]],
    *,
    normalize_cell_text: bool = True,
    max_span: int = 1_000,
    max_slots_per_cell: int = 100_000,
    max_total_slots: int = 2_000_000,
) -> TableGridResult:
    """Expand row/column spans while retaining origin and source provenance."""

    source_rows = [list(row) for row in rows]
    grid: list[list[TableGridCell | None]] = []
    grid_width = 0
    warnings: list[ParseWarning] = []

    for row_index, source_row in enumerate(source_rows):
        _ensure_grid_size(grid, row_index + 1, 0)
        cursor = 0
        for cell_index, cell in enumerate(source_row):
            rowspan = _span_value(
                cell.rowspan,
                name="rowspan",
                row_index=row_index,
                cell_index=cell_index,
                warnings=warnings,
                provenance=cell.provenance,
                max_span=max_span,
            )
            colspan = _span_value(
                cell.colspan,
                name="colspan",
                row_index=row_index,
                cell_index=cell_index,
                warnings=warnings,
                provenance=cell.provenance,
                max_span=max_span,
            )

            if rowspan * colspan > max_slots_per_cell:
                warnings.append(
                    ParseWarning(
                        code=TABLE_SPAN_NORMALIZATION_FAILED,
                        message="Combined spans exceed the safe grid expansion limit; used 1x1 for recovery",
                        provenance=cell.provenance,
                        details={
                            "row_index": row_index,
                            "cell_index": cell_index,
                            "rowspan": rowspan,
                            "colspan": colspan,
                            "maximum_slots_per_cell": max_slots_per_cell,
                            "recovery_rowspan": 1,
                            "recovery_colspan": 1,
                        },
                    )
                )
                rowspan = 1
                colspan = 1

            while True:
                projected_rows = max(len(grid), row_index + rowspan)
                projected_columns = max(grid_width, cursor + colspan)
                if projected_rows * projected_columns > max_total_slots:
                    warnings.append(
                        ParseWarning(
                            code=TABLE_SPAN_NORMALIZATION_FAILED,
                            message=(
                                "Table-wide grid expansion exceeds the safe slot "
                                "limit; raw rows were retained without a normalized grid"
                            ),
                            provenance=cell.provenance,
                            details={
                                "row_index": row_index,
                                "cell_index": cell_index,
                                "projected_rows": projected_rows,
                                "projected_columns": projected_columns,
                                "maximum_total_slots": max_total_slots,
                            },
                        )
                    )
                    return TableGridResult(success=False, grid=[], warnings=warnings)
                if _rectangle_is_free(
                    grid, row_index, cursor, rowspan, colspan
                ):
                    grid_width = projected_columns
                    break
                cursor += 1

            normalized = (
                normalize_text(cell.raw_text)
                if normalize_cell_text and cell.text is None
                else cell.text
                if cell.text is not None
                else cell.raw_text
            )
            for row_offset in range(rowspan):
                for column_offset in range(colspan):
                    grid[row_index + row_offset][cursor + column_offset] = (
                        TableGridCell(
                            raw_text=cell.raw_text,
                            text=normalized,
                            origin_row=row_index,
                            origin_column=cursor,
                            source_row_index=cell.source_row_index,
                            source_cell_index=cell.source_cell_index,
                            rowspan=rowspan,
                            colspan=colspan,
                            is_span_continuation=(
                                row_offset != 0 or column_offset != 0
                            ),
                            is_header=cell.is_header,
                            provenance=cell.provenance,
                        )
                    )
            cursor += colspan

    if len(grid) > len(source_rows):
        warnings.append(
            ParseWarning(
                code=TABLE_SPAN_NORMALIZATION_FAILED,
                message="A rowspan extends beyond the source table row count",
                details={
                    "source_row_count": len(source_rows),
                    "expanded_row_count": len(grid),
                },
            )
        )

    _ensure_grid_size(grid, len(grid), grid_width)
    return TableGridResult(success=not warnings, grid=grid, warnings=warnings)


def normalize_table(
    table: Table,
    *,
    normalize_cell_text: bool = True,
    max_span: int = 1_000,
    max_slots_per_cell: int = 100_000,
    max_total_slots: int = 2_000_000,
) -> TableGridResult:
    """Normalize a table without mutating its raw rows or evidence strings."""

    return expand_table_grid(
        table.raw_rows,
        normalize_cell_text=normalize_cell_text,
        max_span=max_span,
        max_slots_per_cell=max_slots_per_cell,
        max_total_slots=max_total_slots,
    )


__all__ = [
    "TABLE_SPAN_NORMALIZATION_FAILED",
    "TableGridCell",
    "TableGridResult",
    "expand_table_grid",
    "normalize_table",
]
