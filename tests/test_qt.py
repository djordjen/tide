from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tide import compile_project
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.qt import QtBrowseController, QtDetailCollection, QtDetailGroup


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


class _BrowseClient:
    def __init__(self) -> None:
        self.list_cursors: list[str | None] = []
        self.reference_reads = 0
        self.invoice_reads = 0

    def list_records(
        self,
        entity_name: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Any:
        assert entity_name == "sales.Invoice"
        assert limit == 2
        self.list_cursors.append(cursor)
        if cursor is None:
            return SimpleNamespace(
                records=(
                    {
                        "id": 1,
                        "number": "INV-2026-001",
                        "invoice_date": date(2026, 7, 1),
                        "customer": 1,
                        "status": "draft",
                        "total": Decimal("1000.00"),
                    },
                    {
                        "id": 2,
                        "number": "INV-2026-002",
                        "invoice_date": date(2026, 7, 2),
                        "customer": 1,
                        "status": "posted",
                        "total": Decimal("10.00"),
                    },
                ),
                next_cursor="invoice-page-2",
            )
        assert cursor == "invoice-page-2"
        return SimpleNamespace(
            records=(
                {
                    "id": 3,
                    "number": "INV-2026-003",
                    "invoice_date": date(2026, 7, 3),
                    "customer": 2,
                    "status": "cancelled",
                    "total": Decimal("100.00"),
                },
            ),
            next_cursor=None,
        )

    def get_record(self, entity_name: str, identity: Any) -> Any:
        if entity_name == "sales.Invoice":
            assert identity == 1
            self.invoice_reads += 1
            return SimpleNamespace(
                values={
                    "id": 1,
                    "number": "INV-2026-001",
                    "invoice_date": date(2026, 7, 1),
                    "currency": "EUR",
                    "status": "draft",
                    "posted_at": None,
                    "posted_by": None,
                    "version": 1,
                    "customer": 1,
                    "lines": [
                        {
                            "line_number": 1,
                            "product": 10,
                            "description": "Consulting day",
                            "quantity": Decimal("2"),
                            "unit_price": Decimal("500.00"),
                            "total": Decimal("1000.00"),
                        }
                    ],
                    "total": Decimal("1000.00"),
                }
            )
        if entity_name == "catalog.Product":
            assert identity == 10
            self.reference_reads += 1
            return SimpleNamespace(
                values={"id": 10, "code": "CONSULT", "name": "Consulting"}
            )
        assert entity_name == "crm.Customer"
        self.reference_reads += 1
        values = {
            1: {"id": 1, "code": "ADRIA", "name": "Adria Consulting"},
            2: {"id": 2, "code": "NORTH", "name": "Northwind"},
        }
        return SimpleNamespace(values=values[identity])


def test_qt_browse_uses_metadata_formatting_references_and_cursor_paging() -> None:
    model = compile_project(INVOICING)
    client = _BrowseClient()
    controller = QtBrowseController(
        model,
        client,
        _session(model),
        page_size=2,
    )

    first = controller.refresh()

    assert controller.view.name == "sales.Invoice.browse"
    assert controller.title == "Invoices"
    assert tuple(column.name for column in first.columns) == (
        "number",
        "invoice_date",
        "customer",
        "status",
        "total",
    )
    assert first.rows == (
        (
            "INV-2026-001",
            "01.07.2026",
            "ADRIA - Adria Consulting",
            "Draft",
            "1,000.00",
        ),
        (
            "INV-2026-002",
            "02.07.2026",
            "ADRIA - Adria Consulting",
            "Posted",
            "10.00",
        ),
    )
    assert first.columns[-1].alignment == "right"
    assert first.identities == (1, 2)
    assert first.page_number == 1
    assert first.previous_available is False
    assert first.next_available is True
    assert client.reference_reads == 1

    second = controller.next_page()
    assert second.rows[0][2] == "NORTH - Northwind"
    assert second.page_number == 2
    assert second.previous_available is True
    assert second.next_available is False

    previous = controller.previous_page()
    assert previous.rows == first.rows
    assert previous.page_number == 1
    assert client.list_cursors == [None, "invoice-page-2", None]


def test_qt_detail_uses_form_groups_and_nested_inline_collection() -> None:
    model = compile_project(INVOICING)
    client = _BrowseClient()
    controller = QtBrowseController(
        model,
        client,
        _session(model),
        page_size=2,
    )
    controller.refresh()

    detail = controller.load_detail(0)

    assert detail.identity == 1
    assert detail.title == "Invoices — INV-2026-001"
    assert tuple(
        section.label
        for section in detail.sections
    ) == ("Invoice", "Lines", "Totals", "Posting")
    invoice = detail.sections[0]
    assert isinstance(invoice, QtDetailGroup)
    invoice_fields = tuple(field for row in invoice.rows for field in row)
    assert tuple(field.name for field in invoice_fields) == (
        "number",
        "invoice_date",
        "status",
        "currency",
        "customer",
    )
    assert tuple(field.value for field in invoice_fields) == (
        "INV-2026-001",
        "01.07.2026",
        "Draft",
        "EUR",
        "ADRIA - Adria Consulting",
    )
    lines = detail.sections[1]
    assert isinstance(lines, QtDetailCollection)
    assert tuple(column.name for column in lines.columns) == (
        "line_number",
        "product",
        "description",
        "quantity",
        "unit_price",
        "total",
    )
    assert lines.rows == (
        (
            "1",
            "CONSULT - Consulting",
            "Consulting day",
            "2",
            "500.00",
            "1,000.00",
        ),
    )
    assert client.invoice_reads == 1
    assert client.reference_reads == 2


def test_qt_browse_rejects_an_inaccessible_or_invalid_configuration() -> None:
    model = compile_project(INVOICING)
    client = _BrowseClient()
    session = _session(model, entity_names=("sales.Invoice",))

    with pytest.raises(
        ValueError,
        match="Qt browse view 'catalog.Product.browse' is not accessible",
    ):
        QtBrowseController(
            model,
            client,
            session,
            view_name="catalog.Product.browse",
        )

    with pytest.raises(
        ValueError,
        match="Qt browse page size must be between 1 and 500",
    ):
        QtBrowseController(model, client, session, page_size=0)


def test_qt_detail_requires_get_capability() -> None:
    model = compile_project(INVOICING)
    client = _BrowseClient()
    session = _session(
        model,
        entity_names=("sales.Invoice",),
        operations=("list",),
    )
    controller = QtBrowseController(model, client, session, page_size=2)
    controller.refresh()

    assert controller.detail_available is False
    with pytest.raises(
        ValueError,
        match="sales.Invoice does not define an accessible form view",
    ):
        controller.load_detail(0)


def _session(
    model: Any,
    *,
    entity_names: tuple[str, ...] | None = None,
    operations: tuple[str, ...] = ("list", "get"),
) -> TideSessionInfo:
    accessible = entity_names or tuple(model.entities)
    return TideSessionInfo(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        authentication="development",
        principal="qt:tester",
        roles=("sales_clerk",),
        entities={
            name: TideEntityCapabilities(
                operations=operations,
                readable_fields=tuple(model.entity(name).fields),
            )
            for name in accessible
        },
    )
