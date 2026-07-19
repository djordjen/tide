from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects import mssql
from sqlalchemy.dialects.mssql import NVARCHAR
from sqlalchemy.pool import StaticPool

from tide import compile_project
from tide.cli import main
from tide.compiler.normalized import NormalizedField, deep_thaw, immutable_mapping
from tide.data import migrations as migration_module
from tide.data import (
    MigrationPlanningError,
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemyRepository,
    propose_migration,
)


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
LEGACY = ROOT / "tests" / "fixtures" / "valid" / "legacy-database"


def test_clean_managed_database_has_deterministic_empty_proposal(
    tmp_path: Path,
) -> None:
    model, url = _managed_database(tmp_path / "clean.db")

    first = propose_migration(model, url)
    second = propose_migration(model, url)

    assert first.clean is True
    assert first.kind == "migration_proposal"
    assert first.fingerprint == second.fingerprint
    assert first.as_dict()["writes_performed"] is False
    assert first.as_dict()["revision_generation_available"] is True
    assert first.as_dict()["revision_render_only"] is True
    assert first.as_dict()["migration_apply_available"] is False
    assert first.as_dict()["rename_inference_performed"] is False
    assert first.database_fingerprint == second.database_fingerprint
    assert first.database_fingerprint is not None


def test_empty_managed_database_proposes_application_and_framework_tables(
    tmp_path: Path,
) -> None:
    model = compile_project(INVOICING)
    database = tmp_path / "empty.db"
    database.touch()

    proposal = propose_migration(
        model,
        f"sqlite+pysqlite:///{database.as_posix()}",
    )

    assert proposal.clean is False
    assert proposal.requires_backup is True
    assert {change.operation for change in proposal.changes} == {"create_table"}
    assert {change.scope for change in proposal.changes} == {
        "application",
        "framework",
    }
    assert {change.object_name for change in proposal.changes} >= {
        "crm_customer",
        "sales_invoice",
        "tide_query_cursor",
        "tide_action_audit",
        "tide_record_audit",
    }


def test_missing_columns_are_classified_by_existing_data_requirements(
    tmp_path: Path,
) -> None:
    model, url = _managed_database(tmp_path / "columns.db")
    changed = _with_fields(
        model,
        "crm.Customer",
        NormalizedField(
            "notes",
            immutable_mapping({"type": "string", "length": 200}),
        ),
        NormalizedField(
            "account_number",
            immutable_mapping({"type": "string", "length": 40, "required": True}),
        ),
    )

    proposal = propose_migration(changed, url)
    changes = {change.object_name: change for change in proposal.changes}

    assert changes["crm_customer.notes"].operation == "add_column"
    assert changes["crm_customer.notes"].safety == "additive"
    assert changes["crm_customer.account_number"].operation == "add_column"
    assert changes["crm_customer.account_number"].safety == "data_required"
    assert proposal.revision_blocked is True


def test_possible_rename_is_never_inferred(tmp_path: Path) -> None:
    model, url = _managed_database(tmp_path / "rename.db")
    customer = model.entity("crm.Customer")
    fields = dict(customer.fields)
    original = fields.pop("code")
    fields["customer_code"] = replace(original, name="customer_code")
    changed_customer = replace(customer, fields=immutable_mapping(fields))
    entities = dict(model.entities)
    entities[customer.name] = changed_customer
    changed = replace(model, entities=immutable_mapping(entities))

    proposal = propose_migration(changed, url)
    operations = {
        (change.operation, change.object_name) for change in proposal.changes
    }

    assert ("drop_column_candidate", "crm_customer.code") in operations
    assert ("add_column", "crm_customer.customer_code") in operations
    assert all("rename" not in change.operation for change in proposal.changes)
    assert proposal.as_dict()["rename_inference_performed"] is False


