from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, event, inspect

from tide import compile_project
from tide.cli import main
from tide.data import (
    RevisionGenerationError,
    RevisionSqlRenderingError,
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemyRepository,
    generate_revision,
    propose_migration,
    render_revision_sql,
)


def test_empty_database_revision_renders_complete_application_and_runtime_tables(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, _item_yaml())
    model = compile_project(project)
    database = tmp_path / "empty.db"
    database.touch()
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    proposal = propose_migration(model, engine)

    artifact = generate_revision(
        model,
        engine,
        name="initial managed schema",
        proposal_fingerprint=proposal.fingerprint,
        database_fingerprint=_database_fingerprint(proposal),
        backup_evidence="restore-rehearsal:INITIAL-001",
    )

    script = artifact.path.read_text(encoding="utf-8")
    compile(script, str(artifact.path), "exec")
    assert "op.create_table(\n        'demo_item'" in script
    assert "op.create_table(\n        'tide_action_audit'" in script
    assert "sa.BigInteger().with_variant(sa.Integer(), 'sqlite')" in script
    assert "sa.Identity()" in script
    assert "op.create_index('ix_tide_action_audit_started'" in script
    assert inspect(engine).get_table_names() == []
    engine.dispose()
    database.unlink()

    outside = tmp_path / "outside.sql"
    with pytest.raises(RevisionSqlRenderingError, match="inside the application root"):
        render_revision_sql(model, artifact.path, output=outside)
    assert not outside.exists()

    sql_artifact = render_revision_sql(model, artifact.path)
    sql = sql_artifact.path.read_text(encoding="utf-8")
    sql_manifest = json.loads(
        sql_artifact.manifest_path.read_text(encoding="utf-8")
    )
    assert "CREATE TABLE demo_item" in sql
    assert "CREATE TABLE tide_action_audit" in sql
    assert "database connection used: no" in sql
    assert "alembic_version" not in sql
    assert sql_manifest["database_connection_used"] is False
    assert sql_manifest["database_writes_performed"] is False
    assert sql_manifest["source"]["revision_sha256"] == artifact.sha256
    assert not database.exists()

    downgrade = render_revision_sql(model, artifact.path, direction="downgrade")
    assert "DROP TABLE demo_item" in downgrade.path.read_text(encoding="utf-8")
    with pytest.raises(RevisionSqlRenderingError, match="never overwritten"):
        render_revision_sql(model, artifact.path)


def test_additive_revision_is_bound_and_rendered_without_database_writes(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, _item_yaml())
    baseline = compile_project(project)
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'additive.db').as_posix()}"
    )
    _initialize_database(baseline, engine)
    _write_model(project, _item_yaml(notes="  notes: {type: string, length: 120}\n"))
    model = compile_project(project)
    proposal = propose_migration(model, engine)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    with pytest.raises(RevisionGenerationError, match="is not required"):
        generate_revision(
            model,
            engine,
            name="add item notes",
            proposal_fingerprint=proposal.fingerprint,
            database_fingerprint=_database_fingerprint(proposal),
            backup_evidence="restore-rehearsal:ADD-NOTES-001",
            acknowledgements=(proposal.changes[0].key,),
            output_dir="review-revisions",
        )
    outside = tmp_path / "outside-revisions"
    with pytest.raises(RevisionGenerationError, match="inside the application root"):
        generate_revision(
            model,
            engine,
            name="add item notes",
            proposal_fingerprint=proposal.fingerprint,
            database_fingerprint=_database_fingerprint(proposal),
            backup_evidence="restore-rehearsal:ADD-NOTES-001",
            output_dir=outside,
        )
    assert not outside.exists()

    artifact = generate_revision(
        model,
        engine,
        name="add item notes",
        proposal_fingerprint=proposal.fingerprint,
        database_fingerprint=_database_fingerprint(proposal),
        backup_evidence="restore-rehearsal:ADD-NOTES-001",
        output_dir="review-revisions",
    )

    script = artifact.path.read_text(encoding="utf-8")
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    compile(script, str(artifact.path), "exec")
    assert "op.add_column('demo_item'" in script
    assert "sa.Column('notes', sa.Unicode(120), nullable=True)" in script
    assert "op.drop_column('demo_item', 'notes')" in script
    assert artifact.operation_count == 1
    assert manifest["proposal_fingerprint"] == proposal.fingerprint
    assert manifest["database_fingerprint"] == proposal.database_fingerprint
    assert manifest["backup_evidence"] == "restore-rehearsal:ADD-NOTES-001"
    assert manifest["database_writes_performed"] is False
    assert manifest["migration_apply_available"] is False
    assert manifest["script"]["sha256"] == hashlib.sha256(
        script.encode("utf-8")
    ).hexdigest()
    assert {column["name"] for column in inspect(engine).get_columns("demo_item")} == {
        "id",
        "name",
    }
    assert not any(
        statement.lstrip().upper().startswith(
            ("CREATE ", "ALTER ", "DROP ", "INSERT ", "UPDATE ", "DELETE ")
        )
        for statement in statements
    )
    engine.dispose()


