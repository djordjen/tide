"""An account can be disabled and its roles changed, not only created.

`enabled` was read and never written: `login` refused a disabled account and a
live session ended at its next revalidation, but nothing in TIDE could disable
one. Roles were fixed at creation for the same reason. Both meant reaching for
`sqlite3` by hand, which is not an interface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide.api.local_auth import (
    LocalAuthenticationError,
    LocalPasswordAuth,
    LocalUserStore,
)
from tide.cli import main

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
PASSWORD = "correct horse battery staple"


def test_a_disabled_account_cannot_sign_in(tmp_path: Path) -> None:
    auth, store, _clock = _auth_for(tmp_path)
    assert auth.login(username="clerk", password=PASSWORD).session_id

    store.set_enabled("clerk", False)

    with pytest.raises(LocalAuthenticationError):
        auth.login(username="clerk", password=PASSWORD)


def test_disabling_ends_a_session_that_is_already_open(tmp_path: Path) -> None:
    """The guarantee #22 added, now with something able to trigger it."""

    auth, store, now = _auth_for(tmp_path)
    session = auth.login(username="clerk", password=PASSWORD).session_id

    store.set_enabled("clerk", False)
    now[0] += auth.revalidate_interval_seconds

    assert auth.authenticate_session(session) is None


def test_disabling_keeps_the_account_and_can_be_undone(tmp_path: Path) -> None:
    auth, store, _clock = _auth_for(tmp_path)
    store.set_enabled("clerk", False)

    store.set_enabled("clerk", True)

    assert auth.login(username="clerk", password=PASSWORD).session_id


def test_roles_are_replaced_rather_than_added_to(tmp_path: Path) -> None:
    """An add-only interface could never withdraw one, which is the point."""

    _auth, store, _clock = _auth_for(tmp_path, roles={"sales_clerk", "sales_manager"})
    del _auth, _clock

    assert store.set_roles("clerk", {"sales_clerk"}) == frozenset({"sales_clerk"})

    user = store.get_user("clerk")
    assert user is not None
    assert user.roles == frozenset({"sales_clerk"})


def test_a_user_may_not_be_left_with_no_roles(tmp_path: Path) -> None:
    """An account nobody can sign in to is a disabled account said unclearly."""

    store = _store(tmp_path)

    with pytest.raises(ValueError, match="at least one role"):
        store.set_roles("clerk", set())


@pytest.mark.parametrize("operation", ["set_enabled", "set_roles"])
def test_changing_an_absent_user_is_refused(tmp_path: Path, operation: str) -> None:
    store = _store(tmp_path)

    with pytest.raises(LocalAuthenticationError, match="does not exist"):
        if operation == "set_enabled":
            store.set_enabled("nobody", False)
        else:
            store.set_roles("nobody", {"sales_clerk"})


def test_a_guarded_write_reads_fresh_state_and_can_refuse(tmp_path: Path) -> None:
    """The guard runs inside the write's own transaction.

    It receives the accounts as they are at write time -- not as some
    earlier check saw them -- and its refusal rolls the write back, which
    is what makes a last-administrator invariant hold across concurrent
    callers instead of between a check and an act.
    """

    store = _store(tmp_path)
    seen: list[tuple[str, bool]] = []

    def refuse(users) -> None:
        seen.extend((user.username, user.enabled) for user in users)
        raise RuntimeError("refused by guard")

    with pytest.raises(RuntimeError, match="refused by guard"):
        store.set_enabled("clerk", False, guard=refuse)

    unchanged = store.get_user("clerk")
    assert unchanged is not None
    assert unchanged.enabled is True
    assert ("clerk", True) in seen

    with pytest.raises(RuntimeError, match="refused by guard"):
        store.set_roles("clerk", {"sales_clerk"}, guard=refuse)


# --- through the command line ------------------------------------------------


def test_the_command_line_disables_and_enables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)

    assert _run("disable-user", store.path) == 0
    disabled = store.get_user("clerk")
    assert disabled is not None and disabled.enabled is False
    assert "next revalidation" in capsys.readouterr().out

    assert _run("enable-user", store.path) == 0
    enabled = store.get_user("clerk")
    assert enabled is not None and enabled.enabled is True


def test_the_command_line_refuses_a_role_the_application_does_not_define(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The store does not know the model, so the command has to check."""

    store = _store(tmp_path)

    assert _run("set-roles", store.path, "--role", "not_a_role") == 1

    assert "unknown application role" in capsys.readouterr().err
    user = store.get_user("clerk")
    assert user is not None
    assert user.roles == frozenset({"sales_clerk"})


def test_the_command_line_replaces_roles(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert _run("set-roles", store.path, "--role", "auditor") == 0

    user = store.get_user("clerk")
    assert user is not None
    assert user.roles == frozenset({"auditor"})


def _run(command: str, store_path: Path, *extra: str) -> int:
    return main(
        [
            "auth",
            command,
            str(INVOICING),
            "--store",
            str(store_path),
            "--username",
            "clerk",
            *extra,
        ]
    )


def _store(tmp_path: Path) -> LocalUserStore:
    store = LocalUserStore(
        tmp_path / "auth.sqlite3",
        application="TIDE Invoicing",
        password_iterations=1_000,
    )
    store.initialize()
    store.create_user("clerk", PASSWORD, roles={"sales_clerk"})
    return store


def _auth_for(
    tmp_path: Path, *, roles: set[str] | None = None
) -> tuple[LocalPasswordAuth, LocalUserStore, list[float]]:
    store = LocalUserStore(
        tmp_path / "auth.sqlite3",
        application="TIDE Invoicing",
        password_iterations=1_000,
    )
    store.initialize()
    store.create_user("clerk", PASSWORD, roles=roles or {"sales_clerk"})
    now = [1000.0]
    auth = LocalPasswordAuth(
        store,
        allowed_roles=frozenset({"sales_clerk", "sales_manager", "auditor"}),
        secure_cookie=False,
        clock=lambda: now[0],
    )
    return auth, store, now


