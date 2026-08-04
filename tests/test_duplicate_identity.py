"""Two records may not answer to the same identity, and the refusal must say so.

Writing a new record whose primary key another record already holds reported
`ConcurrencyError(None, None)` from the document-shaped repository: a
stale-version error, for a collision that has nothing to do with versions,
carrying a message -- "expected None, current None" -- that names neither the
record nor the number. The SQL repository reported the same collision as an
unclassified constraint violation. Neither said what had happened, and the two
did not agree with each other.

Clients cannot supply a primary key; the service refuses that write before it
reaches a repository. So arriving here means identity allocation handed out a
number twice, which is a fault in the server and stays one -- but a legible
one, which a 412 "record version changed" was not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tide import compile_project
from tide.data import (
    DuplicateIdentityError,
    InMemoryRepository,
    SQLAlchemyRepository,
    WriteIntegrityError,
)
from tide.runtime.errors import ConcurrencyError

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_a_taken_identity_is_named_in_the_refusal(kind: str, tmp_path: Path) -> None:
    repository = _repository(kind, tmp_path)
    _write(repository, identity=1, code="FIRST")

    with pytest.raises(DuplicateIdentityError) as raised:
        _write(repository, identity=1, code="SECOND")

    assert raised.value.identity == 1
    assert raised.value.entity == "crm.Customer"
    assert "crm.Customer" in str(raised.value)
    assert "1" in str(raised.value)


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_a_taken_identity_is_not_a_stale_version(kind: str, tmp_path: Path) -> None:
    """The old error told the caller someone else had edited the record.

    Nobody had. Reporting a collision as a version conflict sends a client
    down the refresh-and-retry path, which cannot resolve it: the identity is
    taken however many times the write is retried.
    """

    repository = _repository(kind, tmp_path)
    _write(repository, identity=1, code="FIRST")

    with pytest.raises(WriteIntegrityError) as raised:
        _write(repository, identity=1, code="SECOND")

    assert not isinstance(raised.value, ConcurrencyError)


def test_both_repositories_refuse_a_taken_identity_the_same_way(
    tmp_path: Path,
) -> None:
    """The point of two adapters is that swapping them changes nothing here."""

    refusals = []
    for kind in ("memory", "sql"):
        repository = _repository(kind, tmp_path)
        _write(repository, identity=1, code="FIRST")
        with pytest.raises(WriteIntegrityError) as raised:
            _write(repository, identity=1, code="SECOND")
        refusals.append(type(raised.value))

    assert refusals[0] is refusals[1]


def test_an_unrelated_constraint_keeps_its_own_error(tmp_path: Path) -> None:
    """Only the identity collision may be reported as one.

    A duplicate `code` is a constraint the caller can act on by choosing a
    different code. Relabelling it as a duplicate identity would name the
    wrong field, so the specific error has to be earned rather than assumed
    from the fact that some constraint failed.
    """

    repository = _repository("sql", tmp_path)
    _write(repository, identity=1, code="TAKEN")

    with pytest.raises(WriteIntegrityError) as raised:
        _write(repository, identity=2, code="TAKEN")

    assert not isinstance(raised.value, DuplicateIdentityError)


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_a_generated_identity_is_never_refused(kind: str, tmp_path: Path) -> None:
    repository = _repository(kind, tmp_path)

    stored = [_write(repository, code=f"GEN{index}") for index in range(3)]

    assert len({record["id"] for record in stored}) == 3


def _write(
    repository: Any, *, identity: Any = None, code: str = "DUP"
) -> dict[str, Any]:
    entity = compile_project(INVOICING).entity("crm.Customer")
    values: dict[str, Any] = {"code": code, "name": "Duplicate", "active": True}
    if identity is not None:
        values[entity.primary_key.name] = identity
    return repository.write(
        "crm.Customer",
        values,
        primary_key=entity.primary_key.name,
        version_field=None if entity.version_field is None else entity.version_field.name,
        expected_version=None,
        is_new=True,
    )


def _repository(kind: str, tmp_path: Path) -> Any:
    if kind == "memory":
        return InMemoryRepository()
    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(
        model, f"sqlite+pysqlite:///{(tmp_path / f'{kind}-identity.db').as_posix()}"
    )
    repository.create_schema()
    return repository
