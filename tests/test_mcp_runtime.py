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
    InvalidQueryCursor,
    NotFoundError,
    Principal,
    RequestContext,
)
from tide.services import RecordsService
from tide.tui import seed_demo_data


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def test_runtime_mcp_exposure_is_read_only_and_deterministic() -> None:
    model = compile_project(INVOICING)

    exposures = runtime_mcp_exposures(model)

    assert tuple(exposures) == (
        "catalog.Product",
        "crm.Customer",
        "sales.Invoice",
    )
    product = exposures["catalog.Product"]
    assert product.resources == ("schema", "record")
    assert product.tools == ("search",)
    assert product.schema_uri == (
        "tide://runtime/tide_invoicing/entities/catalog.Product/schema"
    )
    assert product.record_uri_template.endswith(
        "/entities/catalog.Product/records/{identity}"
    )
    assert product.search_tool == "search_catalog_product"


@pytest.mark.parametrize(
    "mcp",
    [
        {"resources": ["schema", "unknown"]},
        {"tools": ["search", "delete"]},
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
    with pytest.raises(AuthorizationError):
        service.search("crm.Customer", TideQueryInput(), _context(None))


def _service() -> RuntimeMcpService:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    return RuntimeMcpService(model, RecordsService(model, repository))


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
