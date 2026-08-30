"""The column chooser's wire: manifest offer, and the three view-state verbs.

The browse manifest names what a person may arrange -- every readable,
non-collection field, not only the declared columns -- and the view-state
routes keep the arrangement they chose. The service owns the rules; these
tests prove the doorway carries them faithfully and adds nothing of its own.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from tide import compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.runtime import Principal
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.services.view_state import InMemoryViewStateRows, ViewStateService
from tide.tui import seed_demo_data

TOKEN = "tide-development-token-that-is-long-enough"
ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _app(*roles: str) -> Any:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    principal = Principal("api:test", roles=frozenset(roles))
    view_state = ViewStateService(
        model, records.security, InMemoryViewStateRows()
    )
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(TOKEN, principal),
        actions=actions,
        view_state=view_state,
    )


@asynccontextmanager
async def _client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://testserver",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        yield client


def test_the_manifest_offers_every_field_a_person_may_arrange() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            response = await client.get("/api/v1/_tide/presentation")
        assert response.status_code == 200
        browse = response.json()["views"]["sales.Invoice.browse"]

        declared = [column["name"] for column in browse["columns"]]
        assert declared == ["number", "invoice_date", "customer", "status", "total"]

        available = {
            column["name"]: column for column in browse["available_columns"]
        }
        # Undeclared but readable fields are on offer...
        assert "version" in available
        assert "currency" in available
        assert "signed_document" in available
        # ...with the same contract shape a declared column carries.
        assert available["number"]["label"] == "Number"
        assert available["customer"]["reference"] is not None
        # A collection is navigation, not a column, and a field this
        # principal cannot read is not on offer.
        assert "lines" not in available
        assert "posted_by" not in available

        # The capability lists widen to the offer: an arrangement may sort
        # and funnel what it shows, not only what the view declared.
        assert "version" in browse["sortable_fields"]
        assert "version" in browse["filterable_fields"]
        # The field-type rules still hold over the wider set.
        assert "signed_document" not in browse["sortable_fields"]
        assert "signed_document" not in browse["filterable_fields"]

    asyncio.run(exercise())


def test_an_auditor_is_offered_the_field_a_clerk_is_not() -> None:
    app = _app("sales_clerk", "auditor")

    async def exercise() -> None:
        async with _client(app) as client:
            response = await client.get("/api/v1/_tide/presentation")
        browse = response.json()["views"]["sales.Invoice.browse"]
        assert "posted_by" in {
            column["name"] for column in browse["available_columns"]
        }

    asyncio.run(exercise())


def test_the_view_state_routes_keep_and_return_an_arrangement() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            initial = await client.get(
                "/api/v1/_tide/view-state/sales.Invoice.browse"
            )
            assert initial.status_code == 200
            assert initial.json() == {"columns": []}

            saved = await client.put(
                "/api/v1/_tide/view-state/sales.Invoice.browse",
                json={
                    "columns": [
                        {"name": "number", "label": "No."},
                        {"name": "total"},
                        {"name": "version"},
                    ]
                },
            )
            assert saved.status_code == 204

            read_back = await client.get(
                "/api/v1/_tide/view-state/sales.Invoice.browse"
            )
            assert read_back.json() == {
                "columns": [
                    {"name": "number", "label": "No."},
                    {"name": "total", "label": None},
                    {"name": "version", "label": None},
                ]
            }

            reset = await client.delete(
                "/api/v1/_tide/view-state/sales.Invoice.browse"
            )
            assert reset.status_code == 204
            afterwards = await client.get(
                "/api/v1/_tide/view-state/sales.Invoice.browse"
            )
            assert afterwards.json() == {"columns": []}

    asyncio.run(exercise())


def test_a_refused_arrangement_names_every_reason() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            refused = await client.put(
                "/api/v1/_tide/view-state/sales.Invoice.browse",
                json={
                    "columns": [
                        {"name": "lines"},
                        {"name": "posted_by"},
                        {"name": "no_such_field"},
                    ]
                },
            )
            assert refused.status_code == 400
            body = refused.json()
            assert body["code"] == "view_state_invalid"
            issues = "\n".join(
                issue["message"] for issue in body["issues"]
            )
            assert "'lines' is a collection" in issues
            assert "'posted_by' cannot be read" in issues
            assert "unknown field 'no_such_field'" in issues

    asyncio.run(exercise())


def test_only_a_real_browse_view_answers() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            for method, kwargs in (
                ("GET", {}),
                ("PUT", {"json": {"columns": [{"name": "number"}]}}),
                ("DELETE", {}),
            ):
                missing = await client.request(
                    method, "/api/v1/_tide/view-state/no.such.view", **kwargs
                )
                assert missing.status_code == 404, method
                form = await client.request(
                    method, "/api/v1/_tide/view-state/sales.Invoice.edit", **kwargs
                )
                assert form.status_code == 404, method

    asyncio.run(exercise())


def test_the_routes_require_authentication() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://testserver"
        ) as anonymous:
            response = await anonymous.get(
                "/api/v1/_tide/view-state/sales.Invoice.browse"
            )
            assert response.status_code == 401

    asyncio.run(exercise())


def test_an_arrangement_is_private_to_its_principal() -> None:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    rows = InMemoryViewStateRows()
    view_state = ViewStateService(model, records.security, rows)

    def app_for(identifier: str) -> Any:
        return build_fastapi_app(
            model,
            records,
            DevelopmentTokenAuthenticator(
                TOKEN,
                Principal(identifier, roles=frozenset({"sales_clerk"})),
            ),
            actions=actions,
            view_state=view_state,
        )

    async def exercise() -> None:
        async with _client(app_for("api:one")) as first:
            saved = await first.put(
                "/api/v1/_tide/view-state/sales.Invoice.browse",
                json={"columns": [{"name": "number"}]},
            )
            assert saved.status_code == 204
        async with _client(app_for("api:two")) as second:
            response = await second.get(
                "/api/v1/_tide/view-state/sales.Invoice.browse"
            )
            assert response.json() == {"columns": []}

    asyncio.run(exercise())
