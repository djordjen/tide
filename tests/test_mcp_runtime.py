from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tide import compile_project
from tide.api.contracts import TideFilterInput, TideQueryInput, TideSortInput
from tide.data import InMemoryRepository
from tide.mcp import RuntimeMcpService, runtime_mcp_exposures
from tide.model.source import EntitySource
from tide.runtime import (
    AuthorizationError,
    Channel,
    ConcurrencyError,
    InvalidQueryCursor,
    NotFoundError,
    Principal,
    RequestContext,
    VersionPreconditionRequired,
    configure_application_runtime,
)
from tide.services import ActionService, AuditHistoryService, RecordsService
from tide.tui import seed_demo_data


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def test_runtime_mcp_exposure_is_explicit_and_deterministic() -> None:
    model = compile_project(INVOICING)

    exposures = runtime_mcp_exposures(model)

    assert tuple(exposures) == (
        "catalog.Product",
        "crm.Customer",
        "sales.Invoice",
    )
    product = exposures["catalog.Product"]
    assert product.resources == ("schema", "record", "audit")
    assert product.tools == ("search", "create", "update", "delete")
    assert product.schema_uri == (
        "tide://runtime/tide_invoicing/entities/catalog.Product/schema"
    )
    assert product.record_uri_template.endswith(
        "/entities/catalog.Product/records/{identity}"
    )
    assert product.search_tool == "search_catalog_product"
    assert product.create_tool == "create_catalog_product"
    invoice = exposures["sales.Invoice"]
    assert invoice.delete_tool == "delete_sales_invoice"
    assert [(action.action, action.tool) for action in invoice.actions] == [
        ("post", "post_sales_invoice")
    ]


@pytest.mark.parametrize(
    "mcp",
    [
        {"resources": ["schema", "unknown"]},
        {"tools": ["search", "execute_sql"]},
        {"resources": ["schema", "schema"]},
        {"tools": ["search", "search"]},
    ],
)
def test_runtime_mcp_metadata_rejects_unknown_or_repeated_capabilities(
    mcp: dict[str, list[str]],
) -> None:
    with pytest.raises(ValidationError):
        EntitySource.model_validate(
            {
                "entity": "test.Item",
                "expose": {"mcp": mcp},
                "fields": {"id": {"type": "integer", "primary_key": True}},
            }
        )


def test_schema_resource_contains_only_principal_visible_fields() -> None:
    service = _service()

    schema = service.entity_schema(
        "sales.Invoice",
        _context("summary_viewer"),
    )

    names = {field.name for field in schema.fields}
    assert "lines" not in names
    assert "posted_by" not in names
    assert "status" in names
    status = next(field for field in schema.fields if field.name == "status")
    assert status.read_only is True
    assert status.query_operators == (
        "eq",
        "ne",
        "lt",
        "lte",
        "gt",
        "gte",
        "contains",
        "icontains",
    )
    assert schema.actions == ()

    clerk_schema = service.entity_schema("sales.Invoice", _context("sales_clerk"))
    assert [(action.name, action.tool) for action in clerk_schema.actions] == [
        ("post", "post_sales_invoice")
    ]


def test_record_resource_preserves_exact_values_and_protection_metadata() -> None:
    service = _service()

    result = service.record(
        "sales.Invoice",
        "1",
        _context("sales_clerk"),
    ).model_dump(mode="json")

    assert result["entity"] == "sales.Invoice"
    assert result["record"]["invoice_date"] == "2026-07-01"
    assert result["record"]["total"] == "850.00"
    assert result["record"]["lines"][0]["total"] == "850.00"
    assert result["record"]["posted_by"] is None
    assert result["record"]["_tide"]["protected_fields"] == ["posted_by"]


def test_search_tool_reuses_typed_query_security_and_principal_bound_cursors() -> None:
    service = _service()
    context = _context("sales_clerk", identifier="mcp:clerk")
    query = TideQueryInput(
        filters=(TideFilterInput(field="status", operator="eq", value="draft"),),
        sort=(TideSortInput(field="invoice_date"),),
        limit=2,
    )

    first = service.search("sales.Invoice", query, context)
    assert [record["id"] for record in first.records] == [2, 3]
    assert first.next_cursor is not None
    assert first.model_dump(mode="json")["records"][0]["total"] == "480.00"

    second = service.search(
        "sales.Invoice",
        query.model_copy(update={"cursor": first.next_cursor}),
        context,
    )
    assert [record["id"] for record in second.records] == [6, 8]
    assert second.next_cursor is None

    with pytest.raises(InvalidQueryCursor):
        service.search(
            "sales.Invoice",
            query.model_copy(update={"cursor": first.next_cursor}),
            _context("sales_clerk", identifier="mcp:another-clerk"),
        )


