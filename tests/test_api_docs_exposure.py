"""The API's own description is not public unless someone says it is.

`/docs`, `/redoc` and `/openapi.json` were served to anyone who asked. The
document is not a courtesy: it is every exposed entity, every field, every
action, and the `x-tide` runtime configuration -- a complete map of the
application and its data model, handed out before authentication. That is
useful on a laptop and a gift on a deployment, so it is now something a
deployment turns on rather than something it has to remember to turn off.
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

from tide.runtime import Principal
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-development-token-that-is-long-enough"

DOCUMENTATION_PATHS = ("/docs", "/redoc", "/openapi.json")


@pytest.mark.parametrize("path", DOCUMENTATION_PATHS)
def test_the_api_description_is_not_served_by_default(path: str) -> None:
    """Off unless asked: an embedder that never considers the question is safe."""

    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            anonymous = await client.get(path)
            authenticated = await client.get(path, headers=_authorization())

        assert anonymous.status_code == 404
        assert authenticated.status_code == 404, (
            "the document is withheld, not merely gated -- a bearer token is "
            "not what makes publishing the model surface acceptable"
        )

    asyncio.run(exercise())


@pytest.mark.parametrize("path", DOCUMENTATION_PATHS)
def test_the_api_description_is_served_when_asked_for(path: str) -> None:
    app = _app(docs=True)

    async def exercise() -> None:
        async with _client(app) as client:
            response = await client.get(path)

        assert response.status_code == 200

    asyncio.run(exercise())


def test_turning_docs_off_leaves_the_rest_of_the_api_alone() -> None:
    """The gate is on the description, not on the application."""

    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            live = await client.get("/health/live")
            records = await client.get(
                "/api/v1/invoices", headers=_authorization()
            )

        assert live.status_code == 200
        assert records.status_code == 200

    asyncio.run(exercise())


def _app(*, docs: bool = False) -> Any:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    seed_demo_data(model, repository)
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"sales_clerk"})),
        ),
        actions=actions,
        docs=docs,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _authorization() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}
