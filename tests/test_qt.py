from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tide import compile_project
from tide.api import TideApiClientError
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.data import FilterCondition, QuerySpec, SortField
from tide.qt import (
    QtBrowseController,
    QtBrowseQuery,
    QtDetailCollection,
    QtDetailGroup,
    QtEditActionError,
)
from tide.sessions import ConflictDisposition, ConflictValueChoice


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


class _InvoiceLookupClient(_BrowseClient):
    def __init__(self) -> None:
        super().__init__()
        self.lookup_queries: list[QuerySpec] = []
        self.reference_selections: list[tuple[str, str, dict[str, Any], Any]] = []
        self.created_customers: list[dict[str, Any]] = []

    def query_records(self, entity_name: str, query: QuerySpec) -> Any:
        if entity_name == "sales.Invoice":
            return super().query_records(entity_name, query)
        assert entity_name == "crm.Customer"
        self.lookup_queries.append(query)
        return SimpleNamespace(
            records=(
                {
                    "id": 1,
                    "code": "ADRIA",
                    "name": "Adria Consulting",
                    "email": "office@adria.test",
                    "active": True,
                },
            ),
            next_cursor=None,
        )

    def get_record(self, entity_name: str, identity: Any) -> Any:
        record = super().get_record(entity_name, identity)
        return SimpleNamespace(values=record.values, etag='"7"')

    def apply_reference_selection(
        self,
        entity_name: str,
        field_name: str,
        values: dict[str, Any],
        identity: Any,
    ) -> dict[str, Any]:
        self.reference_selections.append(
            (entity_name, field_name, dict(values), identity)
        )
        return {**values, field_name: identity}

    def create_record(
        self,
        entity_name: str,
        values: dict[str, Any],
    ) -> Any:
        assert entity_name == "crm.Customer"
        self.created_customers.append(dict(values))
        return SimpleNamespace(values={"id": 3, **values}, etag=None)