def test_explicit_renames_preserve_constraint_and_index_identity(
    tmp_path: Path,
) -> None:
    model = compile_project(_rename_project(tmp_path))
    database = tmp_path / "renamed.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE previous_parent ("
            "previous_id INTEGER NOT NULL PRIMARY KEY, "
            "previous_code VARCHAR(40) NOT NULL, "
            "UNIQUE(previous_code))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE previous_child ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "previous_parent_id INTEGER NOT NULL, "
            "FOREIGN KEY(previous_parent_id) REFERENCES "
            "previous_parent(previous_id) ON DELETE CASCADE)"
        )
    engine.dispose()

    proposal = propose_migration(model, url)
    application_changes = [
        change for change in proposal.changes if change.scope == "application"
    ]

    assert {
        (change.operation, change.object_name) for change in application_changes
    } == {
        ("rename_table", "previous_child -> current_child"),
        ("rename_table", "previous_parent -> current_parent"),
        (
            "rename_column",
            "current_child.previous_parent_id -> current_parent_id",
        ),
        ("rename_column", "current_parent.previous_code -> current_code"),
        ("rename_column", "current_parent.previous_id -> current_id"),
    }
    assert proposal.as_dict()["explicit_rename_changes"] == 5
    assert proposal.as_dict()["rename_inference_performed"] is False
    assert proposal.revision_blocked is False
    assert set(proposal.required_acknowledgements) == {
        change.key for change in application_changes
    }
    assert not any(
        change.object_type in {"constraint", "index"}
        for change in application_changes
    )


def test_retained_rename_declarations_are_clean_after_the_schema_is_current(
    tmp_path: Path,
) -> None:
    model = compile_project(_rename_project(tmp_path))
    database = tmp_path / "current.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    repository = SQLAlchemyRepository(model, url)
    repository.create_schema()
    SQLAlchemyCursorStore(repository.engine, mode="managed").create_schema()
    SQLAlchemyActionExecutionStore(repository.engine, mode="managed").create_schema()
    repository.dispose()

    proposal = propose_migration(model, url)

    assert proposal.clean is True
    assert proposal.as_dict()["explicit_rename_changes"] == 0


def test_table_rename_can_move_from_a_separately_declared_schema(
    tmp_path: Path,
) -> None:
    project = tmp_path / "schema-rename"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "tide.yaml").write_text(
        '\n'.join(
            [
                'schema_version: "0.1"',
                "application: {name: Schema Rename, version: 0.1.0}",
                "database: {mode: managed}",
                "model: {paths: [models]}",
            ]
        ),
        encoding="utf-8",
    )
    (models / "item.yaml").write_text(
        """entity: demo.Item
storage:
  schema: current_schema
  table: current_item
  migration_id: demo.item
  renamed_from: {schema: previous_schema, table: previous_item}
fields:
  id: {type: integer, primary_key: true}
""",
        encoding="utf-8",
    )
    model = compile_project(project)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def attach_schemas(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS current_schema")
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS previous_schema")

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE previous_schema.previous_item "
            "(id INTEGER NOT NULL PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE previous_schema.unrelated "
            "(id INTEGER NOT NULL PRIMARY KEY)"
        )

    proposal = propose_migration(model, engine)
    engine.dispose()

    assert any(
        change.operation == "move_table_schema"
        and change.object_name
        == "previous_schema.previous_item -> current_schema.current_item"
        for change in proposal.changes
    )
    assert not any("unrelated" in change.object_name for change in proposal.changes)


def test_explicit_rename_conflicts_and_missing_sources_require_review(
    tmp_path: Path,
) -> None:
    model = compile_project(_rename_project(tmp_path))
    conflict_database = tmp_path / "conflict.db"
    conflict_url = f"sqlite+pysqlite:///{conflict_database.as_posix()}"
    repository = SQLAlchemyRepository(model, conflict_url)
    repository.create_schema()
    repository.dispose()
    engine = create_engine(conflict_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE previous_parent (previous_id INTEGER PRIMARY KEY)"
        )
    engine.dispose()

    conflict = propose_migration(model, conflict_url)

    assert any(
        change.operation == "rename_table_conflict"
        and change.object_name == "previous_parent -> current_parent"
        for change in conflict.changes
    )
    assert not any(
        change.operation == "drop_table_candidate"
        and change.object_name == "previous_parent"
        for change in conflict.changes
    )

    missing_database = tmp_path / "missing-source.db"
    missing_database.touch()
    missing = propose_migration(
        model,
        f"sqlite+pysqlite:///{missing_database.as_posix()}",
    )

    assert {
        change.object_name
        for change in missing.changes
        if change.operation == "rename_table_source_missing"
    } == {
        "previous_child -> current_child",
        "previous_parent -> current_parent",
    }
    assert not any(
        change.operation == "create_table" and change.scope == "application"
        for change in missing.changes
    )


