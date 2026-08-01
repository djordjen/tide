from pathlib import Path

import pytest

from tide.api.local_auth import (
    LocalAuthenticationError,
    LocalPasswordAuth,
    LocalUserStore,
    verify_password,
)


PASSWORD = "correct horse battery staple"


def _store(path: Path, *, application: str = "TIDE Invoicing") -> LocalUserStore:
    return LocalUserStore(
        path,
        application=application,
        password_iterations=1_000,
    )


def test_local_user_store_is_explicit_and_application_bound(tmp_path: Path) -> None:
    path = tmp_path / "identity" / "users.sqlite3"
    store = _store(path)

    with pytest.raises(LocalAuthenticationError, match="not initialized"):
        store.validate()

    store.initialize()
    store.validate()
    with pytest.raises(LocalAuthenticationError, match="different application"):
        _store(path, application="Another application").validate()


def test_local_password_login_maps_only_application_roles_and_expires(
    tmp_path: Path,
) -> None:
    now = [1_000.0]
    store = _store(tmp_path / "users.sqlite3")
    store.initialize()
    created = store.create_user(
        "Alice.Example",
        PASSWORD,
        display_name="Alice Example",
        roles=("sales_clerk", "removed_role"),
    )
    assert created.username == "alice.example"
    assert PASSWORD not in created.password_hash
    assert verify_password(PASSWORD, created.password_hash)

    authentication = LocalPasswordAuth(
        store,
        allowed_roles=("sales_clerk", "auditor"),
        secure_cookie=False,
        session_lifetime_seconds=60,
        clock=lambda: now[0],
    )
    login = authentication.login(
        username="ALICE.EXAMPLE",
        password=PASSWORD,
    )
    access = authentication.authenticate_session(login.session_id)
    assert access is not None
    assert access.principal.identifier == "local:alice.example"
    assert access.principal.roles == frozenset({"sales_clerk"})
    assert access.csrf_token == login.csrf_token
    assert authentication.authenticate(login.session_id) == access.principal

    now[0] += 61
    assert authentication.authenticate_session(login.session_id) is None


def test_local_password_failures_are_generic_and_bounded(tmp_path: Path) -> None:
    store = _store(tmp_path / "users.sqlite3")
    store.initialize()
    store.create_user("alice", PASSWORD, roles=("sales_clerk",))
    authentication = LocalPasswordAuth(
        store,
        allowed_roles=("sales_clerk",),
        secure_cookie=True,
        max_failures=2,
    )

    for username in ("missing", "alice", "%invalid"):
        with pytest.raises(
            LocalAuthenticationError,
            match="username or password is incorrect",
        ):
            authentication.login(username=username, password="wrong password")

    with pytest.raises(LocalAuthenticationError, match="incorrect"):
        authentication.login(username="alice", password="still wrong")
    with pytest.raises(LocalAuthenticationError, match="incorrect"):
        authentication.login(username="alice", password=PASSWORD)


def test_local_user_password_can_be_replaced_without_recreating_roles(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "users.sqlite3")
    store.initialize()
    store.create_user("alice", PASSWORD, roles=("auditor",))
    replacement = "a completely different passphrase"

    store.set_password("alice", replacement)

    user = store.get_user("alice")
    assert user is not None
    assert user.roles == frozenset({"auditor"})
    assert not verify_password(PASSWORD, user.password_hash)
    assert verify_password(replacement, user.password_hash)


def test_local_user_creation_rejects_duplicates_and_weak_passwords(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "users.sqlite3")
    store.initialize()
    with pytest.raises(ValueError, match="at least 12"):
        store.create_user("alice", "short", roles=("sales_clerk",))
    store.create_user("alice", PASSWORD, roles=("sales_clerk",))
    with pytest.raises(LocalAuthenticationError, match="already exists"):
        store.create_user("ALICE", PASSWORD, roles=("sales_clerk",))
