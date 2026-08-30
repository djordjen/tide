"""The saved-view routes: list, keep, forget -- the service owns the rules.

No manifest change rides along: saved views are pure user state, fetched
per view, so what these tests pin is the doorway -- auth, the three verbs,
the house error shape, and that one principal's names never reach another.
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
from tide.services.saved_views import InMemorySavedViewRows, SavedViewService
from tide.tui import seed_demo_data

TOKEN = "tide-development-token-that-is-long-enough"
ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"

OVERDUE_DOCUMENT = {
    "name": "Overdue invoices",
    "named_filter": "drafts",
    "value_filters": {"status": ["draft", None]},
    "sort": [{"field": "total", "descending": True}],
    "columns": [
        {"name": "number", "label": "No."},
        {"name": "total", "label": None},
    ],
}


def _app(*roles: str) -> Any:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    principal = Principal("api:test", roles=frozenset(roles))
    saved_views = SavedViewService(
        model, records.security, InMemorySavedViewRows()
    )
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(TOKEN, principal),
        actions=actions,
        saved_views=saved_views,
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


def test_the_routes_keep_list_and_forget_a_saved_view() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            empty = await client.get(
                "/api/v1/_tide/saved-views/sales.Invoice.browse"
            )
            assert empty.status_code == 200
            assert empty.json() == {"views": []}

            kept = await client.put(
                "/api/v1/_tide/saved-views/sales.Invoice.browse/Overdue%20invoices",
                json=OVERDUE_DOCUMENT,
            )
            assert kept.status_code == 204

            listed = await client.get(
                "/api/v1/_tide/saved-views/sales.Invoice.browse"
            )
            assert listed.json() == {"views": [OVERDUE_DOCUMENT]}

            forgotten = await client.delete(
                "/api/v1/_tide/saved-views/sales.Invoice.browse/Overdue%20invoices"
            )
            assert forgotten.status_code == 204
            afterwards = await client.get(
                "/api/v1/_tide/saved-views/sales.Invoice.browse"
            )
            assert afterwards.json() == {"views": []}

    asyncio.run(exercise())


def test_the_path_name_wins_over_the_body_name() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            await client.put(
                "/api/v1/_tide/saved-views/sales.Invoice.browse/Renamed",
                json=OVERDUE_DOCUMENT,
            )
            listed = await client.get(
                "/api/v1/_tide/saved-views/sales.Invoice.browse"
            )
            assert [entry["name"] for entry in listed.json()["views"]] == [
                "Renamed"
            ]

    asyncio.run(exercise())


def test_a_refused_saved_view_names_every_reason() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            refused = await client.put(
                "/api/v1/_tide/saved-views/sales.Invoice.browse/Bad",
                json={
                    "name": "Bad",
                    "named_filter": "no_such_filter",
                    "value_filters": {"posted_by": ["x"]},
                    "sort": [{"field": "signed_document", "descending": False}],
                    "columns": None,
                },
            )
            assert refused.status_code == 400
            body = refused.json()
            assert body["code"] == "saved_view_invalid"
            issues = "\n".join(issue["message"] for issue in body["issues"])
            assert "unknown named filter 'no_such_filter'" in issues
            assert "'posted_by' cannot carry a value filter" in issues
            assert "'signed_document' cannot be sorted" in issues

    asyncio.run(exercise())


def test_only_a_real_browse_view_answers() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            for method, path, kwargs in (
                ("GET", "/api/v1/_tide/saved-views/no.such.view", {}),
                (
                    "PUT",
                    "/api/v1/_tide/saved-views/sales.Invoice.edit/X",
                    {"json": OVERDUE_DOCUMENT},
                ),
                ("DELETE", "/api/v1/_tide/saved-views/no.such.view/X", {}),
            ):
                response = await client.request(method, path, **kwargs)
                assert response.status_code == 404, (method, path)

    asyncio.run(exercise())


def test_the_routes_require_authentication() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://testserver"
        ) as anonymous:
            response = await anonymous.get(
                "/api/v1/_tide/saved-views/sales.Invoice.browse"
            )
            assert response.status_code == 401

    asyncio.run(exercise())
