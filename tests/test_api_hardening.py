"""Smaller hardening items from the authentication and transport review.

Each is independent; they are together because each is too small to be worth
its own file, and because they share a property: nothing failed loudly before.
A world-readable store, a stale work factor, an absent header and an echoed
exception message are all things a passing test suite is happy to keep.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import Event, Thread
from typing import Any

import httpx
import pytest

from tide import compile_project
from tide.api.local_auth import (
    LocalAuthenticationError,
    LocalPasswordAuth,
    LocalUserStore,
    hash_password,
    verify_password,
)
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.development.generation import CreateApplicationOperation
from tide.runtime import Principal
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-development-token-that-is-long-enough"
PASSWORD = "correct horse battery staple"
SELF = "'self'"


# --- the stored identity file ------------------------------------------------


def test_the_identity_store_is_restricted_on_every_platform(
    tmp_path: Path,
) -> None:
    """`chmod` does nothing on Windows, which is the documented main platform."""

    store = _store(tmp_path, iterations=1_000)
    store.initialize()

    assert store.path.is_file()
    store.validate()
    store.create_user("clerk", PASSWORD, roles={"sales_clerk"})
    assert store.get_user("clerk") is not None
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o077 == 0


def test_restricting_the_store_is_best_effort_on_either_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hardening that fails must not become an outage -- on both branches.

    Both are called directly rather than through whichever one this host
    takes. The first version of this test ran only the host's branch, so it
    passed on Windows and failed on Linux, where `os.chmod` was in fact
    outside the suppression its own docstring promised.
    """

    import subprocess

    from tide.api.local_auth import _restrict_with_chmod, _restrict_with_icacls

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("permission tooling is unavailable")

    target = tmp_path / "auth.sqlite3"
    target.write_bytes(b"")
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setattr(os, "chmod", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)

    _restrict_with_chmod(target)
    _restrict_with_icacls(target)


def test_a_store_whose_permissions_cannot_be_set_is_still_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same property, end to end, on whichever branch this host takes."""

    import subprocess

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("permission tooling is unavailable")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(os, "chmod", refuse)

    store = _store(tmp_path, iterations=1_000)
    store.initialize()
    store.create_user("clerk", PASSWORD, roles={"sales_clerk"})

    assert store.get_user("clerk") is not None


def test_windows_acl_uses_the_effective_sid_not_username(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The environment can name a different account than the process token."""

    import subprocess

    from tide.api.local_auth import _restrict_with_icacls

    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(arguments)
        if arguments[0] == "whoami":
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout='"host\\effective","S-1-5-21-1234"\n',
                stderr="",
            )
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    target = tmp_path / "auth.sqlite3"
    target.write_bytes(b"")
    monkeypatch.setenv("USERNAME", "a-different-account")
    monkeypatch.setattr(subprocess, "run", run)

    _restrict_with_icacls(target)

    assert calls[0] == ["whoami", "/user", "/fo", "csv", "/nh"]
    assert calls[1][0] == "icacls"
    assert "*S-1-5-21-1234:F" in calls[1]
    assert all("a-different-account" not in argument for argument in calls[1])


# --- the stored password hash ------------------------------------------------


def test_a_weaker_stored_hash_is_upgraded_when_its_owner_signs_in(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, iterations=1_000)
    store.initialize()
    store.create_user("clerk", PASSWORD, roles={"sales_clerk"})
    weak = store.get_user("clerk")
    assert weak is not None
    assert weak.password_hash.split("$")[1] == "1000"

    stronger = _store(tmp_path, iterations=4_000)
    auth = LocalPasswordAuth(
        stronger,
        allowed_roles=frozenset({"sales_clerk"}),
        secure_cookie=False,
    )
    auth.login(username="clerk", password=PASSWORD)

    upgraded = stronger.get_user("clerk")
    assert upgraded is not None
    assert upgraded.password_hash.split("$")[1] == "4000"
    assert verify_password(PASSWORD, upgraded.password_hash)


def test_upgrading_the_work_factor_keeps_other_sessions_signed_in(
    tmp_path: Path,
) -> None:
    """A re-hash is not a password change and must not read as one.

    The session store recognises a reset by the recorded *change time*,
    precisely so that strengthening a hash on someone's behalf does not sign
    them out of the windows they left open.
    """

    store = _store(tmp_path, iterations=1_000)
    store.initialize()
    store.create_user("clerk", PASSWORD, roles={"sales_clerk"})
    now = [1000.0]
    auth = LocalPasswordAuth(
        _store(tmp_path, iterations=4_000),
        allowed_roles=frozenset({"sales_clerk"}),
        secure_cookie=False,
        clock=lambda: now[0],
    )
    existing = auth.login(username="clerk", password=PASSWORD).session_id

    auth.login(username="clerk", password=PASSWORD)
    now[0] += auth.revalidate_interval_seconds

    assert auth.authenticate_session(existing) is not None