def test_type_and_unique_changes_remain_data_or_manual_review(
    tmp_path: Path,
) -> None:
    model, url = _managed_database(tmp_path / "type-index.db")
    customer = model.entity("crm.Customer")
    fields = dict(customer.fields)
    code_metadata = deep_thaw(fields["code"].metadata)
    code_metadata["length"] = 40
    fields["code"] = replace(
        fields["code"],
        metadata=immutable_mapping(code_metadata),
    )
    fields["external_id"] = NormalizedField(
        "external_id",
        immutable_mapping({"type": "string", "length": 80, "unique": True}),
    )
    entities = dict(model.entities)
    entities[customer.name] = replace(customer, fields=immutable_mapping(fields))
    changed = replace(model, entities=immutable_mapping(entities))

    proposal = propose_migration(changed, url)

    assert any(
        change.operation == "alter_column_type"
        and change.object_name == "crm_customer.code"
        and change.safety == "manual"
        for change in proposal.changes
    )
    assert any(
        change.operation == "add_unique"
        and change.object_name == "crm_customer.index[external_id]"
        and change.safety == "data_required"
        for change in proposal.changes
    )


def test_new_reference_requires_existing_value_validation(tmp_path: Path) -> None:
    model, url = _managed_database(tmp_path / "foreign-key.db")
    changed = _with_fields(
        model,
        "crm.Customer",
        NormalizedField(
            "favorite_product",
            immutable_mapping({"type": "reference", "on_delete": "restrict"}),
            target_entity="catalog.Product",
        ),
    )

    proposal = propose_migration(changed, url)

    assert any(
        change.operation == "add_foreign_key"
        and change.object_name.startswith("crm_customer.foreign_key")
        and change.safety == "data_required"
        for change in proposal.changes
    )


def test_sql_server_reflection_falls_back_to_unique_indexes() -> None:
    class InspectorWithoutUniqueConstraints:
        def get_unique_constraints(self, _name, *, schema=None):
            raise NotImplementedError

        def get_indexes(self, _name, *, schema=None):
            return [
                {
                    "name": "ux_customer_code",
                    "unique": True,
                    "column_names": ["code"],
                }
            ]

    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
    try:
        signatures = migration_module._actual_indexes(
            InspectorWithoutUniqueConstraints(),
            repository.table("crm.Customer"),
        )
    finally:
        repository.dispose()

    assert signatures == {(True, ("code",))}


def test_sql_server_inherited_collation_is_not_a_model_type_change() -> None:
    engine = type("DialectOnly", (), {"dialect": mssql.dialect()})()

    assert migration_module._type_signature(
        NVARCHAR(30, collation="Latin1_General_CI_AI"),
        engine,
    ) == "NVARCHAR(30)"


