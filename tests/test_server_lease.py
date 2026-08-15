"""One server at a time, where the sessions cannot be shared.

`--auth oidc` keeps its sessions in the process that issued them, and nothing
in the product stopped an operator running two of those behind one address.
The symptom is not a refusal but intermittent 401s: a user's requests land on
whichever process the proxy picked, and only one of them knows them.

A process cannot see its siblings, but it can see their rows. This is that
lease: taken at startup, renewed while the server runs, released when it
stops, and refused to anyone who finds it live.
"""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from tide.api.server_lease import ServerLeaseHolder
from tide.runtime import Principal
from tide.data.sqlalchemy_leases import SQLAlchemyServerLeaseStore

ROOT = Path(__file__).parents[1]
APPLICATION = "TIDE Invoicing"
SCOPE = "browser-sessions"


@pytest.fixture
def leases(tmp_path: Path):
    store = SQLAlchemyServerLeaseStore(
        f"sqlite+pysqlite:///{(tmp_path / 'leases.db').as_posix()}",
        mode="managed",
    )
    store.create_schema()
    try:
        yield store
    finally:
        store.dispose()


def _acquire(store, lease_id: str, *, now: float = 0.0, ttl: float = 60.0):
    return store.acquire(
        lease_id,
        application=APPLICATION,
        scope=SCOPE,
        now=now,
        ttl=ttl,
    )


def test_the_first_server_takes_the_lease(leases) -> None:
    granted = _acquire(leases, "first")

    assert granted.granted is True
    assert granted.holder == "first"


def test_a_second_server_is_refused_and_told_who_holds_it(leases) -> None:
    _acquire(leases, "first")

    refused = _acquire(leases, "second", now=1.0)

    assert refused.granted is False
    assert refused.holder == "first"
    # The refusal names when the holder was last heard from, because the
    # operator's next question is "is that thing still running?".
    assert refused.held_since == 0.0
    assert refused.renewed_at == 0.0


def test_a_lease_nobody_renewed_is_free_to_take(leases) -> None:
    """A crashed server must not lock the application out forever."""

    _acquire(leases, "first", ttl=60.0)

    assert _acquire(leases, "second", now=59.0, ttl=60.0).granted is False
    taken = _acquire(leases, "second", now=61.0, ttl=60.0)

    assert taken.granted is True
    assert taken.holder == "second"


def test_renewing_keeps_a_lease_from_going_stale(leases) -> None:
    _acquire(leases, "first", ttl=60.0)

    assert leases.renew("first", now=50.0) is True
    # Past the original expiry, but not past the renewal.
    assert _acquire(leases, "second", now=100.0, ttl=60.0).granted is False
    assert _acquire(leases, "second", now=111.0, ttl=60.0).granted is True


def test_renewing_a_lease_somebody_else_took_reports_the_loss(leases) -> None:
    """The heartbeat is how a process learns it was replaced.

    A server whose lease expired and was taken by another must find out, and
    must not quietly go on renewing a row that is no longer its own.
    """

    _acquire(leases, "first", ttl=60.0)
    _acquire(leases, "second", now=61.0, ttl=60.0)

    assert leases.renew("first", now=62.0) is False


def test_releasing_a_lease_lets_the_next_server_start_at_once(leases) -> None:
    _acquire(leases, "first")

    leases.release("first")

    assert _acquire(leases, "second", now=1.0).granted is True


def test_releasing_a_lease_that_moved_on_leaves_it_alone(leases) -> None:
    """Shutdown must not free a lease this process no longer holds."""

    _acquire(leases, "first", ttl=60.0)
    _acquire(leases, "second", now=61.0, ttl=60.0)

    leases.release("first")

    assert _acquire(leases, "third", now=62.0, ttl=60.0).granted is False


def test_two_applications_on_one_database_do_not_block_each_other(
    leases,
) -> None:
    _acquire(leases, "first")

    other = leases.acquire(
        "second",
        application="TIDE Contacts",
        scope=SCOPE,
        now=1.0,
        ttl=60.0,
    )

    assert other.granted is True


def test_the_same_process_asking_twice_keeps_its_own_lease(leases) -> None:
    """Idempotent, so a retry in startup is not a refusal against itself."""

    _acquire(leases, "first")

    again = _acquire(leases, "first", now=5.0)

    assert again.granted is True
    assert again.holder == "first"


def test_the_holder_refuses_to_start_and_says_who_is_there(leases) -> None:
    """What `tide serve` does with the answer, without a thread involved."""

    incumbent = ServerLeaseHolder(
        leases,
        lease_id="incumbent",
        application=APPLICATION,
        scope=SCOPE,
        ttl=60.0,
        clock=lambda: 0.0,
    )
    assert incumbent.acquire().granted is True

    now = [1.0]
    arriving = ServerLeaseHolder(
        leases,
        lease_id="arriving",
        application=APPLICATION,
        scope=SCOPE,
        ttl=60.0,
        clock=lambda: now[0],
    )
    result = arriving.acquire()

    assert result.granted is False
    assert result.holder == "incumbent"


