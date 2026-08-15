"""A session presented to the process that did not issue it says so.

Where sessions live in the process that issued them, a request that reaches a
sibling is answered 401 -- indistinguishable from an expired session, from a
revoked one, and from never having signed in. That is the misconfiguration
this names: the server lease refuses it at startup, and this is what the
deployment that got past the lease anyway looks like from the browser.

The cookie carries the issuing process only when sessions are *not* shared,
so the prefix's presence means "process-local" and a differing prefix means
exactly one thing. A shared deployment emits no prefix and behaves as before.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from tide import compile_project
from tide.api.development_auth import DevelopmentBrowserAuth
from tide.api.server import (
    DevelopmentTokenAuthenticator,
    build_fastapi_app,
    encode_session_cookie,
    split_session_cookie,
)
from tide.api.session_store import InMemorySessionStore
from tide.data import InMemoryRepository
from tide.runtime import Principal
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "0123456789abcdef0123456789abcdef"


def _principal() -> Principal:
    return Principal("development:api", roles=frozenset({"sales_clerk"}))


def _app(*, origin: str | None, sessions: Any = None) -> Any:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    seed_demo_data(model, repository)
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    principal = _principal()
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(TOKEN, principal),
        actions=actions,
        browser_auth=DevelopmentBrowserAuth(principal, sessions=sessions),
        session_origin=origin,
    )


@pytest.mark.parametrize(
    ("origin", "session_id", "expected"),
    [
        (None, "abc", "abc"),
        ("p1", "abc", "p1~abc"),
    ],
)
def test_a_cookie_carries_its_origin_only_when_there_is_one(
    origin: str | None, session_id: str, expected: str
) -> None:
    assert encode_session_cookie(origin, session_id) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("p1~abc", ("p1", "abc")),
        # No separator: a cookie from a shared deployment, or from a build
        # before this existed. It is a session identifier and nothing else.
        ("abc", (None, "abc")),
        ("", (None, None)),
        (None, (None, None)),
        # A session identifier is url-safe base64 and never contains the
        # separator, so a second one is not a delimiter to guess about.
        ("p1~a~b", ("p1", "a~b")),
    ],
)
def test_splitting_a_cookie_is_unambiguous(
    value: str | None, expected: tuple[str | None, str | None]
) -> None:
    assert split_session_cookie(value) == expected


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
    )


async def _open_session(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/_tide/browser-auth/login",
        headers={"X-TIDE-LOGIN": "development"},
    )
    assert response.status_code == 200
    return response.cookies["tide_session"]


def test_a_session_offered_to_another_process_is_named_not_just_refused() -> None:
    left = _app(origin="server-a")
    right = _app(origin="server-b")

    async def exercise() -> httpx.Response:
        async with _client(left) as client:
            cookie = await _open_session(client)
        async with _client(right) as client:
            return await client.get(
                "/api/v1/_tide/session", cookies={"tide_session": cookie}
            )

    answer = asyncio.run(exercise())

    assert answer.status_code == 401
    body = answer.json()
    assert body["code"] == "session_from_another_server"
    assert "another server process" in body["message"]


def test_a_session_offered_to_its_own_process_still_works() -> None:
    """The control: the same cookie, the same server, unaffected."""

    app = _app(origin="server-a")

    async def exercise() -> httpx.Response:
        async with _client(app) as client:
            cookie = await _open_session(client)
            return await client.get(
                "/api/v1/_tide/session", cookies={"tide_session": cookie}
            )

    answer = asyncio.run(exercise())

    assert answer.status_code == 200
    assert answer.json()["principal"] == "development:api"


def test_a_shared_deployment_keeps_the_plain_refusal() -> None:
    """No origin means shared sessions, where a miss is an ordinary miss.

    Reporting "another server process" there would be a lie: the stores agree,
    so a session that cannot be found has genuinely expired or been revoked.
    """

    shared = InMemorySessionStore()
    left = _app(origin=None, sessions=shared)
    right = _app(origin=None, sessions=shared)

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        async with _client(left) as client:
            cookie = await _open_session(client)
        async with _client(right) as client:
            return (
                await client.get(
                    "/api/v1/_tide/session", cookies={"tide_session": cookie}
                ),
                await client.get(
                    "/api/v1/_tide/session",
                    cookies={"tide_session": "not-a-session"},
                ),
            )

    found, missing = asyncio.run(exercise())

    assert found.status_code == 200
    assert missing.status_code == 401
    assert missing.json()["code"] != "session_from_another_server"