def test_unexpected_managed_objects_are_destructive_candidates(
    tmp_path: Path,
) -> None:
    model, url = _managed_database(tmp_path / "unexpected.db")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE old_customer (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("ALTER TABLE crm_customer ADD COLUMN old_code TEXT")
    engine.dispose()

    proposal = propose_migration(model, url)
    changes = {(change.operation, change.object_name): change for change in proposal.changes}

    assert changes[("drop_table_candidate", "old_customer")].safety == "destructive"
    assert (
        changes[("drop_column_candidate", "crm_customer.old_code")].safety
        == "destructive"
    )
    assert proposal.revision_blocked is True


def test_missing_file_is_not_created_by_read_only_diff(tmp_path: Path) -> None:
    model = compile_project(INVOICING)
    database = tmp_path / "missing.db"

    with pytest.raises(
        MigrationPlanningError,
        match="requires an existing SQLite database",
    ):
        propose_migration(model, f"sqlite+pysqlite:///{database.as_posix()}")

    assert not database.exists()


def test_legacy_diff_is_a_no_ddl_compatibility_report() -> None:
    model = compile_project(LEGACY)
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    statements: list[str] = []

    @event.listens_for(engine, "connect")
    def attach_schema(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS erp")

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE erp.EMPLOYEE_MASTER ("
            "EMPLOYEE_NO INTEGER PRIMARY KEY, DISPLAY_NAME VARCHAR(120) NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE erp.CUSTOMER_MASTER ("
            "CUSTOMER_NO INTEGER PRIMARY KEY, OWNER_EMPLOYEE_NO INTEGER)"
        )

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    proposal = propose_migration(model, engine)

    assert proposal.kind == "compatibility_report"
    assert proposal.database_mode == "legacy"
    assert proposal.revision_blocked is True
    assert proposal.as_dict()["revision_generation_available"] is False
    assert proposal.required_acknowledgements == ()
    assert any(change.operation == "compatibility_issue" for change in proposal.changes)
    assert not any(
        statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP "))
        for statement in statements
    )
    engine.dispose()


def test_database_diff_cli_json_is_secret_free_and_ci_checkable(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _model, url = _managed_database(tmp_path / "schema-SUPERSECRET.db")
    monkeypatch.setenv("DIFF_DATABASE_URL", url)

    clean = main(
        [
            "db",
            "diff",
            str(INVOICING),
            "--database-env",
            "DIFF_DATABASE_URL",
            "--json",
            "--require-clean",
        ]
    )
    output = capsys.readouterr()

    assert clean == 0
    document = json.loads(output.out)
    assert document["clean"] is True
    assert document["writes_performed"] is False
    assert url not in output.out
    assert "SUPERSECRET" not in output.out
    assert output.err == ""

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE obsolete (id INTEGER PRIMARY KEY)")
    engine.dispose()
    different = main(
        [
            "db",
            "diff",
            str(INVOICING),
            "--database-env",
            "DIFF_DATABASE_URL",
            "--require-clean",
        ]
    )

    different_output = capsys.readouterr()
    assert different == 1
    assert "[DESTRUCTIVE] drop_table_candidate obsolete" in different_output.out


def _managed_database(path: Path):
    model = compile_project(INVOICING)
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    repository = SQLAlchemyRepository(model, url)
    repository.create_schema()
    SQLAlchemyCursorStore(repository.engine, mode="managed").create_schema()
    SQLAlchemyActionExecutionStore(repository.engine, mode="managed").create_schema()
    repository.dispose()
    return model, url


def _with_fields(model, entity_name: str, *new_fields: NormalizedField):
    entity = model.entity(entity_name)
    fields = dict(entity.fields)
    fields.update((field.name, field) for field in new_fields)
    entities = dict(model.entities)
    entities[entity_name] = replace(entity, fields=immutable_mapping(fields))
    return replace(model, entities=immutable_mapping(entities))


def _rename_project(tmp_path: Path) -> Path:
    project = tmp_path / "explicit-renames"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Explicit Renames, version: 0.1.0}",
                "database: {mode: managed}",
                "model: {paths: [models]}",
            ]
        ),
        encoding="utf-8",
    )
    (models / "parent.yaml").write_text(
        """entity: demo.Parent
storage:
  table: current_parent
  migration_id: demo.parent
  renamed_from: {table: previous_parent}
fields:
  id:
    type: integer
    primary_key: true
    column: current_id
    migration_id: demo.parent.id
    renamed_from: previous_id
  code:
    type: string
    length: 40
    required: true
    unique: true
    column: current_code
    migration_id: demo.parent.code
    renamed_from: previous_code
""",
        encoding="utf-8",
    )
    (models / "child.yaml").write_text(
        """entity: demo.Child
storage:
  table: current_child
  migration_id: demo.child
  renamed_from: {table: previous_child}
fields:
  id: {type: integer, primary_key: true}
  parent:
    type: reference
    target: demo.Parent
    required: true
    storage: current_parent_id
    migration_id: demo.child.parent
    renamed_from: previous_parent_id
    on_delete: cascade
""",
        encoding="utf-8",
    )
    return project