def test_a_heartbeat_keeps_the_lease_and_reports_losing_it(leases) -> None:
    now = [0.0]
    holder = ServerLeaseHolder(
        leases,
        lease_id="mine",
        application=APPLICATION,
        scope=SCOPE,
        ttl=60.0,
        clock=lambda: now[0],
    )
    assert holder.acquire().granted is True

    now[0] = 30.0
    assert holder.beat() is True

    # Somebody else takes it while this process is not looking.
    leases.acquire(
        "usurper", application=APPLICATION, scope=SCOPE, now=200.0, ttl=60.0
    )
    now[0] = 201.0

    assert holder.beat() is False


def test_stopping_releases_the_lease_for_the_next_server(leases) -> None:
    now = [0.0]
    holder = ServerLeaseHolder(
        leases,
        lease_id="mine",
        application=APPLICATION,
        scope=SCOPE,
        ttl=60.0,
        clock=lambda: now[0],
    )
    holder.acquire()

    holder.stop()

    assert _acquire(leases, "next", now=1.0).granted is True


def test_the_running_heartbeat_actually_renews(leases) -> None:
    """One test that the thread exists and does what `beat` does.

    Everything else drives `beat` directly, because a test that waits on a
    thread is a test that can fail for a reason unrelated to leases.
    """

    beats: list[bool] = []
    holder = ServerLeaseHolder(
        leases,
        lease_id="mine",
        application=APPLICATION,
        scope=SCOPE,
        ttl=60.0,
        interval=0.01,
        on_beat=beats.append,
    )
    assert holder.acquire().granted is True
    holder.start()
    try:
        deadline = time.monotonic() + 5.0
        while not beats and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        holder.stop()

    assert beats, "the heartbeat thread never renewed the lease"
    assert all(beats)
    assert holder.running is False


class _StubOidcAuthenticator:
    """Enough of an OIDC bearer adapter for `tide serve` to build an app."""

    authentication_type = "oidc-jwt"
    production = True

    def authenticate(self, credential: str) -> Principal | None:
        return None


def _serve_with_oidc(monkeypatch, database_env: str, *extra: str) -> int:
    """Run `tide serve --auth oidc` with the provider stood in for.

    Discovery is the only part of OIDC that needs a network, and it is not
    what is under test here: the lease is. Both `from_discovery` calls are
    replaced, and everything after them is the real startup path.
    """

    import uvicorn

    from tide.api import auth as auth_module
    from tide.api import browser_auth as browser_module
    from tide.cli.main import main

    authenticator = _StubOidcAuthenticator()
    monkeypatch.setattr(
        auth_module.OidcJwtAuthenticator,
        "from_discovery",
        classmethod(lambda cls, **kwargs: authenticator),
    )
    monkeypatch.setattr(
        browser_module.OidcBrowserAuth,
        "from_discovery",
        classmethod(
            lambda cls, **kwargs: browser_module.OidcBrowserAuth(
                authenticator=authenticator,
                authorization_endpoint="https://identity.example.test/authorize",
                token_endpoint="https://identity.example.test/token",
                client_id="tide-web",
                client_secret="provider-client-secret",
                redirect_uri=(
                    "http://127.0.0.1:8000/api/v1/_tide/browser-auth/callback"
                ),
            )
        ),
    )
    monkeypatch.setattr(uvicorn, "run", lambda app, **configuration: None)

    return main(
        [
            "serve",
            str(ROOT / "applications" / "invoicing"),
            "--database-env",
            database_env,
            "--auth",
            "oidc",
            "--oidc-issuer",
            "https://identity.example.test",
            "--oidc-audience",
            "tide",
            "--web-oidc-client-id",
            "tide-web",
            "--web-oidc-redirect-uri",
            "http://127.0.0.1:8000/api/v1/_tide/browser-auth/callback",
            *extra,
        ]
    )


def test_an_oidc_server_takes_the_lease_and_says_it_is_the_only_one(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    url = f"sqlite+pysqlite:///{(tmp_path / 'oidc.db').as_posix()}"
    monkeypatch.setenv("LEASE_DATABASE_URL", url)

    assert _serve_with_oidc(monkeypatch, "LEASE_DATABASE_URL", "--create-schema") == 0

    output = capsys.readouterr().out
    assert "sessions: this process (this server only)" in output


def test_a_second_oidc_server_is_refused_and_told_what_to_do(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The gap this closes: two OIDC processes behind one address.

    Sessions live in the process that issued them, so the second server does
    not fail at startup today -- it fails later, as a user's requests landing
    on the wrong process and being answered 401.
    """

    url = f"sqlite+pysqlite:///{(tmp_path / 'oidc.db').as_posix()}"
    monkeypatch.setenv("LEASE_DATABASE_URL", url)
    assert _serve_with_oidc(monkeypatch, "LEASE_DATABASE_URL", "--create-schema") == 0
    capsys.readouterr()

    # Another process is up and holding the lease.
    incumbent = SQLAlchemyServerLeaseStore(url)
    try:
        taken = incumbent.acquire(
            "the-other-server",
            application="TIDE Invoicing",
            scope=SCOPE,
            now=time.time(),
            ttl=120.0,
        )
        assert taken.granted is True

        result = _serve_with_oidc(monkeypatch, "LEASE_DATABASE_URL")
    finally:
        incumbent.dispose()

    error = capsys.readouterr().err
    assert result == 1
    assert "another TIDE process is already serving 'TIDE Invoicing'" in error
    assert "--auth oidc keeps browser sessions in the process that issued them" in error
    assert "clears itself in about" in error