def test_a_password_reset_wins_a_race_with_hash_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A login with the old password must never undo an administrator's reset."""

    from tide.api import local_auth

    weak = _store(tmp_path, iterations=1_000)
    weak.initialize()
    weak.create_user("clerk", PASSWORD, roles={"sales_clerk"})
    stronger = _store(tmp_path, iterations=4_000)
    auth = LocalPasswordAuth(
        stronger,
        allowed_roles=frozenset({"sales_clerk"}),
        secure_cookie=False,
    )
    replacement = "an entirely different passphrase"
    upgrade_started = Event()
    reset_finished = Event()
    original_hash_password = hash_password

    def delayed_hash(password: str, *, iterations: int) -> str:
        if password == PASSWORD and iterations == 4_000:
            upgrade_started.set()
            assert reset_finished.wait(5), "password reset did not finish"
        return original_hash_password(password, iterations=iterations)

    monkeypatch.setattr(local_auth, "hash_password", delayed_hash)
    outcome: list[str] = []

    def sign_in() -> None:
        try:
            auth.login(username="clerk", password=PASSWORD)
        except LocalAuthenticationError:
            outcome.append("refused")
        else:
            outcome.append("accepted")

    login = Thread(target=sign_in)
    login.start()
    assert upgrade_started.wait(5), "login did not reach the hash upgrade"
    stronger.set_password("clerk", replacement)
    reset_finished.set()
    login.join(5)

    assert not login.is_alive()
    stored = stronger.get_user("clerk")
    assert stored is not None
    assert outcome == ["refused"]
    assert verify_password(replacement, stored.password_hash)
    assert not verify_password(PASSWORD, stored.password_hash)


def test_an_unreadable_hash_is_not_mistaken_for_a_weak_one() -> None:
    from tide.api.local_auth import _MAX_PASSWORD_ITERATIONS, _hash_iterations

    assert _hash_iterations("not-a-hash") == _MAX_PASSWORD_ITERATIONS
    assert _hash_iterations(hash_password(PASSWORD, iterations=1_000)) == 1_000


# --- what the browser is told ------------------------------------------------


def test_the_content_policy_says_what_may_load_not_only_what_may_frame() -> None:
    policy = _headers()["Content-Security-Policy"]
    directives = dict(
        part.strip().split(" ", 1) for part in policy.split(";") if part.strip()
    )

    assert directives["default-src"] == SELF
    assert directives["script-src"] == SELF
    assert directives["object-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["base-uri"] == SELF


def test_transport_security_is_asserted_only_over_tls() -> None:
    assert "Strict-Transport-Security" not in _headers(scheme="http")

    secure = _headers(scheme="https")["Strict-Transport-Security"]

    assert "max-age=31536000" in secure
    assert "includeSubDomains" in secure


# --- what an error tells the caller ------------------------------------------


def test_a_library_exception_is_not_repeated_to_the_caller() -> None:
    """A decimal that will not parse used to answer with its exception repr.

    `{"message": "[<class 'decimal.ConversionSyntax'>]"}` names an internal
    type, tells the caller nothing they can act on, and is exactly the kind of
    detail the error contract promises never to echo.
    """

    body = _query({"filters": [{"field": "total", "operator": "eq", "value": "x"}]})

    assert body["code"] == "invalid_request"
    assert "ConversionSyntax" not in body["message"]
    assert "class" not in body["message"]


def test_a_message_the_server_composed_is_still_returned() -> None:
    """The point is not to say less; it is to say only what was meant."""

    body = _query({"filters": [{"field": "nope", "operator": "eq", "value": "x"}]})

    assert body["message"] == "unknown query field 'nope'"


def _query(payload: dict[str, Any]) -> dict[str, Any]:
    app = _app()
    captured: list[dict[str, Any]] = []

    async def exercise() -> None:
        async with _client(app) as client:
            response = await client.post(
                "/api/v1/invoices/_query",
                json=payload,
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
        assert response.status_code == 400, response.text
        captured.append(response.json())

    asyncio.run(exercise())
    return captured[0]


# --- what an application may be called ---------------------------------------


@pytest.mark.parametrize("reserved", ["con", "nul", "aux", "com1", "lpt9", "prn"])
def test_a_reserved_device_name_is_refused_by_validation(reserved: str) -> None:
    """It used to fail at `os.rename` -- closed, but from the wrong layer."""

    with pytest.raises(ValueError, match="reserved"):
        CreateApplicationOperation(application_id=reserved, name="Demo")


def test_an_ordinary_identifier_is_still_accepted() -> None:
    operation = CreateApplicationOperation(
        application_id="control-tower", name="Demo"
    )

    assert operation.application_id == "control-tower"


def _store(tmp_path: Path, *, iterations: int) -> LocalUserStore:
    return LocalUserStore(
        tmp_path / "auth.sqlite3",
        application="TIDE Invoicing",
        password_iterations=iterations,
    )


def _headers(scheme: str = "https") -> httpx.Headers:
    from tide.api.browser_auth import BrowserSessionAccess

    class _Browser:
        authentication_mode = "password"
        secure_cookie = True
        session_cookie_name = "tide_session"
        session_lifetime_seconds = 60

        def authenticate_session(self, session_id: str | None) -> Any:
            del session_id
            return BrowserSessionAccess(Principal("x", roles=frozenset()), "csrf")

        def end_session(self, session_id: str | None) -> None:
            del session_id

    app = _app(browser_auth=_Browser())
    captured: list[httpx.Headers] = []

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=f"{scheme}://testserver",
        ) as client:
            response = await client.get("/health/live")
        # httpx.Headers rather than a plain dict: header names are
        # case-insensitive, and a dict silently made every lookup here miss.
        captured.append(response.headers)

    asyncio.run(exercise())
    return captured[0]


def _app(**kwargs: Any) -> Any:
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
            TOKEN, Principal("api:test", roles=frozenset({"sales_clerk"}))
        ),
        actions=actions,
        **kwargs,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
