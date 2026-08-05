"""Column geometry shared by every table this renderer puts on screen."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtWidgets import (
    QHeaderView,
    QTableView,
    QTableWidget,
)

from .presenter import (
    QtBrowseColumn,
    QtBrowseController,
)


def qt_alignment(value: str) -> Any:
    horizontal = {
        "left": Qt.AlignmentFlag.AlignLeft,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
    }[value]
    return horizontal | Qt.AlignmentFlag.AlignVCenter


def configure_interactive_header(table: QTableView | QTableWidget) -> None:
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setMinimumSectionSize(56)
    header.setStretchLastSection(False)


def fit_interactive_columns(
    table: QTableView | QTableWidget,
    columns: tuple[QtBrowseColumn, ...],
) -> None:
    fit_content_columns(table)
    column_count = _table_column_count(table)

    left_aligned = tuple(
        index for index, column in enumerate(columns) if column.alignment == "left"
    )
    if not left_aligned:
        return
    flexible = max(left_aligned, key=table.columnWidth)
    used = sum(table.columnWidth(index) for index in range(column_count))
    available = table.viewport().width()
    extra = max(available - used - 2, 0)
    flexible_limit = max(280, int(available * 0.55))
    table.setColumnWidth(
        flexible,
        min(table.columnWidth(flexible) + extra, flexible_limit),
    )


def fit_content_columns(table: QTableView | QTableWidget) -> None:
    table.resizeColumnsToContents()
    for index in range(_table_column_count(table)):
        fitted = table.columnWidth(index)
        table.setColumnWidth(index, min(max(fitted, 72), 360))


def _table_column_count(table: QTableView | QTableWidget) -> int:
    model = table.model()
    return 0 if model is None else model.columnCount()


def column_layout_key(controller: QtBrowseController) -> str:
    parts = (
        controller.model.name,
        controller.view.name,
        controller.session.principal,
    )
    encoded = "/".join(quote(part, safe="") for part in parts)
    return f"browse-column-layouts/{encoded}"


def known_column_order(
    configured: list[Any],
    current: tuple[str, ...],
) -> tuple[str, ...]:
    known: list[str] = []
    for name in configured:
        if isinstance(name, str) and name in current and name not in known:
            known.append(name)
    known.extend(name for name in current if name not in known)
    return tuple(known)


def known_column_widths(
    configured: dict[Any, Any],
    current: tuple[str, ...],
) -> dict[str, int]:
    return {
        name: value
        for name, value in configured.items()
        if name in current and isinstance(value, int) and not isinstance(value, bool)
    }


def apply_column_order(
    header: QHeaderView,
    desired: tuple[str, ...],
    current: tuple[str, ...],
) -> None:
    logical_by_name = {name: index for index, name in enumerate(current)}
    for target_visual, field_name in enumerate(desired):
        logical_index = logical_by_name[field_name]
        current_visual = header.visualIndex(logical_index)
        if current_visual != target_visual:
            header.moveSection(current_visual, target_visual)
