"""Browser sessions that outlive the process that issued them.

Two authenticator instances sharing one store are what two workers are: the
property under test is that a session opened against one is honored by the
other, which no single-instance test can observe.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path
import pathlib
from typing import Any

import pytest
from sqlalchemy import select

from tide import compile_project
from tide.api.development_auth import DevelopmentBrowserAuth
from tide.api.local_auth import (
    LocalAuthenticationError,
    LocalPasswordAuth,
    LocalUserStore,
)
from tide.api.session_store import InMemorySessionStore, SessionRecord
from tide.data import (
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemyRepository,
    propose_migration,
)
from tide.data.backup import DatabaseBackupError, _validate_application_backup
from tide.data.sqlalchemy import SchemaCompatibilityError, SchemaManagementError
from tide.data.sqlalchemy_sessions import SQLAlchemySessionStore
from tide.cli.storage import open_run_storage
from tide.runtime import Principal

ROOT = pathlib.Path(__file__).parents[1]
PASSWORD = "correct horse battery staple"


def _record(
    *,
    subject: str = "alice",
    expires_at: float = 1_000.0,
    csrf: str = "csrf-token",
) -> SessionRecord:
    return SessionRecord(
        subject=subject,
        expires_at=expires_at,
        state={"version": 1, "csrf_token": csrf, "roles": ["sales_clerk"]},
    )


@pytest.fixture(params=["memory", "sql"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[object]:
    """Every store answers the same contract, however it holds it."""

    if request.param == "memory":
        yield InMemorySessionStore(max_entries=4)
        return
    database = tmp_path / "shared-sessions.db"
    shared = SQLAlchemySessionStore(
        f"sqlite+pysqlite:///{database.as_posix()}",
        mode="managed",
        max_entries=4,
    )
    shared.create_schema()
    try:
        yield shared
    finally:
        shared.dispose()


def _users(path: Path) -> LocalUserStore:
    store = LocalUserStore(
        path,
        application="TIDE Invoicing",
        password_iterations=1_000,
    )
    store.initialize()
    store.create_user(
        "alice",
        PASSWORD,
        display_name="Alice Example",
        roles=("sales_clerk",),
    )
    return store


def _worker(
    users: LocalUserStore,
    sessions: Any,
    *,
    clock: object = None,
) -> LocalPasswordAuth:
    return LocalPasswordAuth(
        users,
        allowed_roles=("sales_clerk",),
        secure_cookie=False,
        session_lifetime_seconds=60,
        sessions=sessions,
        **({"clock": clock} if clock is not None else {}),
    )


def test_a_session_opened_on_one_worker_is_honored_by_another(
    tmp_path: Path,
) -> None:
    users = _users(tmp_path / "users.sqlite3")
    sessions = InMemorySessionStore()
    first = _worker(users, sessions)
    second = _worker(users, sessions)

    login = first.login(username="alice", password=PASSWORD)
    access = second.authenticate_session(login.session_id)

    assert access is not None
    assert access.principal.identifier == "local:alice"
    assert access.principal.roles == frozenset({"sales_clerk"})
    assert access.csrf_token == login.csrf_token


def test_a_development_session_is_shared_the_same_way(tmp_path: Path) -> None:
    """So that a multi-worker server can be run and looked at locally.

    Development mode is the one a browser can be pointed at without a
    credential, which makes it the one worth being able to run with more than
    one worker: otherwise the arrangement this all exists for is the single
    arrangement that cannot be checked by hand.
    """

    principal = Principal("development:api", roles=frozenset({"sales_clerk"}))
    sessions = InMemorySessionStore()
    left = DevelopmentBrowserAuth(principal, sessions=sessions)
    right = DevelopmentBrowserAuth(principal, sessions=sessions)

    started = left.begin_session()
    access = right.authenticate_session(started.session_id)

    assert access is not None
    assert access.principal.identifier == "development:api"
    assert access.csrf_token == started.csrf_token

    right.end_session(started.session_id)
    assert left.authenticate_session(started.session_id) is None


def test_the_login_throttle_is_counted_across_workers_not_per_worker(
    tmp_path: Path,
) -> None:
    """Otherwise N workers is N times the budget for guessing a password.

    A throttle held per process does not fail loudly when a second worker
    appears; it just quietly stops being the number it says it is, which is
    the worst way for a security control to degrade.
    """

    users = _users(tmp_path / "users.sqlite3")
    sessions = InMemorySessionStore()
    left = _worker(users, sessions)
    right = _worker(users, sessions)

    for _attempt in range(5):
        with pytest.raises(LocalAuthenticationError):
            left.login(username="alice", password="wrong password entirely")

    # The budget is spent. The other worker must not hand out a fresh one, and
    # must not sign the account in either, even with the correct password.
    with pytest.raises(LocalAuthenticationError):
        right.login(username="alice", password=PASSWORD)


def test_a_store_returns_what_it_was_given(store: Any) -> None:
    record = _record()
    store.create("session-1", record, now=0.0)

    assert store.read("session-1", now=0.0) == record
    assert store.read("absent", now=0.0) is None


def test_a_store_forgets_a_session_that_has_expired(store: Any) -> None:
    store.create("session-1", _record(expires_at=100.0), now=0.0)

    assert store.read("session-1", now=99.0) is not None
    assert store.read("session-1", now=100.0) is None


def test_a_store_swaps_a_session_only_while_it_is_unchanged(store: Any) -> None:
    original = _record(csrf="first")
    store.create("session-1", original, now=0.0)
    replacement = _record(csrf="second")

    assert store.replace("session-1", original, replacement, now=0.0) is True
    assert store.read("session-1", now=0.0) == replacement
    # The caller that still holds the original has lost the race, and must be
    # told so rather than overwriting the record that beat it.
    assert store.replace("session-1", original, _record(csrf="third"), now=0.0) is False
    assert store.read("session-1", now=0.0) == replacement


def test_a_store_discards_a_session_only_while_it_is_unchanged(store: Any) -> None:
    original = _record(csrf="first")
    store.create("session-1", original, now=0.0)
    store.replace("session-1", original, _record(csrf="second"), now=0.0)

    assert store.discard("session-1", original) is False
    assert store.read("session-1", now=0.0) is not None
    assert store.discard("session-1", _record(csrf="second")) is True
    assert store.read("session-1", now=0.0) is None


def test_a_store_recognises_equal_state_however_it_was_assembled(
    store: Any,
) -> None:
    """Two workers holding equal state must be holding the same session.

    A shared store compares serialized text, so text equality is standing in
    for state equality. Nothing guarantees two builds assemble the same
    mapping in the same order -- the key order of a dict literal is not a
    contract either of them agreed to -- and if it did not hold, the loser of
    a swap would be a worker that had not actually lost.
    """

    store.create("session-1", _record(), now=0.0)
    reordered = SessionRecord(
        subject="alice",
        expires_at=1_000.0,
        state={"roles": ["sales_clerk"], "csrf_token": "csrf-token", "version": 1},
    )

    assert store.replace("session-1", reordered, _record(csrf="next"), now=0.0) is True
    assert store.discard("session-1", _record(csrf="next")) is True


def test_a_store_ends_every_session_one_subject_holds(store: Any) -> None:
    store.create("a", _record(subject="alice"), now=0.0)
    store.create("b", _record(subject="alice"), now=0.0)
    store.create("c", _record(subject="bob"), now=0.0)

    assert store.delete_subject("alice") == 2
    assert store.read("a", now=0.0) is None
    assert store.read("b", now=0.0) is None
    assert store.read("c", now=0.0) is not None
    assert store.clear() == 1
    assert store.read("c", now=0.0) is None


def test_a_store_merges_a_change_into_one_subjects_sessions(store: Any) -> None:
    store.create("a", _record(subject="alice"), now=0.0)
    store.create("c", _record(subject="bob"), now=0.0)

    assert store.update_subject("alice", {"credential_stamp": "moved"}) == 1
    updated = store.read("a", now=0.0)
    assert updated is not None
    assert updated.state["credential_stamp"] == "moved"
    assert updated.state["csrf_token"] == "csrf-token"
    untouched = store.read("c", now=0.0)
    assert untouched is not None
    assert "credential_stamp" not in untouched.state


def test_a_store_evicts_the_least_recently_used_session_when_full(
    store: Any,
) -> None:
    """Capacity drops the session nobody is using, not the oldest one.

    The clock advances between calls because that is what a clock does, and
    because a shared store orders by the time it recorded: sessions touched at
    the same instant have no order to be least-recent in.
    """

    for index in range(4):
        store.create(f"session-{index}", _record(), now=float(index))
    store.read("session-0", now=10.0)

    store.create("session-4", _record(), now=11.0)

    # `session-1` was the oldest touch once `session-0` was read again.
    assert store.read("session-1", now=12.0) is None
    assert store.read("session-0", now=12.0) is not None
    assert store.read("session-4", now=12.0) is not None


def test_a_session_this_build_cannot_read_ends_rather_than_being_guessed_at(
    tmp_path: Path,
) -> None:
    """A shared store holds rows this process did not write.

    An older build's state version, a value somebody edited, a column that
    changed on the way through a driver: the session ends, and it does not end
    as a session with no roles.
    """

    users = _users(tmp_path / "users.sqlite3")
    sessions = InMemorySessionStore()
    worker = _worker(users, sessions)
    login = worker.login(username="alice", password=PASSWORD)

    stored = sessions.read(login.session_id, now=0.0)
    assert stored is not None
    sessions.create(
        login.session_id,
        stored.merged({"version": stored.state["version"] + 1}),
        now=0.0,
    )

    assert worker.authenticate_session(login.session_id) is None
    assert sessions.read(login.session_id, now=0.0) is None


def test_a_store_counts_only_the_failures_inside_the_window(store: Any) -> None:
    assert store.count_failures("alice", now=0.0, window=60.0) == 0
    store.record_failure("alice", now=0.0, window=60.0, limit=5)
    store.record_failure("alice", now=1.0, window=60.0, limit=5)

    assert store.count_failures("alice", now=2.0, window=60.0) == 2
    assert store.count_failures("bob", now=2.0, window=60.0) == 0
    # The window slides rather than being cleared, so waiting is what lifts it.
    assert store.count_failures("alice", now=70.0, window=60.0) == 0


def test_a_store_stops_counting_failures_once_the_limit_is_reached(
    store: Any,
) -> None:
    """The bucket is a decision, not a log; it must not grow with the attack."""

    for _attempt in range(50):
        store.record_failure("alice", now=0.0, window=60.0, limit=3)

    assert store.count_failures("alice", now=0.0, window=60.0) == 3


def test_a_store_forgets_the_failures_when_the_password_is_right(
    store: Any,
) -> None:
    store.record_failure("alice", now=0.0, window=60.0, limit=5)
    store.clear_failures("alice")

    assert store.count_failures("alice", now=0.0, window=60.0) == 0


def test_a_managed_database_carries_the_session_store_like_the_cursor_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No second URL to configure: it rides the engine already opened.

    The cursor and action-execution stores made this choice first, and it is
    the one that keeps `--create-schema` a single instruction. A database TIDE
    does not own gets no store, because it may not create a table there.
    """

    url = f"sqlite+pysqlite:///{(tmp_path / 'application.db').as_posix()}"
    monkeypatch.setenv("SHARED_SESSION_DATABASE_URL", url)
    model = compile_project(ROOT / "applications" / "invoicing")
    storage = open_run_storage(
        argparse.Namespace(
            database_env="SHARED_SESSION_DATABASE_URL",
            create_schema=True,
        ),
        model,
        purpose="Test",
    )

    assert storage is not None
    try:
        assert storage.session_store is not None
        # Created by --create-schema alongside everything else, and validated
        # on every later start rather than assumed.
        storage.session_store.validate_schema()
        assert storage.session_store.shared is True
    finally:
        storage.dispose()


