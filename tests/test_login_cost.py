"""A failed sign-in must not cost the server more than it costs the attacker.

Verifying a password is deliberately expensive -- 600,000 PBKDF2 iterations,
roughly a third of a second of CPU. `login()` computed whether the username was
throttled and then hashed anyway, so a request that was already going to be
refused still bought that work, and rotating usernames never triggered
throttling at all. These handlers share FastAPI's bounded threadpool, so a few
dozen concurrent attempts starve everything else the server does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tide.api.local_auth import (
    LocalAuthenticationBusy,
    LocalAuthenticationError,
    LocalPasswordAuth,
    LocalUserStore,
)


class _CountingStore(LocalUserStore):
    """Counts how often a login attempt reached the expensive path."""

    def __init__(self, path: Path) -> None:
        super().__init__(
            path, application="TIDE Invoicing", password_iterations=1_000
        )
        self.lookups = 0

    def get_user(self, username: str) -> Any:
        self.lookups += 1
        return super().get_user(username)


def _auth(tmp_path: Path, **kwargs: Any) -> tuple[LocalPasswordAuth, _CountingStore]:
    store = _CountingStore(tmp_path / "auth.sqlite3")
    store.initialize()
    store.create_user(
        "clerk", "correct horse battery staple", roles={"sales_clerk"}
    )
    auth = LocalPasswordAuth(
        store,
        allowed_roles=frozenset({"sales_clerk"}),
        max_failures=3,
        secure_cookie=False,
        **kwargs,
    )
    return auth, store


def _fail(auth: LocalPasswordAuth, username: str = "clerk") -> None:
    with pytest.raises(LocalAuthenticationError):
        auth.login(username=username, password="wrong")


def test_a_throttled_attempt_never_reaches_the_hash(tmp_path: Path) -> None:
    """The refusal was already decided; paying for it is pure amplification."""

    auth, store = _auth(tmp_path)
    for _ in range(3):
        _fail(auth)
    spent = store.lookups

    for _ in range(20):
        _fail(auth)

    assert store.lookups == spent


def test_throttling_still_refuses_the_real_password(tmp_path: Path) -> None:
    """Short-circuiting must not become a way to skip the check."""

    auth, _ = _auth(tmp_path)
    for _ in range(3):
        _fail(auth)

    with pytest.raises(LocalAuthenticationError):
        auth.login(username="clerk", password="correct horse battery staple")


def test_an_unknown_username_is_still_equalized(tmp_path: Path) -> None:
    """An un-throttled miss keeps paying, so it cannot be timed apart."""

    auth, store = _auth(tmp_path)

    _fail(auth, username="nobody")

    assert store.lookups == 1


def test_concurrent_logins_are_capped(tmp_path: Path) -> None:
    """Rotating usernames is never throttled, so the cost needs its own bound.

    Per-username throttling cannot see an attacker who never repeats a name.
    Capping how many verifications run at once is what keeps that from
    consuming every thread the application has.
    """

    auth, _ = _auth(tmp_path, max_concurrent_verifications=1)

    with auth.verification_slot():
        with pytest.raises(LocalAuthenticationBusy):
            auth.login(username="other", password="wrong")


def test_the_cap_is_released_when_a_login_fails(tmp_path: Path) -> None:
    auth, _ = _auth(tmp_path, max_concurrent_verifications=1)

    _fail(auth, username="nobody")
    _fail(auth, username="nobody-else")

    assert auth.login(
        username="clerk", password="correct horse battery staple"
    ) is not None


def test_hashing_a_password_is_still_expensive(tmp_path: Path) -> None:
    """Guard the parameter this whole issue is about."""

    del tmp_path
    from tide.api.local_auth import DEFAULT_PASSWORD_ITERATIONS

    assert DEFAULT_PASSWORD_ITERATIONS == 600_000
