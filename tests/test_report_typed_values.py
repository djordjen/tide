"""The values a report carries beside its text, for a format that has types.

A report already knows what every cell says. A workbook needs to know what a
cell *is* -- otherwise a Total column arrives as text and the one reason to
prefer XLSX over CSV is gone.

Which cells are typed is decided by the column, not by the value. A reference
stores an identity whose text is a customer's name, and a choice stores a code
whose text is its caption; sending either as a value would put the stored
thing where the reader expects the shown thing. Aggregates are the exception,
and safely so: `_initial_aggregates` seeds every one as `0` or `Decimal(0)`,
so a report aggregate is always a number.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tide import compile_project
from tide.data import InMemoryRepository
from tide.reporting import ReportService
from tide.runtime import Channel, Principal, RequestContext
from tide.services import RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


@pytest.fixture
def reporting() -> tuple[ReportService, RequestContext]:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    service = ReportService(model, RecordsService(model, repository))
    context = RequestContext(
        Principal("report:user", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )
    return service, context


def test_the_typed_table_lines_up_with_the_document_it_came_with(
    reporting: tuple[ReportService, RequestContext],
) -> None:
    service, context = reporting

    built = service.build_export("sales.summary", {}, context)

    assert built.document is not None
    assert len(built.typed_values) == len(built.document.detail.rows)
    for row, typed in zip(built.document.detail.rows, built.typed_values):
        assert len(typed) == len(row)


def test_a_grouped_listing_types_its_numbers_and_dates(
    reporting: tuple[ReportService, RequestContext],
) -> None:
    service, context = reporting

    built = service.build_export("sales.summary", {}, context)
    columns = [column.name for column in built.document.detail.columns]

    total_at = columns.index("total")
    date_at = columns.index("invoice_date")
    number_at = columns.index("number")

    first = built.typed_values[0]
    assert isinstance(first[total_at], Decimal)
    assert isinstance(first[date_at], date)
    # A string column has nothing to type; its text is already the value.
    assert first[number_at] is None


def test_a_record_report_types_its_line_columns(
    reporting: tuple[ReportService, RequestContext],
) -> None:
    service, context = reporting

    built = service.build_export_for_record("sales.invoice", 1, context)
    columns = [column.name for column in built.document.detail.columns]
    assert built.document.detail.rows, "the fixture invoice must have lines"

    for name in ("quantity", "unit_price", "line_total"):
        if name in columns:
            position = columns.index(name)
            assert built.typed_values[0][position] is not None, name

    # A reference on a line keeps the name the reader saw.
    if "product" in columns:
        position = columns.index("product")
        assert built.typed_values[0][position] is None
        assert built.document.detail.rows[0][position].text


def test_the_plain_build_still_returns_just_the_document(
    reporting: tuple[ReportService, RequestContext],
) -> None:
    """Every existing caller keeps the shape it had."""

    service, context = reporting

    document = service.build("sales.summary", {}, context)
    built = service.build_export("sales.summary", {}, context)

    assert document.detail.columns == built.document.detail.columns
    assert [
        [cell.text for cell in row] for row in document.detail.rows
    ] == [[cell.text for cell in row] for row in built.document.detail.rows]


def test_a_summary_without_columns_types_its_aggregate_cells(
    reporting: tuple[ReportService, RequestContext],
) -> None:
    """An ungrouped summary is aggregates all the way across.

    Those are the numbers somebody opened a spreadsheet for, so they must not
    arrive as `4,610.00`.
    """

    from dataclasses import replace

    service, context = reporting
    report = dict(service.model.reports["sales.summary"])
    report.pop("columns")
    model = replace(
        service.model,
        reports={**service.model.reports, "sales.rollup": report},
    )
    rolled = ReportService(model, service.records)

    built = rolled.build_export("sales.rollup", {}, context)
    columns = [column.name for column in built.document.detail.columns]

    # The group keys are captions; the aggregates are numbers.
    assert built.typed_values[0][columns.index("customer")] is None
    count_at = columns.index("invoice_count")
    total_at = columns.index("sales_total")
    assert built.typed_values[0][count_at] == 3
    assert built.typed_values[0][total_at] == Decimal("4610.00")