@pytest.mark.parametrize("fingerprint", ["proposal", "database"])
def test_stale_fingerprints_create_no_artifacts(
    tmp_path: Path,
    fingerprint: str,
) -> None:
    project = _project(tmp_path, _item_yaml())
    baseline = compile_project(project)
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'stale.db').as_posix()}")
    _initialize_database(baseline, engine)
    _write_model(project, _item_yaml(notes="  notes: {type: string}\n"))
    model = compile_project(project)
    proposal = propose_migration(model, engine)
    proposal_fingerprint = proposal.fingerprint
    database_fingerprint = _database_fingerprint(proposal)
    if fingerprint == "proposal":
        proposal_fingerprint = "0" * 64
    else:
        database_fingerprint = "0" * 64

    with pytest.raises(RevisionGenerationError, match="fingerprint is stale"):
        generate_revision(
            model,
            engine,
            name="stale revision",
            proposal_fingerprint=proposal_fingerprint,
            database_fingerprint=database_fingerprint,
            backup_evidence="restore-rehearsal:STALE-001",
        )

    assert not (project / "migrations").exists()
    engine.dispose()


def test_data_required_change_remains_blocked_even_when_acknowledged(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, _item_yaml())
    baseline = compile_project(project)
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'data-required.db').as_posix()}"
    )
    _initialize_database(baseline, engine)
    _write_model(
        project,
        _item_yaml(notes="  account: {type: string, required: true}\n"),
    )
    model = compile_project(project)
    proposal = propose_migration(model, engine)
    assert proposal.revision_blocked is True
    assert proposal.required_acknowledgements == ()

    with pytest.raises(RevisionGenerationError, match="does not support"):
        generate_revision(
            model,
            engine,
            name="required account",
            proposal_fingerprint=proposal.fingerprint,
            database_fingerprint=_database_fingerprint(proposal),
            backup_evidence="restore-rehearsal:ACCOUNT-001",
        )
    assert not (project / "migrations").exists()
    engine.dispose()


def test_sqlite_nullability_revision_waits_for_batch_rebuild_contract(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, _item_yaml())
    baseline = compile_project(project)
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'nullability.db').as_posix()}"
    )
    _initialize_database(baseline, engine)
    _write_model(
        project,
        _item_yaml().replace(
            "name: {type: string, length: 80, required: true}",
            "name: {type: string, length: 80}",
        ),
    )
    model = compile_project(project)
    proposal = propose_migration(model, engine)
    assert [change.operation for change in proposal.changes] == ["drop_not_null"]
    artifact = generate_revision(
        model,
        engine,
        name="make item name optional",
        proposal_fingerprint=proposal.fingerprint,
        database_fingerprint=_database_fingerprint(proposal),
        backup_evidence="restore-rehearsal:NULLABLE-001",
    )
    engine.dispose()

    with pytest.raises(RevisionSqlRenderingError, match="batch-table rebuild"):
        render_revision_sql(model, artifact.path)

    assert not list(artifact.path.parent.glob("*.sql"))


