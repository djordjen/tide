"""One slow identity provider must not stall every other authenticated request.

`authenticate_session` held the auth-wide lock across the token refresh, which
performs a blocking HTTP POST to the provider. Sessions are stored in one
mapping, so every authenticated request in the process contends on that lock:
a single refresh that waited on a slow provider serialized all of them, up to
the client timeout, for users who were nowhere near needing a refresh.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tide.api.browser_auth import OidcBrowserAuth
from tide.runtime import Principal

ISSUER = "https://identity.example.test/tenant"


class _Authenticator:
    def authenticate(self, credential: str) -> Principal | None:
        if credential.startswith("access-"):
            return Principal("oidc:user-123", roles=frozenset({"sales_clerk"}))
        return None


def test_a_slow_refresh_does_not_block_another_session(tmp_path: Any) -> None:
    """The refresh for session A must not hold up a read for session B."""

    del tmp_path
    now = [1000.0]
    refresh_entered = threading.Event()
    release_refresh = threading.Event()

    def provider(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("ascii"))
        if form["grant_type"] == ["refresh_token"]:
            refresh_entered.set()
            # Stands in for a provider that is slow rather than broken.
            assert release_refresh.wait(timeout=10), "refresh was never released"
            return _tokens("access-refreshed", "refresh-next", 120)
        return _tokens("access-one", "refresh-one", 60)

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        browser = _browser(client, now)
        slow = _login(browser)

        # Only the first session is due a refresh. The second signs in after
        # the clock moves, so its own access token is still fresh -- otherwise
        # it would be waiting on a refresh of its own and prove nothing about
        # the lock.
        now[0] += 45
        other = _login(browser)
        assert refresh_entered.is_set() is False

        with ThreadPoolExecutor(max_workers=2) as pool:
            refreshing = pool.submit(browser.authenticate_session, slow)
            assert refreshing is not None
            assert refresh_entered.wait(timeout=10), "the refresh never started"

            reading = pool.submit(browser.authenticate_session, other)
            try:
                access = reading.result(timeout=5)
            except TimeoutError:  # pragma: no cover - the defect being fixed
                release_refresh.set()
                pytest.fail(
                    "a second session's read waited on the first session's "
                    "provider call"
                )
            assert access is not None

            release_refresh.set()
            assert refreshing.result(timeout=10) is not None


def test_two_readers_of_one_session_refresh_it_once(tmp_path: Any) -> None:
    """Per-session locking must still not let a session refresh twice at once."""

    del tmp_path
    now = [1000.0]
    refreshes = []
    started = threading.Event()

    def provider(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("ascii"))
        if form["grant_type"] == ["refresh_token"]:
            refreshes.append(form["refresh_token"][0])
            started.set()
            return _tokens("access-refreshed", "refresh-next", 120)
        return _tokens("access-one", "refresh-one", 60)

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        browser = _browser(client, now)
        session = _login(browser)
        now[0] += 45

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = [
                pool.submit(browser.authenticate_session, session)
                for _ in range(4)
            ]
            accesses = [result.result(timeout=10) for result in results]

    assert all(access is not None for access in accesses)
    assert len(refreshes) == 1, refreshes


def test_ending_a_session_during_its_refresh_does_not_revive_it() -> None:
    """The mapping is re-checked after the provider call, not assumed."""

    now = [1000.0]
    refresh_entered = threading.Event()
    release_refresh = threading.Event()

    def provider(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("ascii"))
        if form["grant_type"] == ["refresh_token"]:
            refresh_entered.set()
            assert release_refresh.wait(timeout=10)
            return _tokens("access-refreshed", "refresh-next", 120)
        return _tokens("access-one", "refresh-one", 60)

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        browser = _browser(client, now)
        session = _login(browser)
        now[0] += 45

        with ThreadPoolExecutor(max_workers=1) as pool:
            refreshing = pool.submit(browser.authenticate_session, session)
            assert refresh_entered.wait(timeout=10)
            browser.end_session(session)
            release_refresh.set()
            assert refreshing.result(timeout=10) is None, (
                "a sign-out that landed during the refresh must win"
            )

        assert browser.authenticate_session(session) is None


def _tokens(access: str, refresh: str, expires_in: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "token_type": "Bearer",
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": expires_in,
        },
    )


def _browser(client: httpx.Client, now: list[float]) -> OidcBrowserAuth:
    return OidcBrowserAuth(
        authenticator=_Authenticator(),
        authorization_endpoint=f"{ISSUER}/authorize",
        token_endpoint=f"{ISSUER}/token",
        client_id="tide-web",
        redirect_uri="https://tide.example.test/api/v1/_tide/auth/callback",
        http_client=client,
        clock=lambda: now[0],
    )


def _login(browser: OidcBrowserAuth) -> str:
    started = browser.begin_login()
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    return browser.complete_login(
        state=state,
        transaction_binding=started.transaction_binding,
        code="provider-code",
    ).session_id
