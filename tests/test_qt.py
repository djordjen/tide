from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tide import compile_project
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.qt import QtBrowseController


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


class _BrowseClient:
    def __init__(self) -> None:
        self.list_cursors: list[str | None] = []
        self.reference_reads = 0

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
                        "number": "INV-2026-001",
                        "invoice_date": date(2026, 7, 1),
                        "customer": 1,
                        "status": "draft",
                        "total": Decimal("1000.00"),
                    },
                    {
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


def _session(
    model: Any,
    *,
    entity_names: tuple[str, ...] | None = None,
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
                operations=("list", "get"),
                readable_fields=tuple(model.entity(name).fields),
            )
            for name in accessible
        },
    )