def test_explicit_renames_require_exact_keys_and_never_overwrite(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, _rename_yaml())
    model = compile_project(project)
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'rename.db').as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE previous_item ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "previous_code VARCHAR(40) NOT NULL)"
        )
    SQLAlchemyCursorStore(engine, mode="managed").create_schema()
    SQLAlchemyActionExecutionStore(engine, mode="managed").create_schema()
    proposal = propose_migration(model, engine)
    assert {change.operation for change in proposal.changes} == {
        "rename_table",
        "rename_column",
    }

    with pytest.raises(RevisionGenerationError, match="requires exact --acknowledge"):
        generate_revision(
            model,
            engine,
            name="rename item code",
            proposal_fingerprint=proposal.fingerprint,
            database_fingerprint=_database_fingerprint(proposal),
            backup_evidence="restore-rehearsal:RENAME-001",
        )

    artifact = generate_revision(
        model,
        engine,
        name="rename item code",
        proposal_fingerprint=proposal.fingerprint,
        database_fingerprint=_database_fingerprint(proposal),
        backup_evidence="restore-rehearsal:RENAME-001",
        acknowledgements=proposal.required_acknowledgements,
    )
    script = artifact.path.read_text(encoding="utf-8")
    assert "op.rename_table('previous_item', 'current_item')" in script
    assert (
        "op.alter_column('current_item', 'previous_code', "
        "new_column_name='current_code')"
    ) in script
    assert inspect(engine).has_table("previous_item")
    assert not inspect(engine).has_table("current_item")

    rendered = render_revision_sql(model, artifact.path)
    rendered_sql = rendered.path.read_text(encoding="utf-8")
    assert "ALTER TABLE previous_item RENAME TO current_item" in rendered_sql
    assert (
        "ALTER TABLE current_item RENAME COLUMN previous_code TO current_code"
        in rendered_sql
    )

    with pytest.raises(RevisionGenerationError, match="already exists"):
        generate_revision(
            model,
            engine,
            name="rename item code",
            proposal_fingerprint=proposal.fingerprint,
            database_fingerprint=_database_fingerprint(proposal),
            backup_evidence="restore-rehearsal:RENAME-001",
            acknowledgements=proposal.required_acknowledgements,
        )
    engine.dispose()


def test_offline_sql_rejects_tampered_python_even_with_refreshed_hash(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, _item_yaml())
    model = compile_project(project)
    database = tmp_path / "tampered.db"
    database.touch()
    proposal = propose_migration(
        model,
        f"sqlite+pysqlite:///{database.as_posix()}",
    )
    artifact = generate_revision(
        model,
        f"sqlite+pysqlite:///{database.as_posix()}",
        name="tamper test",
        proposal_fingerprint=proposal.fingerprint,
        database_fingerprint=_database_fingerprint(proposal),
        backup_evidence="restore-rehearsal:TAMPER-001",
    )
    script = artifact.path.read_text(encoding="utf-8") + "\nopen('unexpected')\n"
    artifact.path.write_text(script, encoding="utf-8", newline="\n")
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    manifest["script"]["sha256"] = hashlib.sha256(
        script.encode("utf-8")
    ).hexdigest()
    artifact.manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(RevisionSqlRenderingError, match="unapproved executable code"):
        render_revision_sql(model, artifact.path)

    assert not list(artifact.path.parent.glob("*.sql"))


