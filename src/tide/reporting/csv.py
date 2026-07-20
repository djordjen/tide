"""Controlled CSV rendering for renderer-neutral report tables."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
import re

from .document import ReportDocument

_PLAIN_NUMBER = re.compile(r"[+-]?\d+(?:,\d{3})*(?:\.\d+)?")


def render_csv(document: ReportDocument) -> str:
    """Render the report detail table as RFC-style CSV text.

    Formula-looking text is prefixed with an apostrophe so opening an export in
    a spreadsheet cannot turn application data into an executable formula.
    """

    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(column.label for column in document.detail.columns)
    for row in document.detail.rows:
        writer.writerow(_safe_spreadsheet_cell(cell.text) for cell in row)
    return output.getvalue()


def write_csv(document: ReportDocument, path: str | Path) -> Path:
    """Write an Excel-friendly UTF-8 CSV file and return its path."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_csv(document), encoding="utf-8-sig", newline="")
    return output


def _safe_spreadsheet_cell(value: str) -> str:
    stripped = value.lstrip()
    if not stripped or stripped[0] not in "=+-@":
        return value
    if _PLAIN_NUMBER.fullmatch(stripped):
        return value
    return f"'{value}"
