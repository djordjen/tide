from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tide import compile_project
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.data import FilterCondition, QuerySpec, SortField
from tide.qt import (
    QtBrowseController,
    QtBrowseQuery,
    QtDetailCollection,
    QtDetailGroup,
)


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


class _BrowseClient:
    def __init__(self) -> None:
        self.queries: list[QuerySpec] = []
        self.reference_reads = 0
        self.invoice_reads = 0

    def query_records(
        self,
        entity_name: str,
        query: QuerySpec,
    ) -> Any:
        assert entity_name == "sales.Invoice"
        assert query.limit == 2
        self.queries.append(query)
        if query.cursor is None:
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
        assert query.cursor == "invoice-page-2"
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


class _ProductClient:
    def __init__(self) -> None:
        self.product = {
            "id": 10,
            "code": "CONSULT",
            "name": "Consulting",
            "unit_price": Decimal("500.00"),
            "active": True,
        }
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[Any, dict[str, Any], Any]] = []

    def query_records(self, entity_name: str, query: QuerySpec) -> Any:
        assert entity_name == "catalog.Product"
        return SimpleNamespace(records=(self.product,), next_cursor=None)

    def get_record(self, entity_name: str, identity: Any) -> Any:
        assert entity_name == "catalog.Product"
        assert identity == 10
        return SimpleNamespace(values=dict(self.product), etag='"4"')

    def create_record(
        self,
        entity_name: str,
        values: dict[str, Any],
    ) -> Any:
        assert entity_name == "catalog.Product"
        self.created.append(dict(values))
        stored = {"id": 11, **values}
        return SimpleNamespace(values=stored, etag=None)

    def update_record(
        self,
        entity_name: str,
        identity: Any,
        values: dict[str, Any],
        *,
        if_match: str | int | None = None,
    ) -> Any:
        assert entity_name == "catalog.Product"
        self.updated.append((identity, dict(values), if_match))
        self.product.update(values)
        return SimpleNamespace(values=dict(self.product), etag='"5"')


def test_qt_browse_formats_incremental_cursor_batches() -> None:
    model = compile_project(INVOICING)
    client = _BrowseClient()
    controller = QtBrowseController(
        model,
        client,
        _session(model),
        page_size=2,
    )

    first = controller.fetch_batch()

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
    assert first.next_cursor == "invoice-page-2"
    assert client.reference_reads == 1

    second = controller.fetch_batch(first.next_cursor)
    assert second.rows[0][2] == "NORTH - Northwind"
    assert second.next_cursor is None
    assert [query.cursor for query in client.queries] == [
        None,
        "invoice-page-2",
    ]


def test_qt_browse_builds_metadata_search_filter_and_sort_query() -> None:
    model = compile_project(INVOICING)
    controller = QtBrowseController(
        model,
        _BrowseClient(),
        _session(model),
        page_size=2,
    )

    query = QtBrowseQuery(
        search_text="2026-007",
        filter_name="drafts",
        sort_field="total",
        sort_descending=True,
    )
    spec = controller.query_spec(query, "opaque-next")

    assert controller.search_field == "number"
    assert controller.search_label == "Number"
    assert tuple(controller.named_filters) == ("drafts", "high_value")
    assert controller.sortable_fields == (
        "number",
        "invoice_date",
        "status",
        "total",
    )
    assert spec == QuerySpec(
        filters=(
            FilterCondition("number", "contains", "2026-007"),
            FilterCondition("status", "eq", "draft"),
        ),
        sort=(SortField("total", descending=True),),
        limit=2,
        cursor="opaque-next",
    )
    assert controller.query_summary(query) == (
        "search '2026-007'  ·  Draft invoices  ·  Total descending"
    )

    with pytest.raises(ValueError, match="filter 'missing' is not configured"):
        controller.query_spec(QtBrowseQuery(filter_name="missing"))
    with pytest.raises(ValueError, match="field 'customer' is not sortable"):
        controller.query_spec(QtBrowseQuery(sort_field="customer"))


def test_qt_detail_uses_form_groups_and_nested_inline_collection() -> None:
    model = compile_project(INVOICING)
    client = _BrowseClient()
    controller = QtBrowseController(
        model,
        client,
        _session(model),
        page_size=2,
    )
    controller.fetch_batch()

    detail = controller.load_detail(1)

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
        match="Qt browse batch size must be between 1 and 500",
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

    assert controller.detail_available is False
    with pytest.raises(
        ValueError,
        match="sales.Invoice does not define an accessible form view",
    ):
        controller.load_detail(1)


def test_qt_flat_product_form_uses_defaults_writable_fields_and_etag() -> None:
    model = compile_project(INVOICING)
    invoice = QtBrowseController(
        model,
        _BrowseClient(),
        _session(
            model,
            entity_names=("sales.Invoice",),
            operations=("list", "get", "create", "update"),
        ),
        page_size=2,
    )
    assert invoice.create_available is False
    assert invoice.update_available is False

    client = _ProductClient()
    controller = QtBrowseController(
        model,
        client,
        _product_session(model),
        view_name="catalog.Product.browse",
        page_size=5,
    )

    assert controller.create_available is True
    assert controller.update_available is True
    created = controller.new_form()
    assert created.title == "New Product"
    assert tuple(field.name for field in created.fields) == (
        "code",
        "unit_price",
        "name",
        "active",
    )
    assert all(field.editable for field in created.fields)
    assert next(field for field in created.fields if field.name == "active").value is True
    price = next(field for field in created.fields if field.name == "unit_price")
    assert price.numeric_mask == "0.00"
    assert price.precision == 12
    assert price.scale == 2

    stored = controller.save_form(
        created,
        {
            "code": "SUPPORT",
            "unit_price": Decimal("75.00"),
            "name": "Support hour",
            "active": True,
        },
    )
    assert stored["id"] == 11
    assert client.created == [
        {
            "code": "SUPPORT",
            "unit_price": Decimal("75.00"),
            "name": "Support hour",
            "active": True,
        }
    ]

    edited = controller.edit_form(10)
    assert edited.title == "Edit Product — CONSULT - Consulting"
    assert edited.etag == '"4"'
    controller.save_form(
        edited,
        {
            "code": "CONSULT",
            "unit_price": Decimal("500.00"),
            "name": "Consulting day",
            "active": True,
        },
    )
    assert client.updated == [
        (10, {"name": "Consulting day"}, '"4"')
    ]

    with pytest.raises(ValueError, match="non-writable field"):
        controller.save_form(edited, {"id": 20})


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


def _product_session(model: Any) -> TideSessionInfo:
    product = model.entity("catalog.Product")
    return TideSessionInfo(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        authentication="development",
        principal="qt:editor",
        roles=("sales_clerk",),
        entities={
            "catalog.Product": TideEntityCapabilities(
                operations=("list", "get", "create", "update"),
                readable_fields=tuple(product.fields),
                writable_fields=("code", "name", "unit_price", "active"),
            )
        },
    )
