"""A browser session that asks for no credential, and the fences around it.

The feature is one button. Everything worth testing here is what stops that
button being reachable from anywhere but this machine, so most of this file is
about refusals rather than about signing in.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from tide import compile_project
from tide.api.development_auth import (
    DevelopmentBrowserAuth,
    is_loopback_host_header,
)
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.runtime import Principal
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

INVOICING = Path(__file__).resolve().parents[1] / "applications" / "invoicing"
TOKEN = "development-token-for-tests-0123456789"


def _principal() -> Principal:
    return Principal("development:api", roles=frozenset({"sales_clerk"}))


def _app(
    *,
    browser_auth: Any = None,
    authenticator: Any = None,
) -> Any:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    configure_application_runtime(model, records, actions)
    principal = _principal()
    return build_fastapi_app(
        model,
        records,
        authenticator or DevelopmentTokenAuthenticator(TOKEN, principal),
        actions=actions,
        browser_auth=(
            DevelopmentBrowserAuth(principal)
            if browser_auth is None
            else browser_auth
        ),
    )


def _client(app: Any, host: str = "127.0.0.1") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=f"http://{host}",
    )


@pytest.mark.parametrize(
    ("header", "loopback"),
    [
        ("localhost", True),
        ("localhost:8000", True),
        ("127.0.0.1", True),
        ("127.0.0.1:8011", True),
        ("[::1]", True),
        ("[::1]:8000", True),
        ("LocalHost:8000", True),
        # The shape a rebinding attack arrives in: the address is this machine,
        # the name is not.
        ("tide.attacker.example", False),
        ("tide.attacker.example:8000", False),
        # Not loopback despite the prefix, and the reason a substring test
        # would be wrong here.
        ("127.0.0.1.attacker.example", False),
        ("localhost.attacker.example", False),
        ("192.168.1.10:8000", False),
        ("", False),
        (None, False),
    ],
)
def test_only_this_machines_own_names_count_as_loopback(
    header: str | None, loopback: bool
) -> None:
    assert is_loopback_host_header(header) is loopback


def test_a_development_server_opens_without_a_credential() -> None:
    """The whole point: a session, from a POST that carries nothing."""

    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            started = await client.post(
                "/api/v1/_tide/browser-auth/login",
                headers={"X-TIDE-LOGIN": "development"},
            )
            assert started.status_code == 200
            assert "csrf_token" in started.json()
            assert "tide_session" in started.cookies

            # And the session is worth having: it reads records, as the
            # principal the server was started with rather than as an
            # implicit superuser.
            listed = await client.get("/api/v1/invoices")
            assert listed.status_code == 200
            assert listed.json()["records"]

    asyncio.run(exercise())


def test_a_request_naming_another_host_is_refused() -> None:
    """The DNS-rebinding fence, and the one nothing else in the stack provides.

    Binding to 127.0.0.1 does not stop a browser whose attacker-controlled
    domain resolves there, and the absent CORS headers do not either, because
    to that browser the page is same-origin. The name in the `Host` header is
    what is still wrong, so that is what is checked.
    """

    app = _app()

    async def exercise() -> None:
        async with _client(app, host="tide.attacker.example") as client:
            refused = await client.post(
                "/api/v1/_tide/browser-auth/login",
                headers={"X-TIDE-LOGIN": "development"},
            )
            assert refused.status_code == 403
            assert refused.json()["detail"]["code"] == "non_loopback_host"
            assert "tide_session" not in refused.cookies

            # Not only the door: every route, including the ones that would
            # have been readable with a bearer token.
            for path in ("/api/v1/invoices", "/api/v1/_tide/presentation"):
                blocked = await client.get(
                    path, headers={"Authorization": f"Bearer {TOKEN}"}
                )
                assert blocked.status_code == 403, path

    asyncio.run(exercise())


def test_a_loopback_named_request_is_not_refused() -> None:
    """The other half: the check has to let this machine through.

    Without this, a fence that refused everything would satisfy the test above
    and take the feature with it.
    """

    app = _app()

    async def exercise() -> None:
        for host in ("localhost:8011", "127.0.0.1", "[::1]:8000"):
            async with _client(app, host=host) as client:
                started = await client.post(
                    "/api/v1/_tide/browser-auth/login",
                    headers={"X-TIDE-LOGIN": "development"},
                )
                assert started.status_code == 200, host

    asyncio.run(exercise())


def test_a_credential_free_session_cannot_be_attached_to_production_identity() -> None:
    """The structural fence, for everything that does not go through the CLI.

    `tide serve` refuses `--auth development` off loopback, but that is one
    caller. A "development only" flag reaches production exactly by being
    possible, so the refusal lives where the application is assembled.
    """

    class _ProductionAuthenticator:
        authentication_type = "test-production"
        production = True

        def authenticate(self, credential: str) -> Principal | None:
            return _principal() if credential == TOKEN else None

    with pytest.raises(ValueError, match="production identity adapter"):
        _app(authenticator=_ProductionAuthenticator())


def test_the_rest_api_still_wants_its_bearer_token() -> None:
    """What this mode relaxes is the browser door, not the API.

    A caller with neither a session cookie nor a token is still refused, so
    `curl` and Swagger behave exactly as they did before.
    """

    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            anonymous = await client.get("/api/v1/invoices")
            assert anonymous.status_code == 401

            with_token = await client.get(
                "/api/v1/invoices",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            assert with_token.status_code == 200

    asyncio.run(exercise())


def test_the_session_door_needs_the_header_a_cross_site_form_cannot_send() -> None:
    """A plain form post from another origin must not start a session.

    It proves nothing about who is asking -- nothing in this mode does -- but
    a custom header is unavailable to a cross-site form, so opening the UI
    stays something the developer did rather than something a page did.
    """

    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            refused = await client.post("/api/v1/_tide/browser-auth/login")
            assert refused.status_code == 400
            assert "tide_session" not in refused.cookies

    asyncio.run(exercise())


def test_the_manifest_declares_the_mode_so_the_renderer_can_show_it() -> None:
    """The connect screen branches on this, and says what it is showing."""

    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            described = await client.get("/api/v1/_tide/browser-auth")
            assert described.status_code == 200
            assert described.json()["mode"] == "development"
            assert described.json()["enabled"] is True

    asyncio.run(exercise())


def test_a_session_ends_and_stops_working() -> None:
    authenticator = DevelopmentBrowserAuth(_principal())
    result = authenticator.begin_session()

    assert authenticator.authenticate_session(result.session_id) is not None
    authenticator.end_session(result.session_id)
    assert authenticator.authenticate_session(result.session_id) is None


def test_a_session_expires() -> None:
    clock = [1000.0]
    authenticator = DevelopmentBrowserAuth(
        _principal(),
        session_lifetime_seconds=60,
        clock=lambda: clock[0],
    )
    result = authenticator.begin_session()

    clock[0] += 59
    assert authenticator.authenticate_session(result.session_id) is not None
    clock[0] += 2
    assert authenticator.authenticate_session(result.session_id) is None
