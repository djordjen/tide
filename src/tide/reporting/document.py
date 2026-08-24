"""Renderer-neutral report document values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

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
class ReportGroup:
    """One contiguous slice of the detail rows, named and subtotaled.

    Presentation structure over the flat table rather than a second copy of
    the rows: CSV stays spreadsheet-flat by reading `detail` alone, and a
    renderer that understands groups slices the same rows everybody else
    formats, so the two can never disagree about what is in the report.
    """

    values: tuple[ReportValue, ...]
    row_start: int
    row_count: int
    footer_values: tuple[ReportValue, ...]


@dataclass(frozen=True, slots=True)
class TypedReport:
    """One document beside the values a typed format needs.

    `ReportCell` carries text and only text, deliberately: it is mirrored to
    the wire by `TideReportDocument`, which forbids extras, so a cell cannot
    grow a second field without breaking every report at once. A workbook
    still has to know that a Total is a number, so the values travel beside
    the document instead of inside it -- server-side only, positional over
    `document.detail.rows`.

    An entry is `None` where the text is already the whole truth: a reference
    names a record and a choice is captioned, and sending what either one
    stores would put an identity where a name belongs.
    """

    document: "ReportDocument"
    typed_values: tuple[tuple[Any, ...], ...] = ()


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
    groups: tuple[ReportGroup, ...] = ()

    def plain_text(self) -> str:
        """Return a compact accessible representation for terminals and tests."""

        lines = [self.title, self.application, *self.header_text]
        lines.extend(f"{value.label}: {value.text}" for value in self.record_values)
        lines.append(" | ".join(column.label for column in self.detail.columns))
        if self.groups:
            for group in self.groups:
                lines.extend(
                    f"{value.label}: {value.text}" for value in group.values
                )
                lines.extend(
                    " | ".join(cell.text for cell in row)
                    for row in self.detail.rows[
                        group.row_start : group.row_start + group.row_count
                    ]
                )
                lines.extend(
                    f"{value.label}: {value.text}" for value in group.footer_values
                )
        else:
            lines.extend(
                " | ".join(cell.text for cell in row) for row in self.detail.rows
            )
        lines.extend(f"{value.label}: {value.text}" for value in self.footer_values)
        return "\n".join(lines)
