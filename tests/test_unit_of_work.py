"""What a scope promises, asked of both adapters in the same words.

Every repository call used to open its own transaction, so an operation that
wrote two records committed them separately and a failure in between left the
first one standing. These are the contract tests for the scope that fixes
that -- and the point of running them over both adapters is that a document
store undoing a dictionary and a database rolling back a transaction have to
be indistinguishable from up here.
"""

from __future__ import annotations

from pathlib import Path
from threading import Barrier, Thread
from typing import Any, Iterator

import pytest

from tide import compile_project
from tide.data import (
    InMemoryRepository,
    SQLAlchemyRepository,
    UnitOfWorkBypassed,
    UnitOfWorkClosed,
    UnitOfWorkFailed,
)
from tide.runtime import NotFoundError

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def customer(identity: int, code: str) -> dict[str, Any]:
    return {
        "id": identity,
        "code": code,
        "name": f"Customer {identity}",
        "email": None,
        "active": True,
        "invoices": [],
    }


@pytest.fixture(params=("memory", "sql"))
def repository(request: pytest.FixtureRequest) -> Iterator[Any]:
    model = compile_project(INVOICING)
    store: InMemoryRepository | SQLAlchemyRepository
    if request.param == "memory":
        store = InMemoryRepository()
    else:
        store = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
        store.create_schema()
    yield store
    if isinstance(store, SQLAlchemyRepository):
        store.dispose()


def codes(store: Any) -> list[str]:
    return sorted(record["code"] for record in store.all("crm.Customer"))


def write(unit: Any, identity: int, code: str) -> dict[str, Any]:
    return unit.write(
        "crm.Customer",
        customer(identity, code),
        primary_key="id",
        version_field=None,
        expected_version=None,
        is_new=True,
    )


def test_two_writes_in_one_scope_arrive_together(repository: Any) -> None:
    with repository.transaction() as unit:
        write(unit, 1, "FIRST")
        write(unit, 2, "SECOND")

    assert codes(repository) == ["FIRST", "SECOND"]


def test_a_failure_between_two_writes_leaves_neither(repository: Any) -> None:
    with pytest.raises(RuntimeError, match="between the writes"):
        with repository.transaction() as unit:
            write(unit, 1, "FIRST")
            raise RuntimeError("something failed between the writes")

    # This is the whole issue in one assertion: before the scope existed the
    # first write had already committed on its own connection by this point.
    assert codes(repository) == []


def test_a_scope_reads_back_what_it_has_not_committed_yet(repository: Any) -> None:
    with repository.transaction() as unit:
        write(unit, 1, "FIRST")

        loaded = unit.get("crm.Customer", 1)

        assert loaded["code"] == "FIRST"
        # A read on a fresh connection cannot see an uncommitted row, so an
        # operation that writes and then reads back would find nothing.
        assert unit.exists("crm.Customer", 1)


def test_going_round_the_scope_is_refused_rather_than_defined(
    repository: Any,
) -> None:
    """Written expecting the two adapters to agree; they did not.

    The document store holds one lock and one snapshot, so a write that
    slipped past on the same thread was rolled back with the scope. The
    database handed out a second connection -- except that SQLite in memory
    gave back the same one through its pool, so it joined instead. Three
    answers, so the call is refused.
    """

    with pytest.raises(UnitOfWorkBypassed):
        with repository.transaction() as unit:
            write(unit, 1, "INSIDE")
            write(repository, 2, "BYPASS")

    assert codes(repository) == []


def test_concurrent_sql_scopes_keep_their_bypass_ownership_separate(
    tmp_path: Path,
) -> None:
    """One request's owner marker must not overwrite another request's."""

    database = (tmp_path / "transactions.sqlite3").as_posix()
    store = SQLAlchemyRepository(
        compile_project(INVOICING),
        f"sqlite+pysqlite:///{database}",
    )
    store.create_schema()
    entered = Barrier(2)
    checked = Barrier(2)
    refused: list[bool] = []
    errors: list[BaseException] = []

    def transact() -> None:
        try:
            with store.transaction():
                entered.wait(timeout=5)
                try:
                    store.all("crm.Customer")
                except UnitOfWorkBypassed:
                    refused.append(True)
                else:
                    refused.append(False)
                checked.wait(timeout=5)
        except BaseException as error:
            errors.append(error)

    requests = [Thread(target=transact) for _ in range(2)]
    try:
        for request in requests:
            request.start()
        for request in requests:
            request.join(5)

        assert all(not request.is_alive() for request in requests)
        assert errors == []
        assert sorted(refused) == [True, True]
    finally:
        store.dispose()