def _database_without_session_tables(tmp_path: Path) -> tuple[str, Any]:
    """A managed database as it was before the session tables existed."""

    url = f"sqlite+pysqlite:///{(tmp_path / 'before.db').as_posix()}"
    model = compile_project(ROOT / "applications" / "invoicing")
    repository = SQLAlchemyRepository(model, url)
    repository.create_schema()
    # Deliberately not `framework_stores`: this builds the schema as it was
    # before the session store existed, so it names the two stores of that
    # moment on purpose. Routing it through the current list would make it
    # complete, and a fixture that cannot be missing anything cannot show that
    # something missing is noticed.
    SQLAlchemyCursorStore(repository.engine, mode="managed").create_schema()
    SQLAlchemyActionExecutionStore(repository.engine, mode="managed").create_schema()
    repository.dispose()
    return url, model


def test_a_migration_proposal_names_the_session_tables_it_is_missing(
    tmp_path: Path,
) -> None:
    """`tide db diff` enumerates the framework tables by hand.

    So does the backup check below. Neither is derived from anything, which is
    the failure mode this repository keeps meeting: adding a store to the
    schema and not to the two places that list the schema leaves an operator
    upgrading an existing database with a proposal that says nothing is
    missing, right up until the server refuses to start.
    """

    url, model = _database_without_session_tables(tmp_path)
    proposal = propose_migration(model, url)

    created = {
        change.object_name
        for change in proposal.changes
        if change.operation == "create_table"
    }
    assert "tide_browser_session" in created
    assert "tide_login_failure" in created


