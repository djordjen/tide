"""Taking the browse query you are looking at away as a file.

A reader who has filtered, sorted and totalled a grid needs the result
somewhere a spreadsheet can reach it. Export is bounded, says when it was
bounded, and is gated by a declared capability -- not because paging could not
reach the same rows, but because a deployment should be able to say "reads on
screen, does not take the file away", and because an export is worth finding
in a log a year later.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterator

import pytest

from tide import compile_project
from tide.data import FilterCondition, InMemoryRepository, SortField
from tide.model.source import FRAMEWORK_PERMISSIONS
from tide.reporting.browse import (
    MAX_EXPORT_ROWS,
    BrowseExportService,
    ExportNotPermitted,
    UnknownBrowseView,
)
from tide.runtime import Channel, Principal, RequestContext
from tide.security import SecurityEngine
from tide.services import RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def context(*roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal(
            "user:clerk", roles=frozenset(roles or ("sales_clerk",))
        ),
        channel=Channel.REST,
    )


def test_export_is_a_declarable_framework_capability() -> None:
    assert "tide.records.export" in FRAMEWORK_PERMISSIONS

    model = compile_project(INVOICING)
    security = SecurityEngine(model)

    # The clerk who reads the grid may take it away.
    assert "tide.records.export" in security.effective_permissions(
        context().principal
    )
    # The administrator role grants administration and nothing else, so it is
    # the proof that the capability is granted rather than ambient.
    assert "tide.records.export" not in security.effective_permissions(
        context("administrator").principal
    )


VIEW = "sales.Invoice.browse"

CUSTOMERS = [
    {
        "id": 1,
        "code": "ACME",
        "name": "ACME Ltd",
        "email": None,
        "active": True,
        "invoices": [],
    },
    {
        "id": 2,
        "code": "MORA",
        "name": "Mora Trade",
        "email": "mora@example.test",
        "active": True,
        "invoices": [],
    },
]

INVOICES = [
    {
        "id": index,
        "number": f"INV-2026-{index:04d}",
        "invoice_date": date(2026, 7, index),
        "currency": "EUR",
        "status": status,
        "posted_at": None,
        "posted_by": None,
        "version": 1,
        "customer": customer,
        "total": Decimal(f"{index}0.50"),
        "lines": [],
    }
    for index, (status, customer) in enumerate(
        [("draft", 1), ("draft", 2), ("posted", 1), ("cancelled", 2)],
        start=1,
    )
]


@pytest.fixture
def exporting() -> Iterator[BrowseExportService]:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    repository.seed("crm.Customer", CUSTOMERS)
    repository.seed("sales.Invoice", INVOICES)
    yield BrowseExportService(model, RecordsService(model, repository))


def test_an_export_carries_every_row_the_query_admits(
    exporting: BrowseExportService,
) -> None:
    export = exporting.build(VIEW, (), (SortField("id"),), context())

    assert export.rows == len(INVOICES)
    assert export.total == len(INVOICES)
    assert export.truncated is False
    assert len(export.document.detail.rows) == len(INVOICES)
    # The view's declared column order, not a reader's arrangement.
    assert [column.name for column in export.document.detail.columns] == [
        "number",
        "invoice_date",
        "customer",
        "status",
        "total",
    ]
    assert [column.label for column in export.document.detail.columns] == [
        "Number",
        "Invoice Date",
        "Customer",
        "Status",
        "Total",
    ]


def test_the_footer_carries_the_summaries_the_view_declares(
    exporting: BrowseExportService,
) -> None:
    """The same aggregates the grid's footer band shows, over the same set."""

    export = exporting.build(VIEW, (), (SortField("id"),), context())

    footer = {value.label: value.text for value in export.document.footer_values}
    assert footer == {
        "COUNT Number": "4",
        # 10.50 + 20.50 + 30.50 + 40.50, at the money format's two places.
        "SUM Total": "102.00",
    }


def test_a_filtered_export_states_the_conditions_that_made_it(
    exporting: BrowseExportService,
) -> None:
    export = exporting.build(
        VIEW,
        (FilterCondition("status", "eq", "draft"),),
        (SortField("id"),),
        context(),
    )

    assert export.rows == 2
    assert any(
        "Status is Draft" in line for line in export.document.header_text
    ), export.document.header_text
    assert any(
        "Sorted by" in line for line in export.document.header_text
    ), export.document.header_text


def test_typed_values_sit_beside_the_text_for_the_spreadsheet(
    exporting: BrowseExportService,
) -> None:
    export = exporting.build(VIEW, (), (SortField("id"),), context())
    columns = [column.name for column in export.document.detail.columns]

    # A number reaches the workbook as a number, so a column can be summed.
    assert export.typed_values[0][columns.index("total")] == Decimal("10.50")
    assert export.typed_values[0][columns.index("invoice_date")] == date(2026, 7, 1)

    # A reference is already its display string, and its stored value is an
    # identity nobody wants in a spreadsheet cell.
    customer_at = columns.index("customer")
    assert export.typed_values[0][customer_at] is None
    # Named the way the entity says it names itself, not by its identity.
    assert export.document.detail.rows[0][customer_at].text == "ACME - ACME Ltd"

    # A choice is captioned, not coded.
    status_at = columns.index("status")
    assert export.typed_values[0][status_at] is None
    assert export.document.detail.rows[0][status_at].text == "Draft"


def test_an_export_stops_at_the_cap_and_says_what_it_stopped_at(
    exporting: BrowseExportService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tide.reporting.browse.MAX_EXPORT_ROWS", 2)

    export = exporting.build(VIEW, (), (SortField("id"),), context())

    assert export.rows == 2
    assert export.total == len(INVOICES)
    assert export.truncated is True
    assert any(
        "2 of 4 rows" in line for line in export.document.header_text
    ), export.document.header_text
    # A partial file says so in its name, because CSV has nowhere else to.
    assert export.document.suggested_filename.endswith("-partial")


def test_a_whole_export_does_not_claim_to_be_partial(
    exporting: BrowseExportService,
) -> None:
    export = exporting.build(VIEW, (), (SortField("id"),), context())

    assert "-partial" not in export.document.suggested_filename
    assert any("4 rows" in line for line in export.document.header_text)


def test_export_is_refused_without_the_capability(
    exporting: BrowseExportService,
) -> None:
    assert exporting.can_export(context("auditor")) is False
    with pytest.raises(ExportNotPermitted):
        exporting.build(VIEW, (), (), context("auditor"))


def test_an_unknown_view_is_refused_rather_than_guessed(
    exporting: BrowseExportService,
) -> None:
    with pytest.raises(UnknownBrowseView):
        exporting.build("sales.NotAView", (), (), context())

    # A form is a view, and is still not something to export.
    with pytest.raises(UnknownBrowseView):
        exporting.build("sales.Invoice.edit", (), (), context())


def test_the_cap_is_a_bound_the_module_declares() -> None:
    assert MAX_EXPORT_ROWS == 10_000