def test_verified_revision_renders_sql_server_batches_without_a_driver_or_database(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, _item_yaml())
    model = compile_project(project)
    database = tmp_path / "retarget.db"
    database.touch()
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    proposal = propose_migration(model, url)
    artifact = generate_revision(
        model,
        url,
        name="sql server initial schema",
        proposal_fingerprint=proposal.fingerprint,
        database_fingerprint=_database_fingerprint(proposal),
        backup_evidence="restore-rehearsal:MSSQL-001",
    )
    script = artifact.path.read_text(encoding="utf-8").replace(
        "'dialect': 'sqlite'",
        "'dialect': 'mssql'",
    )
    artifact.path.write_text(script, encoding="utf-8", newline="\n")
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    manifest["dialect"] = "mssql"
    manifest["script"]["sha256"] = hashlib.sha256(
        script.encode("utf-8")
    ).hexdigest()
    artifact.manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    database.unlink()

    rendered = render_revision_sql(model, artifact.path)
    sql = rendered.path.read_text(encoding="utf-8")

    assert rendered.dialect == "mssql"
    assert "CREATE TABLE demo_item" in sql
    assert "IDENTITY" in sql
    assert "GO" in sql
    assert not database.exists()


def test_revision_cli_is_secret_free_and_writes_inside_application(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = _project(tmp_path, _item_yaml())
    baseline = compile_project(project)
    database = tmp_path / "SUPERSECRET.db"
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    _initialize_database(baseline, engine)
    _write_model(project, _item_yaml(notes="  notes: {type: string}\n"))
    model = compile_project(project)
    proposal = propose_migration(model, engine)
    engine.dispose()
    monkeypatch.setenv("REVISION_DATABASE_URL", f"sqlite+pysqlite:///{database.as_posix()}")

    result = main(
        [
            "db",
            "revision",
            str(project),
            "--database-env",
            "REVISION_DATABASE_URL",
            "--name",
            "add notes",
            "--proposal-fingerprint",
            proposal.fingerprint,
            "--database-fingerprint",
            _database_fingerprint(proposal),
            "--backup-evidence",
            "restore-rehearsal:CLI-001",
            "--output-dir",
            "review",
        ]
    )
    output = capsys.readouterr()

    assert result == 0
    assert "Database writes performed: no" in output.out
    assert "Migration apply available: no" in output.out
    assert "SUPERSECRET" not in output.out
    assert output.err == ""
    revisions = list((project / "review").glob("*.py"))
    assert len(revisions) == 1
    database.unlink()

    rendered_result = main(
        [
            "db",
            "render-sql",
            str(project),
            str(revisions[0]),
        ]
    )
    rendered_output = capsys.readouterr()

    assert rendered_result == 0
    assert "Database connection used: no" in rendered_output.out
    assert "Migration apply available: no" in rendered_output.out
    assert rendered_output.err == ""
    assert not database.exists()


def _project(tmp_path: Path, model_yaml: str) -> Path:
    project = tmp_path / "revision-app"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Revision App, version: 0.1.0}",
                "database: {mode: managed}",
                "model: {paths: [models]}",
            ]
        ),
        encoding="utf-8",
    )
    _write_model(project, model_yaml)
    return project


def _write_model(project: Path, model_yaml: str) -> None:
    (project / "models" / "item.yaml").write_text(model_yaml, encoding="utf-8")


def _item_yaml(*, notes: str = "") -> str:
    return (
        "entity: demo.Item\n"
        "fields:\n"
        "  id: {type: integer, primary_key: true}\n"
        "  name: {type: string, length: 80, required: true}\n"
        f"{notes}"
    )


def _rename_yaml() -> str:
    return """entity: demo.Item
storage:
  table: current_item
  migration_id: demo.item
  renamed_from: {table: previous_item}
fields:
  id: {type: integer, primary_key: true}
  code:
    type: string
    length: 40
    required: true
    column: current_code
    migration_id: demo.item.code
    renamed_from: previous_code
"""


def _initialize_database(model, engine: Engine) -> None:
    repository = SQLAlchemyRepository(model, engine)
    repository.create_schema()
    SQLAlchemyCursorStore(engine, mode="managed").create_schema()
    SQLAlchemyActionExecutionStore(engine, mode="managed").create_schema()


def _database_fingerprint(proposal) -> str:
    assert proposal.database_fingerprint is not None
    return proposal.database_fingerprint
