"""Renderer-neutral report document values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Alignment = Literal["left", "center", "right"]


@dataclass(frozen=True, slots=True)
class ReportValue:
    label: str
    text: str
    alignment: Alignment = "left"


@dataclass(frozen=True, slots=True)
class ReportColumn:
    name: str
    label: str
    alignment: Alignment = "left"


@dataclass(frozen=True, slots=True)
class ReportCell:
    text: str
    alignment: Alignment = "left"


@dataclass(frozen=True, slots=True)
class ReportTable:
    columns: tuple[ReportColumn, ...]
    rows: tuple[tuple[ReportCell, ...], ...]


@dataclass(frozen=True, slots=True)
class ReportDocument:
    report: str
    title: str
    application: str
    generated_at: datetime
    header_text: tuple[str, ...]
    record_values: tuple[ReportValue, ...]
    detail: ReportTable
    footer_values: tuple[ReportValue, ...]
    page_footer_template: str
    suggested_filename: str

    def plain_text(self) -> str:
        """Return a compact accessible representation for terminals and tests."""

        lines = [self.title, self.application, *self.header_text]
        lines.extend(f"{value.label}: {value.text}" for value in self.record_values)
        lines.append(" | ".join(column.label for column in self.detail.columns))
        lines.extend(
            " | ".join(cell.text for cell in row) for row in self.detail.rows
        )
        lines.extend(f"{value.label}: {value.text}" for value in self.footer_values)
        return "\n".join(lines)
