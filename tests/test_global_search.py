"""Searching every record one identity may read, from one box.

The sweep owns no security of its own: each entity is asked through the
records service's secured lookup, so the entity permission, the row
policies and field security decide what a search can see -- and an entity
that refuses is skipped, never an error the whole search wears.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from tide import compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.runtime import Principal, RequestContext
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.services.search import GlobalSearchService
from tide.tui import seed_demo_data

TOKEN = "tide-development-token-that-is-long-enough"

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _runtime() -> tuple[Any, RecordsService]:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    records = RecordsService(model, repository)
    assert seed_demo_data(model, repository) == 15
    return model, records


def _context(*roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal("local:test", roles=frozenset(roles))
    )


def test_search_answers_grouped_hits_in_model_order() -> None:
    model, records = _runtime()
    search = GlobalSearchService(model, records)

    groups = search.search("consulting", _context("sales_clerk"))

    assert [group.entity for group in groups] == [
        "catalog.Product",
        "crm.Customer",
    ]
    products, customers = groups
    assert products.label == "Products"
    assert [hit.display for hit in products.hits] == ["CONS - Consulting hour"]
    assert products.truncated is False
    assert customers.label == "Customers"
    assert [hit.identity for hit in customers.hits] == [1]
    assert [hit.display for hit in customers.hits] == [
        "ADRIA - Adria Consulting"
    ]


def test_search_finds_an_invoice_by_its_number() -> None:
    model, records = _runtime()
    search = GlobalSearchService(model, records)

    groups = search.search("0003", _context("sales_clerk"))

    assert [group.entity for group in groups] == ["sales.Invoice"]
    invoices = groups[0]
    assert invoices.label == "Invoices"
    assert [hit.identity for hit in invoices.hits] == [3]
    assert [hit.display for hit in invoices.hits] == ["INV-2026-0003"]


def test_search_reaches_only_entities_the_identity_may_read() -> None:
    """summary_viewer reads invoices alone -- so a number finds exactly
    the invoice, while a text that names refused entities finds nothing
    at all, rather than leaking names through the sweep."""

    model, records = _runtime()
    search = GlobalSearchService(model, records)

    found = search.search("inv-2026-0001", _context("summary_viewer"))
    assert [group.entity for group in found] == ["sales.Invoice"]
    assert [hit.display for hit in found[0].hits] == ["INV-2026-0001"]

    assert search.search("consulting", _context("summary_viewer")) == ()


def test_search_is_bounded_per_entity_and_says_so() -> None:
    model, records = _runtime()
    search = GlobalSearchService(model, records)

    groups = search.search("o", _context("sales_clerk"), limit=1)

    products = next(
        group for group in groups if group.entity == "catalog.Product"
    )
    assert len(products.hits) == 1
    assert products.truncated is True


def test_search_refuses_blank_text_and_limits_outside_the_bound() -> None:
    model, records = _runtime()
    search = GlobalSearchService(model, records)
    context = _context("sales_clerk")

    with pytest.raises(ValueError):
        search.search("   ", context)
    with pytest.raises(ValueError):
        search.search("x", context, limit=0)
    with pytest.raises(ValueError):
        search.search("x", context, limit=26)


def test_the_route_answers_the_wire_shape_and_requires_identity() -> None:
    async def exercise() -> None:
        async with _client(_app("sales_clerk")) as client:
            unauthenticated = await client.post(
                "/api/v1/_tide/search", json={"text": "adria"}
            )
            found = await client.post(
                "/api/v1/_tide/search",
                headers=_authorization(),
                json={"text": "adria"},
            )
            blank = await client.post(
                "/api/v1/_tide/search",
                headers=_authorization(),
                json={"text": "   "},
            )
            silly = await client.post(
                "/api/v1/_tide/search",
                headers=_authorization(),
                json={"text": "adria", "limit": 0},
            )

        assert unauthenticated.status_code == 401
        assert found.status_code == 200
        body = found.json()
        assert set(body) == {"wire_version", "text", "groups"}
        assert body["text"] == "adria"
        assert [set(group) for group in body["groups"]] == [
            {"entity", "label", "records", "truncated"}
        ]
        customers = body["groups"][0]
        assert customers["entity"] == "crm.Customer"
        assert customers["records"] == [
            {"identity": 1, "display": "ADRIA - Adria Consulting"}
        ]
        assert blank.status_code == 400
        assert silly.status_code == 422

    asyncio.run(exercise())


def test_the_route_answers_empty_groups_for_a_viewer_with_nothing() -> None:
    async def exercise() -> None:
        async with _client(_app("summary_viewer")) as client:
            found = await client.post(
                "/api/v1/_tide/search",
                headers=_authorization(),
                json={"text": "consulting"},
            )
        assert found.status_code == 200
        assert found.json()["groups"] == []

    asyncio.run(exercise())


def _app(role: str) -> Any:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    assert seed_demo_data(model, repository) == 15
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({role})),
        ),
        actions=actions,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _authorization() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}
