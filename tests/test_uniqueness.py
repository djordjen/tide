"""Uniqueness is checked with a bounded question and guarded by the database.

The service used to hydrate every row of an entity, per unique field, on every
commit -- and to do it on a separate connection before the write transaction
opened, so the answer could already be stale by the time the row was written.
The database's unique index is the real guard; the service's job is to turn its
refusal into the same structured error the pre-check produces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tide import compile_project
from tide.data import (
    InMemoryRepository,
    QuerySpec,
    SQLAlchemyRepository,
    WriteIntegrityError,
)
from tide.runtime import Channel, Principal, RequestContext
from tide.runtime.errors import ValidationFailed
from tide.services import RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _context() -> RequestContext:
    return RequestContext(
        Principal("tests:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.REST,
        correlation_id="uniqueness",
    )


def _customer(code: str, email: str | None = None) -> dict[str, Any]:
    values: dict[str, Any] = {"code": code, "name": f"Customer {code}"}
    if email is not None:
        values["email"] = email
    return values


def _write(records: RecordsService, values: dict[str, Any]) -> None:
    records.commit(records.create("crm.Customer", _context(), values), _context())


class _ScanRefusingRepository(InMemoryRepository):
    """Fails the way an unbounded scan would once a table is large."""

    def all(self, entity: str) -> list[dict[str, Any]]:
        raise AssertionError(f"uniqueness must not hydrate every {entity} row")


class _RacingRepository(SQLAlchemyRepository):
    """Blinds exactly one pre-check, the way losing the race blinds it.

    The pre-check runs on its own connection before the write transaction
    opens, so a duplicate can land in the gap: it sees nothing, the write is
    the first thing to notice, and asking again afterwards finds it. Arming
    this for one call reproduces that window without threads.
    """

    blind = False

    def unique_conflict(
        self, entity: str, field: str, value: Any, *, exclude_identity: Any
    ) -> bool:
        if self.blind:
            self.blind = False
            return False
        return super().unique_conflict(
            entity, field, value, exclude_identity=exclude_identity
        )


class _AlwaysRefusingRepository(InMemoryRepository):
    """Refuses every write for a constraint the service does not model."""

    def write(self, entity: str, values: dict[str, Any], **kwargs: Any) -> Any:
        raise WriteIntegrityError(entity)


def test_a_constraint_that_is_not_a_duplicate_keeps_its_own_error() -> None:
    """Only a duplicate reads as validation.

    A foreign key or check constraint the service cannot name is not something
    the caller can fix by editing a field, and reporting it as one would send
    them looking for a conflict that is not there.
    """

    model = compile_project(INVOICING)
    records = RecordsService(model, _AlwaysRefusingRepository())

    with pytest.raises(WriteIntegrityError):
        _write(records, _customer("CONSTRAINED"))


def test_a_duplicate_is_refused_with_the_field_that_collides() -> None:
    model = compile_project(INVOICING)
    records = RecordsService(model, InMemoryRepository())
    _write(records, _customer("DUP"))

    with pytest.raises(ValidationFailed) as failure:
        _write(records, _customer("DUP"))

    assert [(issue.rule, issue.fields) for issue in failure.value.issues] == [
        ("unique", ("code",))
    ]


def test_checking_uniqueness_does_not_hydrate_the_whole_entity() -> None:
    """The scan cost grew with the table and bought only a nicer message."""

    model = compile_project(INVOICING)
    records = RecordsService(model, _ScanRefusingRepository())

    _write(records, _customer("BOUNDED"))


def test_a_null_value_never_collides_with_another_null() -> None:
    """SQL uniqueness ignores NULL, and two customers may omit an email."""

    model = compile_project(INVOICING)
    records = RecordsService(model, InMemoryRepository())

    _write(records, _customer("ONE"))
    _write(records, _customer("TWO"))


def test_a_record_does_not_collide_with_itself_on_update() -> None:
    model = compile_project(INVOICING)
    records = RecordsService(model, InMemoryRepository())
    _write(records, _customer("SELF", "self@example.com"))

    stored = records.query("crm.Customer", QuerySpec(), _context())
    session = records.begin_edit("crm.Customer", stored[0]["id"], _context())
    session.values["name"] = "Renamed"

    assert records.commit(session, _context()) is not None


def test_the_database_refusing_a_racing_duplicate_reads_as_validation(
    tmp_path: Path,
) -> None:
    """The pre-check runs before the write transaction, so it can be stale.

    A raw `IntegrityError` reaching the caller is a 500 for what is an ordinary
    "that code is taken" -- the same condition the pre-check reports cleanly.
    """

    model = compile_project(INVOICING)
    url = f"sqlite+pysqlite:///{(tmp_path / 'race.db').as_posix()}"
    repository = _RacingRepository(model, url)
    repository.create_schema()
    records = RecordsService(model, repository)
    _write(records, _customer("RACE"))

    repository.blind = True
    with pytest.raises(ValidationFailed) as failure:
        _write(records, _customer("RACE"))

    assert repository.blind is False, "the pre-check must have been the blind one"

    assert [issue.fields for issue in failure.value.issues] == [("code",)]
