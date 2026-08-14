"""`tide db inspect` proposes legacy metadata from a schema it does not own."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.dialects import mssql

from tide import compile_project
from tide.cli import main
from tide.compiler.normalized import ApplicationModel
from tide.data import SQLAlchemyRepository, inspect_schema, render_project
from tide.data import inspection
from tide.data.inspection import (
    MAX_COLLECTION_CHAIN,
    InspectionProposal,
    synthesize_collections,
)

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

TANGLED_DDL = (
    # No field type carries a binary key, so this table cannot be proposed --
    # and the table below points a foreign key straight at it.
    "CREATE TABLE ATTACHMENT_STORE (STORE_KEY BLOB PRIMARY KEY, LABEL VARCHAR(40))",
    "CREATE TABLE CUSTOMER_ATTACHMENT ("
    "ATTACHMENT_NO INTEGER PRIMARY KEY, "
    "CAPTION VARCHAR(80) NOT NULL, "
    "STORE_KEY BLOB, "
    "FOREIGN KEY (STORE_KEY) REFERENCES ATTACHMENT_STORE(STORE_KEY))",
)


def _database(tmp_path: Path, name: str, statements: tuple[str, ...]) -> str:
    url = f"sqlite+pysqlite:///{tmp_path / name}"
    engine = create_engine(url)
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
    engine.dispose()
    return url


@pytest.fixture
def legacy_url(tmp_path: Path) -> str:
    return _database(tmp_path, "legacy.db", LEGACY_DDL)


@pytest.fixture
def tangled_url(tmp_path: Path) -> str:
    return _database(tmp_path, "tangled.db", TANGLED_DDL)


@pytest.fixture
def paired_url(tmp_path: Path) -> str:
    """Two keys from one table into another, plus one into its own table."""

    return _database(
        tmp_path,
        "paired.db",
        (
            "CREATE TABLE DEPOT ("
            "DEPOT_NO INTEGER PRIMARY KEY, "
            "NAME VARCHAR(40) NOT NULL, "
            "PARENT_DEPOT_NO INTEGER REFERENCES DEPOT(DEPOT_NO))",
            "CREATE TABLE ROUTE_LEG ("
            "LEG_NO INTEGER PRIMARY KEY, "
            "ORIGIN_NO INTEGER REFERENCES DEPOT(DEPOT_NO), "
            "DESTINATION_NO INTEGER REFERENCES DEPOT(DEPOT_NO))",
        ),
    )


def _compiled(
    proposal: InspectionProposal,
    tmp_path: Path,
    name: str = "proposed",
    *,
    runnable: bool = False,
) -> ApplicationModel:
    """Write a proposal out and compile it, the way a reader would."""

    project = tmp_path / name
    documents = render_project(
        proposal, application="Legacy CRM", runnable=runnable
    )
    for path, text in documents.items():
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return compile_project(project)


def test_the_proposal_maps_the_database_it_was_read_from(
    legacy_url: str, tmp_path: Path
) -> None:
    """The whole point: inspect, compile, and validate against the same schema.

    A proposal that compiles but does not match the database it came from would
    be worse than no proposal, because it looks finished.
    """

    model = _compiled(inspect_schema(legacy_url), tmp_path)
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


def test_a_selected_table_never_references_one_the_selection_left_out(
    legacy_url: str, tmp_path: Path
) -> None:
    """Narrowing the reflection must still produce something that compiles.

    CUSTOMER_MASTER holds a foreign key into EMPLOYEE_MASTER. Proposing the
    first without the second cannot emit a reference to an entity that is not
    there; the column itself still exists in the database, so it is mapped as
    the plain column it physically is.
    """

    proposal = inspect_schema(legacy_url, tables=("CUSTOMER_MASTER",))
    model = _compiled(proposal, tmp_path)

    assert list(model.entities) == ["legacy.CustomerMaster"]
    field = model.entity("legacy.CustomerMaster").field("owner_employee_no")
    assert field.metadata["type"] == "integer"
    assert field.metadata["column"] == "OWNER_EMPLOYEE_NO"

    demoted = {item.name: item for item in proposal.demoted}
    assert demoted["CUSTOMER_MASTER.OWNER_EMPLOYEE_NO"].target == "EMPLOYEE_MASTER"
    assert proposal.deselected == ("EMPLOYEE_MASTER", "NOTE_LOG", "ORDER_LINE")


def test_a_reference_to_a_table_that_cannot_be_mapped_is_demoted_too(
    tangled_url: str, tmp_path: Path
) -> None:
    """The same hole opens without any selection at all.

    ATTACHMENT_STORE is keyed by a column no field type carries, so it is not
    proposed -- and the foreign key pointing at it must not become a reference
    to an entity the proposal never contained.
    """

    proposal = inspect_schema(tangled_url)
    model = _compiled(proposal, tmp_path, name="tangled")

    assert list(model.entities) == ["legacy.CustomerAttachment"]
    assert proposal.deselected == ()
    assert (
        proposal.demoted[0].name == "CUSTOMER_ATTACHMENT.STORE_KEY"
    ), proposal.demoted
    assert "ATTACHMENT_STORE" in proposal.demoted[0].reason
    # A binary column has no field type either, so demotion drops it rather
    # than inventing one -- and says so instead of leaving a silent gap.
    assert "store_key" not in dict(proposal.entities[0].fields)


def test_a_table_pattern_selects_every_table_it_matches(legacy_url: str) -> None:
    proposal = inspect_schema(legacy_url, tables=("*_master",))

    assert sorted(entity.table for entity in proposal.entities) == [
        "CUSTOMER_MASTER",
        "EMPLOYEE_MASTER",
    ]
    assert proposal.deselected == ("NOTE_LOG", "ORDER_LINE")
    assert proposal.demoted == ()


def test_an_excluded_pattern_is_left_out_of_the_proposal(legacy_url: str) -> None:
    proposal = inspect_schema(legacy_url, exclude=("*_LOG", "ORDER_*"))

    assert sorted(entity.table for entity in proposal.entities) == [
        "CUSTOMER_MASTER",
        "EMPLOYEE_MASTER",
    ]
    assert proposal.deselected == ("NOTE_LOG", "ORDER_LINE")
    # Excluding what could never be proposed anyway removes the noise about it.
    assert proposal.skipped == ()


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        # A shouted single word reads better folded ...
        ("CUSTOMER_MASTER", "legacy.CustomerMaster"),
        ("NOTE_LOG", "legacy.NoteLog"),
        # ... but a name that already carries its own capitals is a name.
        ("EquipmentInstance", "legacy.EquipmentInstance"),
        ("Equipment", "legacy.Equipment"),
        ("equipment_instance", "legacy.EquipmentInstance"),
    ],
)
def test_an_entity_name_keeps_the_capitals_the_table_name_already_had(
    table: str, expected: str
) -> None:
    assert inspection._entity_name(table, None, "legacy") == expected


def test_a_runnable_proposal_is_one_the_tui_can_actually_open(
    legacy_url: str, tmp_path: Path
) -> None:
    """Compiling is not running: without these, every surface shows nothing.

    An entity exposed to no channel with no permissions, in an application
    with no views and no roles, is a valid model that no renderer can open.
    """

    proposal = inspect_schema(legacy_url)
    model = _compiled(proposal, tmp_path, name="runnable", runnable=True)

    assert sorted(model.views) == [
        "legacy.CustomerMaster.browse",
        "legacy.CustomerMaster.edit",
        "legacy.CustomerMaster.lookup",
        "legacy.EmployeeMaster.browse",
        # The inline editor for the collection turned around from
        # CUSTOMER_MASTER's foreign key.
        "legacy.EmployeeMaster.customer_master.inline",
        "legacy.EmployeeMaster.edit",
        "legacy.EmployeeMaster.lookup",
    ]
    assert sorted(model.roles) == ["operator"]

    customer = model.entity("legacy.CustomerMaster")
    assert customer.metadata["expose"]["tui"] is True
    assert customer.metadata["permissions"]["list"] == "legacy.customermaster.list"

    # A reference with no lookup view cannot be picked: the form reports
    # "No lookup view is configured" and the field is dead. Naming the view is
    # only half of it -- the editor defaults to a select over the first 500
    # target rows, which cannot hold a legacy table and raises
    # InvalidSelectValueError outright when the stored key is not among them.
    edit = model.views["legacy.CustomerMaster.edit"]
    configured = edit.data["fields"]["owner_employee_no"]
    assert configured["editor"] == "lookup"
    assert configured["lookup_view"] == "legacy.EmployeeMaster.lookup"
    assert configured["lookup_view"] in model.views


def test_a_runnable_proposal_is_served_by_the_rest_api_the_web_ui_reads(
    legacy_url: str, tmp_path: Path
) -> None:
    """The Web UI is a REST client, so `expose: {tui: true}` renders nothing.

    Asserted against the generated description rather than the `expose`
    metadata, because the property that matters is whether the routes are
    there: a server with the entity metadata and no entity routes answers 404
    to every request the browser makes, and looks like an empty application.
    """

    from tide.api.openapi import generate_openapi

    model = _compiled(inspect_schema(legacy_url), tmp_path, "served", runnable=True)
    paths = set(generate_openapi(model)["paths"])

    assert "/api/v1/legacy/customer-master" in paths, sorted(paths)
    assert "/api/v1/legacy/customer-master/{customer_no}" in paths, sorted(paths)
    assert "/api/v1/legacy/employee-master" in paths, sorted(paths)

    exposure = model.entity("legacy.CustomerMaster").metadata["expose"]
    assert exposure["mcp"]["tools"] == ("search", "create", "update", "delete")


def test_a_reference_becomes_a_collection_on_the_entity_it_points_at(
    legacy_url: str, tmp_path: Path
) -> None:
    """One foreign key is two halves of a relationship, not one.

    Reflection can only see the half that holds the column, so proposing that
    alone gives every child a picker and every parent nothing -- no way to see
    what points at a record from the record itself.
    """

    model = _compiled(inspect_schema(legacy_url), tmp_path, "linked", runnable=True)

    collection = model.entity("legacy.EmployeeMaster").field("customer_master")
    assert collection.metadata["type"] == "collection"
    assert collection.metadata["target"] == "legacy.CustomerMaster"
    assert collection.metadata["inverse"] == "owner_employee_no"

    inline = model.views["legacy.EmployeeMaster.customer_master.inline"]
    assert inline.entity == "legacy.CustomerMaster"
    assert inline.kind == "inline_edit"
    # The key that ties a row to the record it is already inside is noise.
    assert "owner_employee_no" not in inline.data["columns"]

    layout = model.views["legacy.EmployeeMaster.edit"].data["layout"]
    section = next(item for item in layout if "collection" in item)
    assert section["collection"] == "customer_master"
    assert section["view"] == "legacy.EmployeeMaster.customer_master.inline"

    # A browse view lists columns, and a collection is not one.
    browse = model.views["legacy.EmployeeMaster.browse"].data["columns"]
    assert "customer_master" not in browse


def test_two_keys_from_one_table_become_two_differently_named_collections(
    paired_url: str, tmp_path: Path
) -> None:
    """Naming a collection after the child alone collides the moment it repeats.

    ROUTE_LEG points at DEPOT twice and DEPOT points at itself, so the owner
    would otherwise be given three fields called `route_leg` and `depot`.
    """

    model = _compiled(inspect_schema(paired_url), tmp_path, "paired", runnable=True)
    depot = model.entity("legacy.Depot")

    collections = {
        name: field.metadata["inverse"]
        for name, field in depot.fields.items()
        if field.metadata["type"] == "collection"
    }
    # DEPOT.PARENT_DEPOT_NO points at DEPOT: a collection there is hydrated
    # into itself for ever, so it is declined rather than proposed.
    assert collections == {
        "route_leg_origin_no": "origin_no",
        "route_leg_destination_no": "destination_no",
    }
    for name in collections:
        assert f"legacy.Depot.{name}.inline" in model.views

    _, declined = synthesize_collections(inspect_schema(paired_url).entities)
    assert [(item.name, "without end" in item.reason) for item in declined] == [
        ("legacy.Depot.parent_depot_no", True)
    ]


def test_a_collection_chain_hydration_cannot_follow_is_declined(
    tmp_path: Path,
) -> None:
    """Too deep is not slower, it is a list that returns nothing at all.

    Collections load eagerly, and past `RelationshipLoadPlan.max_depth` the
    repository raises `RelationshipExpansionLimit` rather than truncating --
    so one chain too long breaks the browse for the entity at its head.
    """

    depth = MAX_COLLECTION_CHAIN + 1
    statements = ["CREATE TABLE T0 (ID INTEGER PRIMARY KEY, NAME VARCHAR(20))"]
    statements += [
        f"CREATE TABLE T{index} (ID INTEGER PRIMARY KEY, "
        f"PARENT_ID INTEGER REFERENCES T{index - 1}(ID))"
        for index in range(1, depth + 1)
    ]
    proposal = inspect_schema(_database(tmp_path, "chain.db", tuple(statements)))
    owned, declined = synthesize_collections(proposal.entities)

    hops = sum(len(items) for items in owned.values())
    assert hops == MAX_COLLECTION_CHAIN, owned
    assert [item.name for item in declined] == [f"legacy.T{depth}.parent_id"]
    assert f"{MAX_COLLECTION_CHAIN} hops" in declined[0].reason


def test_a_runnable_proposal_opens_in_the_tui_over_real_rows(
    legacy_url: str, tmp_path: Path
) -> None:
    """The property `--runnable` promises, asserted where it can be observed.

    `TideApp` raises `application does not define a browse view` on a model
    that merely compiles, so constructing it is the check that matters.
    """

    import asyncio

    from sqlalchemy import create_engine
    from textual.widgets import DataTable

    from tide.data import SQLAlchemyRepository
    from tide.runtime import Channel, Principal, RequestContext
    from tide.services import RecordsService
    from tide.tui import TideApp

    engine = create_engine(legacy_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO CUSTOMER_MASTER VALUES (1001, 'Northwind', 250.00, 1, NULL)"
        )
    engine.dispose()

    model = _compiled(inspect_schema(legacy_url), tmp_path, "opened", runnable=True)
    repository = SQLAlchemyRepository(model, legacy_url)
    repository.validate_schema()
    application = TideApp(
        model,
        RecordsService(model, repository),
        RequestContext(
            principal=Principal("local:probe", roles=frozenset({"operator"})),
            channel=Channel.TUI,
        ),
    )

    async def drive() -> int:
        async with application.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            return int(application.query_one(DataTable).row_count)

    assert asyncio.run(drive()) == 1
    repository.dispose()


def test_a_synthesized_collection_renders_inside_the_record_it_belongs_to(
    legacy_url: str, tmp_path: Path
) -> None:
    """Where a collection has to appear to be worth proposing: on the form.

    Metadata and a view file prove the documents were written. Only opening
    the record shows the tab is really there, wired to the right rows.
    """

    import asyncio

    from sqlalchemy import create_engine
    from textual.widgets import DataTable

    from tide.data import SQLAlchemyRepository
    from tide.runtime import Channel, Principal, RequestContext
    from tide.services import RecordsService
    from tide.tui import TideApp

    engine = create_engine(legacy_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO EMPLOYEE_MASTER VALUES (7, 'Mira Lang', NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO CUSTOMER_MASTER VALUES (1001, 'Northwind', 250.00, 1, 7)"
        )
    engine.dispose()

    model = _compiled(inspect_schema(legacy_url), tmp_path, "nested", runnable=True)
    repository = SQLAlchemyRepository(model, legacy_url)
    application = TideApp(
        model,
        RecordsService(model, repository),
        RequestContext(
            principal=Principal("local:probe", roles=frozenset({"operator"})),
            channel=Channel.TUI,
        ),
        view_name="legacy.EmployeeMaster.browse",
    )

    async def drive() -> list[tuple[str, int]]:
        async with application.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            application.open_record(7)
            for _ in range(6):
                await pilot.pause()
            return [
                (type(application.screen).__name__, table.row_count)
                for table in application.screen.query(DataTable)
            ]

    assert asyncio.run(drive()) == [("RecordEditScreen", 1)]
    repository.dispose()


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


def test_a_table_pattern_matching_nothing_fails_before_writing_anything(
    legacy_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A typo must not quietly hand back a smaller project than was asked for."""

    monkeypatch.setenv("TIDE_DATABASE_URL", legacy_url)
    destination = tmp_path / "typo"

    exit_code = main(
        [
            "db",
            "inspect",
            "--database-env",
            "--table",
            "CUSTOMER_MASTER",
            "--table",
            "CUSTMER_*",
            "--output",
            str(destination),
        ]
    )

    assert exit_code == 1
    assert "CUSTMER_*" in capsys.readouterr().err
    assert not destination.exists()


def test_the_listing_reports_every_table_and_writes_nothing(
    legacy_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Choosing tables sensibly means seeing the menu first."""

    monkeypatch.setenv("TIDE_DATABASE_URL", legacy_url)
    destination = tmp_path / "listed"

    exit_code = main(
        [
            "db",
            "inspect",
            "--database-env",
            "--list",
            "--exclude",
            "NOTE_LOG",
            "--output",
            str(destination),
        ]
    )

    assert exit_code == 0
    assert not destination.exists()
    listing = capsys.readouterr().out
    assert "CUSTOMER_MASTER" in listing
    assert "EMPLOYEE_MASTER" in listing
    assert "composite primary key" in listing
    assert "NOTE_LOG" in listing


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
