"""Nested-write and orphan-delete contracts, on shapes invoicing cannot express.

The bundled invoicing application has one writable collection whose child owns
no field policy, no system-written field, no collection of its own, and nothing
referencing it. The `inspection` fixture supplies each of those, so the rules
that only apply to child records can be stated here rather than assumed.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import pytest

from tide import compile_project
from tide.data import InMemoryRepository, SQLAlchemyRepository
from tide.runtime import (
    AuthorizationError,
    Channel,
    DeleteRestricted,
    ImmutableFieldError,
    Principal,
    RequestContext,
)
from tide.security import SecurityEngine
from tide.services import QuerySpec, RecordsService

ROOT = Path(__file__).parents[1]
INSPECTION = ROOT / "tests" / "fixtures" / "valid" / "inspection"


def context(*roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal("user:tester", roles=frozenset(roles)),
        channel=Channel.TUI,
    )


def _records(kind: str) -> Iterator[RecordsService]:
    model = compile_project(INSPECTION)
    repository: InMemoryRepository | SQLAlchemyRepository
    if kind == "memory":
        repository = InMemoryRepository()
    else:
        repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
        repository.create_schema()
    yield RecordsService(model, repository, SecurityEngine(model))
    if isinstance(repository, SQLAlchemyRepository):
        repository.dispose()


@pytest.fixture(params=("memory", "sql"))
def records(request: pytest.FixtureRequest) -> Iterator[RecordsService]:
    yield from _records(request.param)


@pytest.fixture
def sql_records() -> Iterator[RecordsService]:
    """A relational repository, for contracts that need children to be rows."""

    yield from _records("sql")


def step(title: str = "Check pressure", **overrides: Any) -> dict[str, Any]:
    return {"title": title, "readings": [], **overrides}


def inspection(
    records: RecordsService,
    request_context: RequestContext,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    return records.commit(
        records.create(
            "inspect.Inspection",
            request_context,
            {"reference": "INS-1", "steps": steps},
        ),
        request_context,
    )


def test_commit_refuses_a_system_written_field_on_a_child(records) -> None:
    """`write: system` has to mean the same thing on a line as on a record."""

    inspector = context("inspector")

    with pytest.raises(ImmutableFieldError):
        inspection(records, inspector, [step(position=99)])


def test_commit_refuses_a_child_field_the_principal_may_not_write(records) -> None:
    """A field policy on a child is authorization, not decoration.

    `inspect.Step.note` requires `inspect.note.write`; the inspector role is
    deliberately granted everything except that.
    """

    with pytest.raises(AuthorizationError):
        inspection(records, context("inspector"), [step(note="private")])


def test_commit_writes_a_guarded_child_field_for_a_permitted_principal(
    records,
) -> None:
    """The same field is writable once the principal actually holds the grant."""

    made = inspection(records, context("lead"), [step(note="checked by lead")])

    assert [item["note"] for item in made["steps"]] == ["checked by lead"]


def test_commit_keeps_accepting_an_unchanged_guarded_child_field(records) -> None:
    """Echoing a value back is not a write, or every renderer would break.

    Clients reload a record and send the whole collection again, so a field the
    caller may not write must still round-trip untouched.
    """

    lead = context("lead")
    made = inspection(records, lead, [step(note="written by lead")])

    inspector = context("inspector")
    edit = records.begin_edit("inspect.Inspection", made["id"], inspector)
    steps = deepcopy(list(edit.values["steps"]))
    steps[0]["title"] = "Retitled by inspector"
    edit.set("steps", steps)
    updated = records.commit(edit, inspector)

    assert [item["title"] for item in updated["steps"]] == ["Retitled by inspector"]
    assert [item["note"] for item in updated["steps"]] == ["written by lead"]


def test_orphaning_a_child_another_record_restricts_names_the_relationship(
    sql_records,
) -> None:
    """Removing a child runs its own reference contract, not a bare delete.

    A signoff restricts its step, so dropping that step from the inspection has
    to fail the way an ordinary delete would rather than surfacing the
    database's foreign-key error.
    """

    lead = context("lead")
    made = inspection(sql_records, lead, [step("Keep"), step("Signed")])
    signed = made["steps"][1]["id"]
    sql_records.commit(
        sql_records.create(
            "inspect.Signoff", lead, {"signed_by": "QA", "step": signed}
        ),
        lead,
    )

    edit = sql_records.begin_edit("inspect.Inspection", made["id"], lead)
    keeping = [
        item for item in deepcopy(list(edit.values["steps"])) if item["id"] != signed
    ]
    edit.set("steps", keeping)

    with pytest.raises(DeleteRestricted) as caught:
        sql_records.commit(edit, lead)

    assert "inspect.Signoff" in str(caught.value)


def test_orphaning_a_child_removes_the_rows_it_owns(sql_records) -> None:
    """A step taken out of an inspection takes its own readings with it."""

    lead = context("lead")
    made = inspection(
        sql_records,
        lead,
        [
            step("Keep"),
            step("Drop", readings=[{"label": "psi", "value": Decimal("1.50")}]),
        ],
    )

    edit = sql_records.begin_edit("inspect.Inspection", made["id"], lead)
    edit.set("steps", deepcopy(list(edit.values["steps"]))[:1])
    sql_records.commit(edit, lead)

    assert sql_records.query("inspect.Reading", QuerySpec(), lead) == []
