"""A live session must not outlive the account it belongs to.

Local-password sessions were checked once, at sign-in, and then trusted for
their whole eight-hour lifetime. Disabling the account, resetting the password
or taking away every role left every existing session fully valid, and there
was no way to end them short of restarting the process. The OIDC path
re-validates its access token on every request; this one validated nothing.

Re-reading costs about 1.6ms -- a fresh SQLite connection, a schema check and
two queries -- so it happens on an interval rather than per request, and
`revoke_user` exists for when waiting for the next check is not acceptable.
These tests drive the clock rather than sleeping.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tide.api.local_auth import (
    LocalAuthenticationError,
    LocalPasswordAuth,
    LocalUserStore,
)

PASSWORD = "correct horse battery staple"


def test_a_disabled_account_loses_its_live_session(tmp_path: Path) -> None:
    auth, store, now = _auth(tmp_path)
    session = auth.login(username="clerk", password=PASSWORD).session_id
    assert auth.authenticate_session(session) is not None

    _update(store, "UPDATE tide_local_users SET enabled = 0 WHERE username = ?")
    now[0] += auth.revalidate_interval_seconds

    assert auth.authenticate_session(session) is None


def test_resetting_a_password_signs_that_user_out_everywhere(
    tmp_path: Path,
) -> None:
    """The reset is the revocation; no separate admin step should be needed."""

    auth, store, now = _auth(tmp_path)
    first = auth.login(username="clerk", password=PASSWORD).session_id
    second = auth.login(username="clerk", password=PASSWORD).session_id

    store.set_password("clerk", "an entirely different passphrase")
    now[0] += auth.revalidate_interval_seconds

    assert auth.authenticate_session(first) is None
    assert auth.authenticate_session(second) is None


def test_losing_every_allowed_role_ends_the_session(tmp_path: Path) -> None:
    auth, store, now = _auth(tmp_path)
    session = auth.login(username="clerk", password=PASSWORD).session_id

    _update(store, "DELETE FROM tide_local_user_roles WHERE username = ?")
    now[0] += auth.revalidate_interval_seconds

    assert auth.authenticate_session(session) is None


def test_a_changed_role_set_reaches_the_principal(tmp_path: Path) -> None:
    """Removing one role of several must take effect without ending the session."""

    auth, store, now = _auth(tmp_path, roles={"sales_clerk", "sales_manager"})
    session = auth.login(username="clerk", password=PASSWORD).session_id
    access = auth.authenticate_session(session)
    assert access is not None
    assert access.principal.roles == frozenset({"sales_clerk", "sales_manager"})

    _update(
        store,
        "DELETE FROM tide_local_user_roles "
        "WHERE username = ? AND role = 'sales_manager'",
    )
    now[0] += auth.revalidate_interval_seconds

    access = auth.authenticate_session(session)
    assert access is not None
    assert access.principal.roles == frozenset({"sales_clerk"})


def test_a_change_is_not_seen_before_the_next_check(tmp_path: Path) -> None:
    """The cost of the guarantee, stated rather than left to be discovered.

    A disabled account keeps working until the interval elapses. That is the
    trade for not opening a database connection on every authenticated
    request, and `revoke_user` is the answer when it is not acceptable.
    """

    auth, store, now = _auth(tmp_path, revalidate_interval_seconds=60)
    session = auth.login(username="clerk", password=PASSWORD).session_id
    _update(store, "UPDATE tide_local_users SET enabled = 0 WHERE username = ?")

    now[0] += 59
    assert auth.authenticate_session(session) is not None
    now[0] += 1
    assert auth.authenticate_session(session) is None


def test_a_session_is_not_re_read_on_every_request(tmp_path: Path) -> None:
    """Re-reading opens a SQLite connection, so it is bounded by an interval."""

    auth, store, now = _auth(tmp_path, revalidate_interval_seconds=30)
    session = auth.login(username="clerk", password=PASSWORD).session_id
    before = store.lookups

    for _ in range(20):
        assert auth.authenticate_session(session) is not None

    assert store.lookups == before, "the cached principal should have answered"


def test_revocation_does_not_wait_for_the_interval(tmp_path: Path) -> None:
    """An administrator ending sessions means now, not within thirty seconds."""

    auth, _, now = _auth(tmp_path, revalidate_interval_seconds=3600)
    session = auth.login(username="clerk", password=PASSWORD).session_id
    assert auth.authenticate_session(session) is not None

    auth.revoke_user("clerk")

    assert auth.authenticate_session(session) is None


def test_revoking_everything_leaves_no_session_behind(tmp_path: Path) -> None:
    auth, store, now = _auth(tmp_path, revalidate_interval_seconds=3600)
    store.create_user("second", PASSWORD, roles={"sales_clerk"})
    one = auth.login(username="clerk", password=PASSWORD).session_id
    two = auth.login(username="second", password=PASSWORD).session_id

    auth.revoke_all()

    assert auth.authenticate_session(one) is None
    assert auth.authenticate_session(two) is None


def test_revoking_one_user_leaves_the_others_signed_in(tmp_path: Path) -> None:
    auth, store, now = _auth(tmp_path, revalidate_interval_seconds=3600)
    store.create_user("second", PASSWORD, roles={"sales_clerk"})
    one = auth.login(username="clerk", password=PASSWORD).session_id
    two = auth.login(username="second", password=PASSWORD).session_id

    auth.revoke_user("clerk")

    assert auth.authenticate_session(one) is None
    assert auth.authenticate_session(two) is not None


def test_signing_in_again_after_a_reset_works(tmp_path: Path) -> None:
    """Revocation must not leave the account unusable."""

    auth, store, now = _auth(tmp_path)
    store.set_password("clerk", "an entirely different passphrase")

    with pytest.raises(LocalAuthenticationError):
        auth.login(username="clerk", password=PASSWORD)
    assert auth.login(
        username="clerk", password="an entirely different passphrase"
    ).session_id


class _CountingStore(LocalUserStore):
    """Counts store reads, so an interval can be told from a per-request read."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, application="TIDE Invoicing", password_iterations=1_000)
        self.lookups = 0

    def get_user(self, username: str) -> Any:
        self.lookups += 1
        return super().get_user(username)


def _auth(
    tmp_path: Path,
    *,
    roles: set[str] | None = None,
    **kwargs: Any,
) -> tuple[LocalPasswordAuth, _CountingStore, list[float]]:
    store = _CountingStore(tmp_path / "auth.sqlite3")
    store.initialize()
    store.create_user("clerk", PASSWORD, roles=roles or {"sales_clerk"})
    now = [1000.0]
    auth = LocalPasswordAuth(
        store,
        allowed_roles=frozenset({"sales_clerk", "sales_manager"}),
        secure_cookie=False,
        clock=lambda: now[0],
        **kwargs,
    )
    return auth, store, now


def _update(store: LocalUserStore, statement: str) -> None:
    """Change the stored record behind the running server's back.

    Deliberately raw SQL. The property is that a session notices the account
    changing *by any means* -- and the store has no `set_enabled` or `set_roles`
    of its own today, so there is no supported call to make instead.
    """

    connection = sqlite3.connect(store.path)
    try:
        connection.execute(statement, ("clerk",))
        connection.commit()
    finally:
        connection.close()
