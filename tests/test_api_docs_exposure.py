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
import re
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


SWAGGER_ASSETS = (
    "/api/v1/_tide/docs-assets/swagger-ui-bundle.js",
    "/api/v1/_tide/docs-assets/swagger-ui.css",
    "/api/v1/_tide/docs-assets/swagger-initializer.js",
)


def test_the_documentation_page_asks_no_third_party_for_anything() -> None:
    """It answered 200 with a blank page under the Web UI's own configuration.

    FastAPI's Swagger UI is a CDN script tag, a CDN stylesheet, a CDN favicon
    and an inline initialiser. TIDE sends `script-src 'self'` whenever it owns
    identities, so every one of those was refused and `/docs` rendered nothing
    -- while answering 200, which is all the tests above ever asked it.

    Serving the assets ourselves is what makes the page work without relaxing
    the header that protects the renderer's own origin. The initialiser is a
    file rather than an inline script for the same reason: `'self'` covers a
    file, and covering an inline block would need `unsafe-inline` or a hash of
    a string FastAPI owns.
    """

    app = _app(docs=True)

    async def exercise() -> None:
        async with _client(app) as client:
            page = await client.get("/docs")
            assets = [await client.get(path) for path in SWAGGER_ASSETS]

        assert page.status_code == 200
        assert "swagger-ui" in page.text

        external = re.findall(r"""(?:src|href)=["'](https?://[^"']+)""", page.text)
        assert external == [], (
            "every reference has to be same-origin, or `script-src 'self'` "
            "refuses it and the page renders blank again"
        )

        for path, asset in zip(SWAGGER_ASSETS, assets, strict=True):
            assert asset.status_code == 200, path
            assert asset.content, path
        assert "javascript" in assets[0].headers["content-type"]
        assert "css" in assets[1].headers["content-type"]
        assert "javascript" in assets[2].headers["content-type"]
        assert "SwaggerUIBundle" in assets[2].text

    asyncio.run(exercise())


def test_the_documentation_assets_are_withheld_with_the_documentation() -> None:
    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            responses = [await client.get(path) for path in SWAGGER_ASSETS]

        assert [response.status_code for response in responses] == [404, 404, 404]

    asyncio.run(exercise())


def test_serving_the_documentation_does_not_loosen_the_security_headers() -> None:
    """The point of hosting the assets is that nothing else has to change."""

    app = _app(docs=True, browser_auth=_Browser())

    async def exercise() -> None:
        async with _client(app) as client:
            page = await client.get("/docs")

        policy = page.headers["content-security-policy"]
        assert "script-src 'self';" in policy
        assert "unsafe-inline" not in policy.split("style-src")[0]
        assert "cdn." not in policy

    asyncio.run(exercise())


class _Browser:
    """The Web UI's configuration: TIDE owns identities, so it sends a CSP."""

    authentication_mode = "password"
    secure_cookie = False
    session_cookie_name = "tide_session"
    session_lifetime_seconds = 60

    def authenticate_session(self, session_id: str | None) -> Any:
        del session_id
        return None

    def end_session(self, session_id: str | None) -> None:
        del session_id


def _app(*, docs: bool = False, browser_auth: Any = None) -> Any:
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
        browser_auth=browser_auth,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _authorization() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}
