"""Writing a browse export as a workbook.

Two sheets, because they answer different questions. `Records` is the table
alone, so its first row is a header a spreadsheet can turn into a filter.
`Export details` is where the file says what it is -- which conditions made it,
how it was sorted, and whether it is all of them.

Numbers arrive as numbers. That is the only reason to prefer this over CSV.

It is *not* automatically safer than CSV about formulas, which was the
assumption this module started from and a test disproved: openpyxl infers a
cell's type from what it is handed, and infers `formula` for anything starting
with `=`. A workbook is safe once that guess is overruled, and better than
CSV's apostrophe when it is, because the value survives intact rather than
carrying an escape into whatever reads it back.

Optional, like PDF. A server without the `spreadsheet` extra says so rather
than failing on an import nobody asked for, and the presentation manifest does
not offer the format at all -- so a grid can never present a download the
server would refuse.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from .browse import BrowseExport

try:  # pragma: no cover - the absent branch runs only without the extra
    from openpyxl import Workbook

    SPREADSHEET_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    Workbook = None  # type: ignore[assignment,misc]
    SPREADSHEET_AVAILABLE = False

RECORDS_SHEET = "Records"
DETAILS_SHEET = "Export details"


def render_xlsx(export: BrowseExport) -> bytes:
    """Render one export as workbook bytes."""

    if not SPREADSHEET_AVAILABLE:
        raise RuntimeError(
            "XLSX export needs the 'spreadsheet' extra: "
            "pip install tide-framework[spreadsheet]"
        )
    book = Workbook()
    sheet = book.active
    sheet.title = RECORDS_SHEET
    for position, column in enumerate(export.document.detail.columns, start=1):
        _text(sheet.cell(row=1, column=position), column.label)
    for index, row in enumerate(export.document.detail.rows):
        typed = export.typed_values[index]
        for position, cell in enumerate(row, start=1):
            target = sheet.cell(row=index + 2, column=position)
            value = typed[position - 1]
            if value is None:
                _text(target, cell.text)
            elif isinstance(value, datetime):
                # A workbook has no timezone to keep, and openpyxl refuses an
                # aware value rather than quietly dropping the offset.
                target.value = value.replace(tzinfo=None)
            else:
                target.value = value
    details = book.create_sheet(DETAILS_SHEET)
    for index, line in enumerate(export.document.header_text, start=1):
        _text(details.cell(row=index, column=1), line)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def write_xlsx(export: BrowseExport, path: str | Path) -> Path:
    """Write the workbook to a file and return its path."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(render_xlsx(export))
    return output


def _text(cell: Any, value: str) -> None:
    """Write one string, as a string, whatever it starts with.

    openpyxl infers a cell's type from what it is given, and infers `formula`
    for anything starting with `=`. So a workbook is *not* automatically safe
    from application data that looks like a formula -- the guess has to be
    overruled, or a stored `=SUM(A1:A9)` becomes something Excel evaluates.

    Overruling it rather than escaping it is what keeps the value intact: the
    apostrophe CSV needs would still be there when the cell was read back.
    """

    cell.value = value
    cell.data_type = "s"
