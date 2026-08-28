"""The report document a program reads.

The REST document is preformatted text because renderers draw it. On this
wire a Decimal is a JSON string, so text alone cannot say what is a number:
each cell pairs the display text with the exact typed value where one
exists, and each column reads its type back off the typed table -- the
column decision the service already made, not a guess from data.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from tide.mcp.runtime import RuntimeMcpReportExposure, mcp_report_document
from tide.reporting import (
    ReportCell,
    ReportColumn,
    ReportDocument,
    ReportGroup,
    ReportTable,
    ReportValue,
    TypedReport,
)

EXPOSURE = RuntimeMcpReportExposure(
    report="sales.summary",
    kind="summary",
    title="Posted Sales Summary",
    entity="sales.Invoice",
    tool="report_sales_summary",
)


def _typed() -> TypedReport:
    document = ReportDocument(
        report="sales.summary",
        title="Posted Sales Summary",
        application="invoicing",
        generated_at=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
        header_text=("From 2026-07-01",),
        record_values=(ReportValue("Customer", "ACME - ACME Ltd"),),
        detail=ReportTable(
            columns=(
                ReportColumn("number", "Number"),
                ReportColumn("invoice_date", "Invoice date"),
                ReportColumn("total", "Total", alignment="right"),
                ReportColumn("posted", "Posted"),
            ),
            rows=(
                (
                    ReportCell("INV-1"),
                    ReportCell("2026-07-02"),
                    ReportCell("1,234.50", alignment="right"),
                    ReportCell("Yes"),
                ),
                (
                    ReportCell("INV-2"),
                    ReportCell("2026-07-03"),
                    ReportCell("10.00", alignment="right"),
                    ReportCell("No"),
                ),
            ),
        ),
        footer_values=(ReportValue("Sales total", "1,244.50"),),
        page_footer_template="",
        suggested_filename="posted-sales.csv",
        groups=(
            ReportGroup(
                values=(ReportValue("Customer", "ACME - ACME Ltd"),),
                row_start=0,
                row_count=2,
                footer_values=(ReportValue("Sales total", "1,244.50"),),
            ),
        ),
    )
    typed = (
        (None, date(2026, 7, 2), Decimal("1234.50"), True),
        (None, date(2026, 7, 3), Decimal("10.00"), False),
    )
    return TypedReport(document, typed)


def test_cells_pair_display_text_with_exact_values() -> None:
    wire = mcp_report_document("invoicing", EXPOSURE, _typed())

    first = wire.rows[0]
    assert first[0].text == "INV-1" and first[0].value is None
    assert first[1].value == date(2026, 7, 2)
    assert first[2].text == "1,234.50"
    assert first[2].value == Decimal("1234.50")
    assert first[3].value is True


def test_a_column_is_typed_by_its_values_and_bool_is_not_integer() -> None:
    wire = mcp_report_document("invoicing", EXPOSURE, _typed())

    assert [column.type for column in wire.columns] == [
        "text",
        "date",
        "decimal",
        "boolean",
    ]
    assert [column.name for column in wire.columns] == [
        "number",
        "invoice_date",
        "total",
        "posted",
    ]


def test_a_column_with_no_typed_values_reads_text() -> None:
    """Absence is reported as absence: a column whose document carries no
    typed value offers nothing to compute with, whatever the model calls it.
    """

    typed = _typed()
    emptied = TypedReport(
        typed.document,
        tuple((None,) * len(row) for row in typed.typed_values),
    )

    wire = mcp_report_document("invoicing", EXPOSURE, emptied)

    assert {column.type for column in wire.columns} == {"text"}


def test_groups_keep_their_spans_and_subtotals() -> None:
    wire = mcp_report_document("invoicing", EXPOSURE, _typed())

    assert len(wire.groups) == 1
    group = wire.groups[0]
    assert group.row_start == 0 and group.row_count == 2
    assert group.values[0].label == "Customer"
    assert group.footer_values[0].text == "1,244.50"


def test_presentation_stays_behind_and_identity_travels() -> None:
    wire = mcp_report_document("invoicing", EXPOSURE, _typed())

    assert wire.application == "invoicing"
    assert wire.report == "sales.summary"
    assert wire.kind == "summary"
    assert wire.entity == "sales.Invoice"
    assert wire.generated_at == datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    assert wire.header_text == ("From 2026-07-01",)
    assert wire.footer_values[0].label == "Sales total"
    dumped = wire.model_dump()
    assert "page_footer_template" not in dumped
    assert "suggested_filename" not in dumped
    assert "alignment" not in dumped["columns"][0]