def test_a_backup_missing_the_session_tables_is_not_compatible(
    tmp_path: Path,
) -> None:
    url, model = _database_without_session_tables(tmp_path)
    repository = SQLAlchemyRepository(model, url)
    try:
        issues = SQLAlchemySessionStore(
            repository.engine, mode="managed"
        ).schema_issues()
    finally:
        repository.dispose()

    assert {issue.object_name for issue in issues} == {
        "tide_browser_session",
        "tide_login_failure",
    }
    with pytest.raises(DatabaseBackupError, match="not compatible"):
        _validate_application_backup(model, tmp_path / "before.db")


def test_a_shared_session_outlives_the_store_object_that_wrote_it(
    tmp_path: Path,
) -> None:
    """A restart is a new store over the same rows, which is this."""

    url = f"sqlite+pysqlite:///{(tmp_path / 'sessions.db').as_posix()}"
    first = SQLAlchemySessionStore(url, mode="managed")
    first.create_schema()
    first.create("session-1", _record(), now=0.0)
    first.dispose()

    second = SQLAlchemySessionStore(url)
    try:
        second.validate_schema()
        assert second.read("session-1", now=0.0) == _record()
    finally:
        second.dispose()


def test_a_shared_store_writes_down_no_session_identifier(tmp_path: Path) -> None:
    """The identifier is a bearer credential, so only its digest is kept.

    A database backup, a replica or a stray query would otherwise hand over
    every live session, exactly as it would every live query cursor.
    """

    url = f"sqlite+pysqlite:///{(tmp_path / 'sessions.db').as_posix()}"
    store = SQLAlchemySessionStore(url, mode="managed")
    store.create_schema()
    try:
        store.create("the-secret-session-id", _record(), now=0.0)
        with store.engine.connect() as connection:
            rows = connection.execute(select(store.session_table)).mappings().all()
        stored = "\n".join(str(value) for row in rows for value in row.values())
    finally:
        store.dispose()

    assert "the-secret-session-id" not in stored
    assert "alice" in stored