def test_mcp_capability_and_authorization_both_fail_closed() -> None:
    service = _service()

    with pytest.raises(NotFoundError, match="capability"):
        service.record("sales.InvoiceLine", "1", _context("sales_clerk"))
    with pytest.raises(NotFoundError, match="capability"):
        service.delete(
            "sales.Invoice",
            2,
            _context("sales_clerk"),
            expected_version=1,
        )
    with pytest.raises(AuthorizationError):
        service.search("crm.Customer", TideQueryInput(), _context(None))
    with pytest.raises(AuthorizationError):
        service.audit("catalog.Product", 1, _context("sales_clerk"))
    with pytest.raises(AuthorizationError):
        service.execute_action(
            "sales.Invoice",
            "post",
            2,
            {},
            _context("auditor"),
            expected_version=1,
            idempotency_key="unauthorized-post",
        )


def test_mcp_crud_reuses_security_validation_exact_values_and_audit() -> None:
    service = _service()
    create_context = _context("sales_clerk", identifier="mcp:writer")

    created = service.create(
        "catalog.Product",
        {
            "code": "MCP",
            "name": "MCP-created product",
            "unit_price": "1234567890.12",
        },
        create_context,
    )

    assert created.operation == "create"
    assert created.identity == 4
    assert created.correlation_id == create_context.correlation_id
    assert created.model_dump(mode="json")["record"]["unit_price"] == (
        "1234567890.12"
    )

    updated = service.update(
        "catalog.Product",
        created.identity,
        {"unit_price": "42.50"},
        _context("sales_clerk", identifier="mcp:writer"),
    )
    assert updated.model_dump(mode="json")["record"]["unit_price"] == "42.50"

    deleted = service.delete(
        "catalog.Product",
        created.identity,
        _context("sales_clerk", identifier="mcp:writer"),
    )
    assert deleted.operation == "delete"
    assert deleted.record is None

    history = service.audit(
        "catalog.Product",
        created.identity,
        _context("auditor", identifier="mcp:auditor"),
    )
    assert {event.operation for event in history.events} == {
        "delete",
        "update",
        "create",
    }

    with pytest.raises(AuthorizationError):
        service.create(
            "catalog.Product",
            {"code": "NOPE", "name": "Denied", "unit_price": "1.00"},
            _context("auditor"),
        )


def test_mcp_versioned_action_requires_observation_and_is_idempotent() -> None:
    service = _service()

    with pytest.raises(VersionPreconditionRequired):
        service.update(
            "sales.Invoice",
            2,
            {"currency": "USD"},
            _context("sales_clerk"),
        )
    with pytest.raises(ConcurrencyError):
        service.update(
            "sales.Invoice",
            2,
            {"currency": "USD"},
            _context("sales_clerk"),
            expected_version=99,
        )

    updated = service.update(
        "sales.Invoice",
        2,
        {"currency": "USD"},
        _context("sales_clerk"),
        expected_version=1,
    )
    assert updated.record is not None
    assert updated.record["version"] == 2

    with pytest.raises(ValueError, match="idempotency_key"):
        service.execute_action(
            "sales.Invoice",
            "post",
            2,
            {},
            _context("sales_clerk"),
            expected_version=2,
        )
    with pytest.raises(ValueError, match="payload must be empty"):
        service.execute_action(
            "sales.Invoice",
            "post",
            2,
            {"occurred_at": "1999-01-01T00:00:00Z"},
            _context("sales_clerk"),
            expected_version=2,
            idempotency_key="unsafe-payload",
        )

    posted = service.execute_action(
        "sales.Invoice",
        "post",
        2,
        {},
        _context("sales_clerk", identifier="mcp:poster"),
        expected_version=2,
        idempotency_key="post-invoice-2",
    )
    replayed = service.execute_action(
        "sales.Invoice",
        "post",
        2,
        {},
        _context("sales_clerk", identifier="mcp:poster"),
        expected_version=2,
        idempotency_key="post-invoice-2",
    )
    assert posted.operation == "action"
    assert posted.action == "post"
    assert posted.record is not None
    assert posted.record["status"] == "posted"
    assert posted.record["version"] == 3
    assert replayed.record == posted.record

    history = service.audit(
        "sales.Invoice",
        2,
        _context("auditor", identifier="mcp:auditor"),
    )
    action_events = [event for event in history.events if event.kind == "action"]
    assert sorted(event.outcome for event in action_events if event.outcome) == [
        "replayed",
        "succeeded",
    ]


def _service() -> RuntimeMcpService:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions) is True
    audits = AuditHistoryService(
        model,
        actions.execution_store,
        records.security,
    )
    return RuntimeMcpService(
        model,
        records,
        actions=actions,
        audits=audits,
    )


def _context(
    role: str | None,
    *,
    identifier: str = "mcp:test",
) -> RequestContext:
    return RequestContext(
        Principal(
            identifier,
            roles=frozenset({role}) if role is not None else frozenset(),
        ),
        channel=Channel.MCP,
    )
