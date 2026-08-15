"""Every framework table is created, validated, proposed and verified.

Four layers have to agree about the framework's own tables, and each of them
used to name the stores itself. The two that failed silently when the session
store was added are the reason this file exists: `tide db diff` reported no
differences against a database missing two tables, and backup verification
accepted it.

The assertion is the negative one -- drop a table and each layer must notice --
and it is parametrized over `framework_stores`, so it is a claim about whatever
that list contains rather than about the three stores in it today. A fourth
store is covered on the day it is added, without this file being edited.
"""

from __future__ import annotations

import argparse
from dataclasses import fields
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from tide import compile_project
from tide.data import SQLAlchemyRepository, propose_migration
from tide.data.backup import DatabaseBackupError, _validate_application_backup
from tide.data.framework_schema import FrameworkStores, framework_stores

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _framework_table_names() -> tuple[str, ...]:
    """Ask the one list what it holds, at collection time.

    A store emits no DDL when it is constructed, so an unused in-memory bind
    is enough to read the names off it. Restating them here would reintroduce
    exactly the hand-maintained list this file exists to retire.
    """

    stores = framework_stores("sqlite+pysqlite://")
    try:
        return tuple(table.name for table in stores.tables)
    finally:
        for store in stores.all:
            store.dispose()  # type: ignore[attr-defined]


FRAMEWORK_TABLES = _framework_table_names()


def _arguments(database_env: str, *, create_schema: bool) -> argparse.Namespace:
    return argparse.Namespace(database_env=database_env, create_schema=create_schema)


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A managed database with the whole schema, made the documented way."""

    from tide.cli.storage import open_run_storage

    database = tmp_path / "framework.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    monkeypatch.setenv("FRAMEWORK_DATABASE_URL", url)
    model = compile_project(INVOICING)
    storage = open_run_storage(
        _arguments("FRAMEWORK_DATABASE_URL", create_schema=True),
        model,
        purpose="Test",
    )
    assert storage is not None
    storage.dispose()
    return model, url, database


def test_the_iteration_covers_every_store_the_dataclass_declares() -> None:
    """The one assertion that cannot be derived from `all`, and must not be.

    Everything else in this file walks `FrameworkStores.all` -- the table
    list, the parametrization, the four layers. A store missing from `all` is
    therefore invisible to all of them: the list simply gets shorter and every
    remaining case passes, which is exactly what a first draft of this file
    did when the sabotage was tried. The independent source of truth is the
    declaration, so this compares `all` against `dataclasses.fields` and
    nothing in between.
    """

    stores = framework_stores("sqlite+pysqlite://")
    try:
        declared = [field.name for field in fields(FrameworkStores)]
        assert declared, "FrameworkStores declares no stores at all"
        assert list(stores.all) == [getattr(stores, name) for name in declared]
    finally:
        for store in stores.all:
            store.dispose()  # type: ignore[attr-defined]


def test_the_framework_owns_the_tables_this_file_thinks_it_does() -> None:
    """A guard on the guard: an empty list would pass every case below.

    `pytest.mark.parametrize` over nothing collects nothing and reports
    success, so the derived list needs one assertion that it is not empty and
    that it is the framework's rather than the application's.
    """

    assert len(FRAMEWORK_TABLES) >= 3
    assert all(name.startswith("tide_") for name in FRAMEWORK_TABLES)
    assert len(set(FRAMEWORK_TABLES)) == len(FRAMEWORK_TABLES)


@pytest.mark.parametrize("table_name", FRAMEWORK_TABLES)
def test_a_missing_framework_table_is_noticed_by_every_layer(
    table_name: str,
    managed: tuple[object, str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tide.cli.storage import open_run_storage

    model, url, database = managed
    engine = create_engine(url)
    try:
        # Layer 1: `--create-schema` made it. The fixture ran the ordinary
        # startup path, so this is the real instruction rather than a rebuild.
        assert table_name in set(inspect(engine).get_table_names())

        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE {table_name}"))

        # Layer 2: startup validates rather than assuming, and fails closed.
        assert (
            open_run_storage(
                _arguments("FRAMEWORK_DATABASE_URL", create_schema=False),
                model,
                purpose="Test",
            )
            is None
        )
        assert table_name in capsys.readouterr().err

        # Layer 3: `tide db diff` proposes it. This is the one that answered
        # "No schema differences detected" when the session store was added.
        proposal = propose_migration(model, url)
        assert table_name in {
            change.object_name
            for change in proposal.changes
            if change.operation == "create_table"
        }
        assert not proposal.clean

        # Layer 4: backup verification refuses it. This is the other one.
        with pytest.raises(DatabaseBackupError, match="not compatible"):
            _validate_application_backup(model, database)
    finally:
        engine.dispose()


def test_restoring_the_dropped_table_makes_every_layer_agree_again(
    managed: tuple[object, str, Path],
) -> None:
    """The control for the four refusals above.

    Each of them would also pass against a database that was broken for some
    other reason, or against layers that refuse everything. This asserts the
    positive direction once: with the schema whole, nothing complains.
    """

    model, url, database = managed
    repository = SQLAlchemyRepository(model, url)
    try:
        stores = framework_stores(repository.engine)
        assert stores.schema_issues() == ()
        stores.validate_schema()
        assert propose_migration(model, url).clean is True
        _validate_application_backup(model, database)
    finally:
        repository.dispose()