def test_a_shared_store_refuses_to_create_schema_in_legacy_mode(
    tmp_path: Path,
) -> None:
    url = f"sqlite+pysqlite:///{(tmp_path / 'sessions.db').as_posix()}"
    store = SQLAlchemySessionStore(url)
    try:
        with pytest.raises(SchemaManagementError, match="legacy"):
            store.create_schema()
        with pytest.raises(SchemaCompatibilityError):
            store.validate_schema()
    finally:
        store.dispose()


def test_two_workers_on_one_database_agree_about_who_is_signed_in(
    tmp_path: Path,
) -> None:
    """The whole point, with a real process boundary between the two.

    Separate engines over one file is what two workers have: no object is
    shared between them, only the rows.
    """

    users = _users(tmp_path / "users.sqlite3")
    url = f"sqlite+pysqlite:///{(tmp_path / 'sessions.db').as_posix()}"
    schema_owner = SQLAlchemySessionStore(url, mode="managed")
    schema_owner.create_schema()
    schema_owner.dispose()

    left_store = SQLAlchemySessionStore(url)
    right_store = SQLAlchemySessionStore(url)
    try:
        left = _worker(users, left_store)
        right = _worker(users, right_store)

        login = left.login(username="alice", password=PASSWORD)
        assert right.authenticate_session(login.session_id) is not None

        # Signing out on the worker that did not issue it still signs out.
        right.end_session(login.session_id)
        assert left.authenticate_session(login.session_id) is None

        again = right.login(username="alice", password=PASSWORD)
        assert left.revoke_user("alice") == 1
        assert right.authenticate_session(again.session_id) is None
    finally:
        left_store.dispose()
        right_store.dispose()
