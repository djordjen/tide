"""`tide db inspect` proposes legacy metadata from a schema it does not own."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.dialects import mssql

from tide import compile_project
from tide.cli import main
from tide.data import SQLAlchemyRepository, inspect_schema, render_project
from tide.data import inspection

LEGACY_DDL = (
    "CREATE TABLE EMPLOYEE_MASTER ("
    "EMPLOYEE_NO INTEGER PRIMARY KEY, "
    "DISPLAY_NAME VARCHAR(120) NOT NULL, "
    "HIRED_ON DATE)",
    "CREATE TABLE CUSTOMER_MASTER ("
    "CUSTOMER_NO INTEGER PRIMARY KEY, "
    "DISPLAY_NAME VARCHAR(120) NOT NULL, "
    "CREDIT_LIMIT NUMERIC(12,2), "
    "IS_ACTIVE BOOLEAN NOT NULL, "
    "OWNER_EMPLOYEE_NO INTEGER, "
    "FOREIGN KEY (OWNER_EMPLOYEE_NO) REFERENCES EMPLOYEE_MASTER(EMPLOYEE_NO))",
    # Neither of these can be proposed under schema v0.1.
    "CREATE TABLE ORDER_LINE ("
    "ORDER_NO INTEGER NOT NULL, LINE_NO INTEGER NOT NULL, QTY INTEGER, "
    "PRIMARY KEY (ORDER_NO, LINE_NO))",
    "CREATE TABLE NOTE_LOG (BODY TEXT)",
)


@pytest.fixture
def legacy_url(tmp_path: Path) -> str:
    url = f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(url)
    with engine.begin() as connection:
        for statement in LEGACY_DDL:
            connection.exec_driver_sql(statement)
    engine.dispose()
    return url


def test_the_proposal_maps_the_database_it_was_read_from(
    legacy_url: str, tmp_path: Path
) -> None:
    """The whole point: inspect, compile, and validate against the same schema.

    A proposal that compiles but does not match the database it came from would
    be worse than no proposal, because it looks finished.
    """

    project = tmp_path / "proposed"
    for path, text in render_project(
        inspect_schema(legacy_url), application="Legacy CRM"
    ).items():
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    model = compile_project(project)
    assert str(model.database["mode"]) == "legacy"

    repository = SQLAlchemyRepository(model, legacy_url)
    repository.validate_schema()
    repository.dispose()


def test_a_key_shape_v0_1_cannot_map_is_reported_not_guessed(
    legacy_url: str,
) -> None:
    proposal = inspect_schema(legacy_url)

    assert sorted(entity.table for entity in proposal.entities) == [
        "CUSTOMER_MASTER",
        "EMPLOYEE_MASTER",
    ]
    reasons = {skipped.name: skipped.reason for skipped in proposal.skipped}
    assert "composite primary key" in reasons["ORDER_LINE"]
    assert "no primary key" in reasons["NOTE_LOG"]


def test_a_foreign_key_becomes_a_reference_to_the_entity_it_points_at(
    legacy_url: str,
) -> None:
    customer = next(
        entity
        for entity in inspect_schema(legacy_url).entities
        if entity.table == "CUSTOMER_MASTER"
    )
    declarations = dict(customer.fields)

    assert "type: reference" in declarations["owner_employee_no"]
    assert "target: legacy.EmployeeMaster" in declarations["owner_employee_no"]
    assert "storage: OWNER_EMPLOYEE_NO" in declarations["owner_employee_no"]
    assert declarations["customer_no"].startswith("{type: integer, primary_key: true")
    assert customer.display == "display_name"


def test_inspection_never_writes_to_the_database_it_read(legacy_url: str) -> None:
    """Read-only is a property of the statements issued, not of the intent."""

    engine = create_engine(legacy_url)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.strip().upper())

    inspect_schema(engine)

    assert statements, "no statement was captured, so nothing was observed"
    assert not any(
        statement.startswith(
            ("CREATE ", "ALTER ", "DROP ", "INSERT ", "UPDATE ", "DELETE ")
        )
        for statement in statements
    ), f"inspection wrote to the database: {statements}"
    engine.dispose()


@pytest.mark.parametrize(
    ("column_type", "expected"),
    [
        (mssql.MONEY(), "{type: decimal, precision: 19, scale: 4}"),
        (mssql.SMALLMONEY(), "{type: decimal, precision: 10, scale: 4}"),
        (mssql.UNIQUEIDENTIFIER(), "{type: uuid}"),
    ],
)
def test_sql_server_types_are_proposed_as_the_types_that_validate(
    column_type: object, expected: str
) -> None:
    """The proposal has to agree with the checker that will later judge it.

    Both read the money capacities through `_as_comparable`, so proposing a
    decimal the schema check would then reject is not a mistake either one can
    make on its own.
    """

    parts = inspection._type_parts(column_type)

    assert parts is not None
    assert "{" + ", ".join(parts) + "}" == expected


def test_the_command_writes_a_reviewable_project_without_overwriting(
    legacy_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("TIDE_DATABASE_URL", legacy_url)
    destination = tmp_path / "adopted"

    assert (
        main(["db", "inspect", "--database-env", "--output", str(destination)]) == 0
    )
    first = capsys.readouterr()
    assert "Not proposed -- ORDER_LINE" in first.err
    assert (destination / "tide.yaml").is_file()
    assert (destination / "models" / "customermaster.yaml").is_file()

    # A second run must not quietly discard hand edits made after the first.
    assert (
        main(["db", "inspect", "--database-env", "--output", str(destination)]) == 1
    )
    assert "refusing to overwrite" in capsys.readouterr().err


def test_the_command_reports_a_missing_url_variable(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.delenv("TIDE_INSPECT_URL", raising=False)

    assert main(["db", "inspect", "--database-env", "TIDE_INSPECT_URL"]) == 1
    assert "'TIDE_INSPECT_URL' is not set" in capsys.readouterr().err


def test_an_empty_schema_is_a_failure_not_an_empty_project(tmp_path: Path) -> None:
    """Writing a project with no entities would look like it worked."""

    url = f"sqlite+pysqlite:///{tmp_path / 'empty.db'}"
    create_engine(url).dispose()
    assert not inspect(create_engine(url)).get_table_names()
    os.environ["TIDE_EMPTY_URL"] = url
    try:
        assert main(["db", "inspect", "--database-env", "TIDE_EMPTY_URL"]) == 1
    finally:
        del os.environ["TIDE_EMPTY_URL"]