def test_a_swallowed_write_failure_still_condemns_the_scope(
    repository: Any,
) -> None:
    with pytest.raises(UnitOfWorkFailed):
        with repository.transaction() as unit:
            write(unit, 1, "FIRST")
            try:
                write(unit, 1, "DUPLICATE")
            except Exception:
                # Exactly the shape that would otherwise commit a partial
                # write: there is no savepoint to undo the failed one with.
                pass

    assert codes(repository) == []


def test_a_failed_read_leaves_the_scope_alone(repository: Any) -> None:
    with repository.transaction() as unit:
        write(unit, 1, "FIRST")
        with pytest.raises(NotFoundError):
            unit.get("crm.Customer", 99)

    # A read that found nothing changed nothing, so a caller who means to
    # handle a NotFoundError can still handle it.
    assert codes(repository) == ["FIRST"]


def test_a_condemned_scope_refuses_the_work_after_it(repository: Any) -> None:
    seen: list[str] = []
    with pytest.raises(UnitOfWorkFailed):
        with repository.transaction() as unit:
            write(unit, 1, "FIRST")
            try:
                write(unit, 1, "DUPLICATE")
            except Exception:
                pass
            seen.append("still running")

    assert seen == ["still running"]
    assert codes(repository) == []


def test_a_nested_scope_joins_rather_than_committing_early(
    repository: Any,
) -> None:
    with pytest.raises(RuntimeError):
        with repository.transaction() as outer:
            with outer.transaction() as inner:
                write(inner, 1, "INNER")
            # The inner scope has exited. If it had committed on its own,
            # this rollback could not reach the row it wrote -- which is what
            # makes an operation built from two smaller ones one commit.
            write(outer, 2, "OUTER")
            raise RuntimeError("roll everything back")

    assert codes(repository) == []


def test_a_failure_inside_a_nested_scope_condemns_the_outer_one(
    repository: Any,
) -> None:
    with pytest.raises(UnitOfWorkFailed):
        with repository.transaction() as outer:
            write(outer, 1, "FIRST")
            with pytest.raises(RuntimeError):
                with outer.transaction() as inner:
                    write(inner, 2, "SECOND")
                    raise RuntimeError("inner failed")

    assert codes(repository) == []


def test_a_unit_refuses_to_work_after_its_scope_has_ended(
    repository: Any,
) -> None:
    with repository.transaction() as unit:
        write(unit, 1, "FIRST")
    escaped = unit

    with pytest.raises(UnitOfWorkClosed):
        write(escaped, 2, "TOO LATE")
    with pytest.raises(UnitOfWorkClosed):
        escaped.get("crm.Customer", 1)

    # The write is refused rather than quietly landing outside the atomicity
    # its caller believed it had.
    assert codes(repository) == ["FIRST"]


def test_a_delete_and_a_write_roll_back_together(repository: Any) -> None:
    repository.seed("crm.Customer", [customer(1, "DOOMED")])

    with pytest.raises(RuntimeError):
        with repository.transaction() as unit:
            unit.delete(
                "crm.Customer",
                1,
                primary_key="id",
                version_field=None,
                expected_version=None,
            )
            write(unit, 2, "REPLACEMENT")
            raise RuntimeError("fail after both")

    assert codes(repository) == ["DOOMED"]


def test_a_write_callback_still_runs_inside_the_scope(repository: Any) -> None:
    """`on_written` was the single-write version of this, and still works.

    It is the reason the audit fix in #18 was possible at all, so a scope
    that broke it would have traded one atomicity guarantee for another.
    """

    seen: list[tuple[Any, str]] = []

    with pytest.raises(RuntimeError):
        with repository.transaction() as unit:
            write(unit, 1, "FIRST")
            unit.write(
                "crm.Customer",
                customer(2, "SECOND"),
                primary_key="id",
                version_field=None,
                expected_version=None,
                is_new=True,
                on_written=lambda connection, stored: seen.append(
                    (connection, str(stored["code"]))
                ),
            )
            raise RuntimeError("roll the scope back")

    assert [code for _connection, code in seen] == ["SECOND"]
    assert codes(repository) == []