class _InvoiceLinesClient(_InvoiceLookupClient):
    def __init__(self) -> None:
        super().__init__()
        self.updated_invoices: list[tuple[Any, dict[str, Any], Any]] = []
        self.created_products: list[dict[str, Any]] = []
        self.action_calls: list[
            tuple[str, Any, dict[str, Any], Any, str | None]
        ] = []
        self.action_error: Exception | None = None

    def query_records(self, entity_name: str, query: QuerySpec) -> Any:
        if entity_name != "catalog.Product":
            return super().query_records(entity_name, query)
        return SimpleNamespace(
            records=(
                {
                    "id": 10,
                    "code": "CONSULT",
                    "name": "Consulting",
                    "unit_price": Decimal("500.00"),
                    "active": True,
                },
            ),
            next_cursor=None,
        )

    def apply_reference_selection(
        self,
        entity_name: str,
        field_name: str,
        values: dict[str, Any],
        identity: Any,
    ) -> dict[str, Any]:
        if entity_name != "sales.InvoiceLine":
            return super().apply_reference_selection(
                entity_name,
                field_name,
                values,
                identity,
            )
        return {
            **values,
            field_name: identity,
            "description": "Consulting",
            "unit_price": Decimal("500.00"),
        }

    def update_record(
        self,
        entity_name: str,
        identity: Any,
        values: dict[str, Any],
        *,
        if_match: str | int | None = None,
    ) -> Any:
        assert entity_name == "sales.Invoice"
        self.updated_invoices.append((identity, deepcopy(values), if_match))
        stored = {**super().get_record(entity_name, identity).values, **values}
        return SimpleNamespace(values=stored, etag='"8"')

    def create_record(
        self,
        entity_name: str,
        values: dict[str, Any],
    ) -> Any:
        if entity_name != "catalog.Product":
            return super().create_record(entity_name, values)
        self.created_products.append(dict(values))
        return SimpleNamespace(values={"id": 11, **values}, etag=None)

    def execute_action(
        self,
        entity_name: str,
        action_name: str,
        identity: Any,
        payload: dict[str, Any] | None = None,
        *,
        if_match: str | int | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        assert entity_name == "sales.Invoice"
        assert action_name == "post"
        self.action_calls.append(
            (
                action_name,
                identity,
                dict(payload or {}),
                if_match,
                idempotency_key,
            )
        )
        if self.action_error is not None:
            raise self.action_error
        stored = deepcopy(super().get_record(entity_name, identity).values)
        stored.update(
            {
                "status": "posted",
                "version": 2,
                "posted_by": "qt:editor",
            }
        )
        return SimpleNamespace(values=stored, etag='"9"')


class _ConflictingInvoiceLinesClient(_InvoiceLinesClient):
    def __init__(self) -> None:
        super().__init__()
        self.etag = '"7"'
        self.invoice = {
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

    def get_record(self, entity_name: str, identity: Any) -> Any:
        if entity_name == "sales.Invoice":
            assert identity == 1
            return SimpleNamespace(
                values=deepcopy(self.invoice),
                etag=self.etag,
            )
        return super().get_record(entity_name, identity)


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


def test_qt_product_conflict_review_rebases_only_resolved_draft_fields() -> None:
    model = compile_project(INVOICING)
    client = _ProductClient()
    controller = QtBrowseController(
        model,
        client,
        _product_session(model),
        view_name="catalog.Product.browse",
        page_size=5,
    )
    form = controller.edit_form(10)
    client.product["name"] = "Server consulting"
    client.product["active"] = False

    conflict = controller.review_edit_conflict(
        form,
        {
            "code": "CONSULT",
            "unit_price": Decimal("525.00"),
            "name": "My consulting",
            "active": True,
        },
    )

    assert conflict.current_form.etag == '"4"'
    assert conflict.comparison.conflicting_fields == ("name",)
    assert conflict.comparison.rebase_fields == ("unit_price",)
    dispositions = {
        field.name: field.disposition
        for field in conflict.comparison.fields
    }
    assert dispositions == {
        "unit_price": ConflictDisposition.YOUR_CHANGE,
        "name": ConflictDisposition.CONFLICT,
        "active": ConflictDisposition.CURRENT_CHANGE,
    }

    rebased = controller.rebase_edit_conflict(
        conflict,
        {"name": ConflictValueChoice.DRAFT},
    )

    assert rebased.retained_fields == ("unit_price", "name")
    assert rebased.dropped_fields == ()
    assert rebased.form.original["name"] == "Server consulting"
    assert next(
        field.value for field in rebased.form.fields if field.name == "name"
    ) == "My consulting"
    assert next(
        field.value
        for field in rebased.form.fields
        if field.name == "unit_price"
    ) == Decimal("525.00")
    assert next(
        field.value for field in rebased.form.fields if field.name == "active"
    ) is False

    controller.save_form(
        rebased.form,
        {
            field.name: field.value
            for field in rebased.form.fields
            if field.editable
        },
    )
    assert client.updated == [
        (
            10,
            {
                "unit_price": Decimal("525.00"),
                "name": "My consulting",
            },
            '"4"',
        )
    ]


def test_qt_invoice_header_resolves_lookup_and_nested_create_contract() -> None:
    model = compile_project(INVOICING)
    client = _InvoiceLookupClient()
    controller = QtBrowseController(
        model,
        client,
        _invoice_edit_session(model),
        page_size=2,
    )

    assert controller.create_available is True
    assert controller.update_available is True
    form = controller.edit_form(1)
    assert form.omitted_collections == ()
    assert len(form.collections) == 1
    assert form.collections[0].name == "lines"
    assert form.collections[0].editable is False
    customer = next(field for field in form.fields if field.name == "customer")
    assert customer.field_type == "reference"
    assert customer.lookup_view == "crm.Customer.lookup"
    assert customer.reference_display == "ADRIA - Adria Consulting"

    spec = controller.lookup_spec("customer")
    assert spec.target_entity == "crm.Customer"
    assert tuple(column.name for column in spec.columns) == (
        "code",
        "name",
        "email",
    )
    assert spec.search_fields == ("code", "name", "email")
    assert spec.limit == 20
    assert spec.create_view == "crm.Customer.edit"

    records = controller.search_lookup(spec, "adria")
    assert len(records) == 1
    assert records[0].display == "ADRIA - Adria Consulting"
    assert len(client.lookup_queries) == 3
    assert tuple(
        query.filters[0].field for query in client.lookup_queries
    ) == ("code", "name", "email")
    assert all(
        query.filters[0].operator == "icontains"
        for query in client.lookup_queries
    )

    draft = {
        "invoice_date": date(2026, 7, 1),
        "currency": "EUR",
        "customer": 1,
    }
    selected = controller.apply_lookup_selection(
        form,
        "customer",
        draft,
        records[0],
    )
    assert selected.identity == 1
    assert selected.values["customer"] == 1
    assert client.reference_selections == [
        ("sales.Invoice", "customer", draft, 1)
    ]

    related = controller.related_create_controller(spec)
    assert related.entity.name == "crm.Customer"
    nested = related.new_form()
    assert nested.title == "New Customer"
    stored = related.save_form(
        nested,
        {
            "code": "NORTH",
            "email": "office@north.test",
            "name": "Northwind",
            "active": True,
        },
    )
    created = controller.lookup_record(spec, stored)
    assert created.identity == 3
    assert created.display == "NORTH - Northwind"


def test_qt_invoice_inline_lines_apply_product_defaults_and_nested_payload() -> None:
    model = compile_project(INVOICING)
    client = _InvoiceLinesClient()
    controller = QtBrowseController(
        model,
        client,
        _invoice_lines_session(model),
        page_size=2,
    )
    form = controller.edit_form(1)

    assert form.omitted_collections == ()
    lines = form.collections[0]
    assert lines.name == "lines"
    assert lines.entity == "sales.InvoiceLine"
    assert lines.editable is True
    assert tuple(column.name for column in lines.columns) == (
        "line_number",
        "product",
        "description",
        "quantity",
        "unit_price",
        "total",
    )
    assert tuple(
        tuple(field.name for field in row)
        for row in lines.groups[0].rows
    ) == (
        ("line_number", "unit_price"),
        ("product", "quantity"),
        ("description",),
    )

    unchanged = controller.save_form(
        form,
        {
            "invoice_date": date(2026, 7, 1),
            "currency": "EUR",
            "customer": 1,
            "lines": list(lines.records),
        },
    )
    assert unchanged["number"] == "INV-2026-001"
    assert client.updated_invoices == []

    added = controller.new_collection_record(
        lines,
        lines.records,
    )
    assert added["line_number"] == 2
    product_spec = controller.lookup_spec(
        "product",
        collection_name="lines",
    )
    assert product_spec.target_entity == "catalog.Product"
    assert product_spec.create_view == "catalog.Product.edit"
    product_controller = controller.related_create_controller(product_spec)
    product_form = product_controller.new_form()
    created_product = product_controller.save_form(
        product_form,
        {
            "code": "SUPPORT",
            "unit_price": Decimal("75.00"),
            "name": "Support",
            "active": True,
        },
    )
    assert controller.lookup_record(
        product_spec,
        created_product,
    ).display == "SUPPORT - Support"
    product = controller.search_lookup(product_spec, "consult")[0]
    selected = controller.apply_lookup_selection(
        form,
        "product",
        {
            **added,
            "quantity": Decimal("2.000"),
        },
        product,
        collection_name="lines",
    )
    preview = controller.preview_collection_record(
        lines,
        selected.values,
    )
    assert preview["description"] == "Consulting"
    assert preview["unit_price"] == Decimal("500.00")
    assert preview["total"] == Decimal("1000.00")
    assert controller.collection_cells(lines, preview)[-1] == "1,000.00"

    stored = controller.save_form(
        form,
        {
            "invoice_date": date(2026, 7, 1),
            "currency": "EUR",
            "customer": 1,
            "lines": [*lines.records, preview],
        },
    )
    assert stored["lines"]
    identity, changes, etag = client.updated_invoices[0]
    assert identity == 1
    assert etag == '"7"'
    assert changes["lines"][-1] == {
        "line_number": 2,
        "product": 10,
        "description": "Consulting",
        "quantity": Decimal("2.000"),
        "unit_price": Decimal("500.00"),
    }
    assert "total" not in changes["lines"][-1]


def test_qt_invoice_post_saves_the_draft_then_executes_with_new_etag() -> None:
    model = compile_project(INVOICING)
    client = _InvoiceLinesClient()
    controller = QtBrowseController(
        model,
        client,
        _invoice_lines_session(model),
        page_size=2,
    )
    form = controller.edit_form(1)

    assert tuple(
        (action.name, action.label, action.enabled)
        for action in form.actions
    ) == (("post", "Post invoice", True),)
    stored = controller.execute_form_action(
        form,
        "post",
        {
            "invoice_date": date(2026, 7, 1),
            "currency": "USD",
            "customer": 1,
            "lines": list(form.collections[0].records),
        },
        idempotency_key="qt:post-test",
    )

    assert client.updated_invoices == [
        (1, {"currency": "USD"}, '"7"')
    ]
    assert client.action_calls == [
        ("post", 1, {}, '"8"', "qt:post-test")
    ]
    assert stored["status"] == "posted"
    assert stored["version"] == 2

    denied_session = _invoice_lines_session(model)
    denied_session = denied_session.model_copy(
        update={
            "entities": {
                **denied_session.entities,
                "sales.Invoice": denied_session.entities[
                    "sales.Invoice"
                ].model_copy(update={"actions": ()}),
            }
        }
    )
    denied = QtBrowseController(
        model,
        _InvoiceLinesClient(),
        denied_session,
        page_size=2,
    )
    assert denied.edit_form(1).actions == ()

    poster_session = _invoice_lines_session(model)
    poster_session = poster_session.model_copy(
        update={
            "principal": "qt:poster",
            "roles": ("invoice_poster",),
            "entities": {
                **poster_session.entities,
                "sales.Invoice": poster_session.entities[
                    "sales.Invoice"
                ].model_copy(
                    update={
                        "operations": ("list", "get"),
                        "writable_fields": (),
                    }
                ),
            },
        }
    )
    poster_client = _InvoiceLinesClient()
    poster = QtBrowseController(
        model,
        poster_client,
        poster_session,
        page_size=2,
    )
    assert poster.update_available is False
    assert poster.form_action_available is True
    poster_form = poster.edit_form(1)
    assert all(not field.editable for field in poster_form.fields)
    assert all(
        not collection.editable for collection in poster_form.collections
    )
    poster.execute_form_action(
        poster_form,
        "post",
        {},
        idempotency_key="qt:poster-role-test",
    )
    assert poster_client.updated_invoices == []
    assert poster_client.action_calls == [
        ("post", 1, {}, '"7"', "qt:poster-role-test")
    ]


def test_qt_action_failure_preserves_the_already_saved_draft_form() -> None:
    model = compile_project(INVOICING)
    client = _InvoiceLinesClient()
    client.action_error = TideApiClientError(
        422,
        "validation_failed",
        "invoice cannot be posted",
    )
    controller = QtBrowseController(
        model,
        client,
        _invoice_lines_session(model),
        page_size=2,
    )
    form = controller.edit_form(1)

    with pytest.raises(QtEditActionError) as failed:
        controller.execute_form_action(
            form,
            "post",
            {
                "invoice_date": date(2026, 7, 1),
                "currency": "USD",
                "customer": 1,
                "lines": list(form.collections[0].records),
            },
            idempotency_key="qt:failed-post-test",
        )

    assert failed.value.code == "validation_failed"
    assert failed.value.saved_before_action is True
    assert failed.value.form.etag == '"8"'
    assert failed.value.form.original["currency"] == "USD"
    assert failed.value.draft["currency"] == "USD"


def test_qt_invoice_conflict_treats_lines_as_one_unit_and_honors_new_locks() -> None:
    model = compile_project(INVOICING)
    client = _ConflictingInvoiceLinesClient()
    controller = QtBrowseController(
        model,
        client,
        _invoice_lines_session(model),
        page_size=2,
    )
    form = controller.edit_form(1)
    local_lines = deepcopy(list(form.collections[0].records))
    local_lines[0]["quantity"] = Decimal("4")
    local_lines[0]["total"] = Decimal("2000.00")
    client.invoice["status"] = "posted"
    client.invoice["version"] = 2
    client.invoice["lines"][0]["quantity"] = Decimal("3")
    client.invoice["lines"][0]["total"] = Decimal("1500.00")
    client.invoice["total"] = Decimal("1500.00")
    client.etag = '"8"'

    conflict = controller.review_edit_conflict(
        form,
        {
            "invoice_date": date(2026, 7, 2),
            "currency": "EUR",
            "customer": 1,
            "lines": local_lines,
        },
    )

    assert conflict.comparison.conflicting_fields == ("lines",)
    assert conflict.comparison.rebase_fields == ("invoice_date",)
    assert conflict.locked_fields == (
        "invoice_date",
        "currency",
        "customer",
        "lines",
    )
    assert conflict.current_form.actions[0].enabled is False
    rebased = controller.rebase_edit_conflict(
        conflict,
        {"lines": ConflictValueChoice.DRAFT},
    )
    assert rebased.retained_fields == ()
    assert rebased.dropped_fields == ("invoice_date", "lines")
    assert rebased.form.etag == '"8"'
    assert next(
        field.value
        for field in rebased.form.fields
        if field.name == "invoice_date"
    ) == date(2026, 7, 1)
    assert all(not field.editable for field in rebased.form.fields)
    assert rebased.form.collections[0].editable is False
    assert rebased.form.collections[0].records[0]["quantity"] == Decimal("3")


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


def _invoice_edit_session(model: Any) -> TideSessionInfo:
    invoice = model.entity("sales.Invoice")
    customer = model.entity("crm.Customer")
    return TideSessionInfo(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        authentication="development",
        principal="qt:editor",
        roles=("sales_clerk",),
        entities={
            "sales.Invoice": TideEntityCapabilities(
                operations=("list", "get", "create", "update"),
                readable_fields=tuple(invoice.fields),
                writable_fields=("invoice_date", "currency", "customer"),
                actions=("post",),
            ),
            "crm.Customer": TideEntityCapabilities(
                operations=("list", "get", "create", "update"),
                readable_fields=tuple(customer.fields),
                writable_fields=("code", "name", "email", "active"),
            ),
        },
    )


def _invoice_lines_session(model: Any) -> TideSessionInfo:
    base = _invoice_edit_session(model)
    line = model.entity("sales.InvoiceLine")
    product = model.entity("catalog.Product")
    return base.model_copy(
        update={
            "entities": {
                **base.entities,
                "sales.Invoice": base.entities["sales.Invoice"].model_copy(
                    update={
                        "writable_fields": (
                            "invoice_date",
                            "currency",
                            "customer",
                            "lines",
                        )
                    }
                ),
                "sales.InvoiceLine": TideEntityCapabilities(
                    draft_operations=("create", "update"),
                    readable_fields=tuple(line.fields),
                    writable_fields=(
                        "line_number",
                        "description",
                        "quantity",
                        "unit_price",
                        "product",
                    ),
                ),
                "catalog.Product": TideEntityCapabilities(
                    operations=("list", "get", "create", "update"),
                    readable_fields=tuple(product.fields),
                    writable_fields=("code", "name", "unit_price", "active"),
                ),
            }
        }
    )
