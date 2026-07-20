"""Approval-bound, render-only Alembic revision artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import pprint
import re
from typing import Any, Iterable, Mapping

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Engine,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    Unicode,
    UnicodeText,
    UniqueConstraint,
)
from sqlalchemy.engine import URL
from sqlalchemy.sql.schema import PrimaryKeyConstraint

from tide.compiler.normalized import ApplicationModel
from tide.data.migrations import (
    MigrationChange,
    MigrationProposal,
    _DesiredTable,
    _desired_tables,
    _qualified_name,
    _revision_renderable,
    propose_migration,
)
from tide.data.sqlalchemy import SQLAlchemyRepository
from tide.runtime.errors import TideRuntimeError


REVISION_FORMAT = "tide.alembic-review-revision"
REVISION_FORMAT_VERSION = 2
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_DOWN_REVISION = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")


class RevisionGenerationError(TideRuntimeError):
    """A review revision could not be rendered without weakening approval."""

    code = "revision_generation_error"


@dataclass(frozen=True, slots=True)
class RevisionArtifact:
    path: Path
    manifest_path: Path
    revision: str
    down_revision: str | None
    proposal_fingerprint: str
    database_fingerprint: str
    sha256: str
    operation_count: int


def generate_revision(
    model: ApplicationModel,
    bind: str | URL | Engine,
    *,
    name: str,
    proposal_fingerprint: str,
    database_fingerprint: str,
    backup_evidence: str,
    acknowledgements: Iterable[str] = (),
    down_revision: str | None = None,
    output_dir: str | Path | None = None,
) -> RevisionArtifact:
    """Render a new revision and manifest without issuing database writes."""

    _validate_inputs(
        name=name,
        proposal_fingerprint=proposal_fingerprint,
        database_fingerprint=database_fingerprint,
        backup_evidence=backup_evidence,
        down_revision=down_revision,
    )
    proposal = propose_migration(model, bind)
    if proposal.kind != "migration_proposal":
        raise RevisionGenerationError(
            "legacy databases forbid TIDE revision generation"
        )
    if proposal.clean:
        raise RevisionGenerationError("the current database has no proposed changes")
    if proposal.database_fingerprint is None:
        raise RevisionGenerationError("the proposal has no managed database fingerprint")
    if not hmac.compare_digest(proposal.fingerprint, proposal_fingerprint):
        raise RevisionGenerationError(
            "proposal fingerprint is stale or does not match the current database"
        )
    if not hmac.compare_digest(
        proposal.database_fingerprint,
        database_fingerprint,
    ):
        raise RevisionGenerationError(
            "database fingerprint is stale or does not match the inspected schema"
        )

    provided = tuple(acknowledgements)
    if len(set(provided)) != len(provided):
        raise RevisionGenerationError("acknowledgement keys must not be repeated")
    known = {change.key for change in proposal.changes}
    unknown = sorted(set(provided) - known)
    if unknown:
        raise RevisionGenerationError(
            "acknowledgement does not belong to the current proposal: " + unknown[0]
        )
    unsupported = [
        change.key for change in proposal.changes if not _revision_renderable(change)
    ]
    if unsupported:
        raise RevisionGenerationError(
            "the initial renderer does not support this change safely: "
            + unsupported[0]
        )
    required = set(proposal.required_acknowledgements)
    unnecessary = sorted(set(provided) - required)
    if unnecessary:
        raise RevisionGenerationError(
            "acknowledgement is not required by this renderable proposal: "
            + unnecessary[0]
        )
    missing = sorted(required - set(provided))
    if missing:
        raise RevisionGenerationError(
            "non-additive change requires exact --acknowledge: " + missing[0]
        )
    repository = SQLAlchemyRepository(model, bind)
    owns_repository = not isinstance(bind, Engine)
    try:
        desired = _desired_tables(repository)
        script, upgrade_operations, downgrade_operations = _render_script(
            model,
            repository.engine,
            proposal,
            desired,
            name=name,
            down_revision=down_revision,
            backup_evidence=backup_evidence,
            acknowledgements=provided,
        )
    finally:
        if owns_repository:
            repository.dispose()

    revision = proposal.fingerprint[:12]
    directory = _output_directory(model, output_dir)
    slug = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
    path = directory / f"{revision}_{slug}.py"
    manifest_path = path.with_name(f"{path.name}.manifest.json")
    script_bytes = script.encode("utf-8")
    digest = hashlib.sha256(script_bytes).hexdigest()
    manifest = {
        "format": REVISION_FORMAT,
        "format_version": REVISION_FORMAT_VERSION,
        "application": {
            "name": model.name,
            "version": model.version,
            "schema_version": model.schema_version,
        },
        "dialect": proposal.dialect,
        "revision": revision,
        "down_revision": down_revision,
        "name": name,
        "proposal_fingerprint": proposal.fingerprint,
        "database_fingerprint": proposal.database_fingerprint,
        "backup_evidence": backup_evidence,
        "acknowledged_change_keys": sorted(provided),
        "change_keys": [change.key for change in proposal.changes],
        "operation_count": len(proposal.changes),
        "operations": {
            "upgrade": upgrade_operations,
            "downgrade": downgrade_operations,
        },
        "script": {
            "filename": path.name,
            "sha256": digest,
        },
        "database_writes_performed": False,
        "migration_apply_available": False,
    }
    reservation = directory / f".{revision}.render.lock"
    reserved = False
    try:
        _reserve_revision(reservation)
        reserved = True
        existing_revision = sorted(directory.glob(f"{revision}_*.py"))
        existing_manifests = sorted(
            directory.glob(f"{revision}_*.py.manifest.json")
        )
        if existing_revision or existing_manifests:
            raise RevisionGenerationError(
                f"revision {revision} already exists; artifacts are never overwritten"
            )
        _write_artifacts(path, script, manifest_path, manifest)
    finally:
        if reserved:
            reservation.unlink(missing_ok=True)
    return RevisionArtifact(
        path=path,
        manifest_path=manifest_path,
        revision=revision,
        down_revision=down_revision,
        proposal_fingerprint=proposal.fingerprint,
        database_fingerprint=proposal.database_fingerprint,
        sha256=digest,
        operation_count=len(proposal.changes),
    )


def _validate_inputs(
    *,
    name: str,
    proposal_fingerprint: str,
    database_fingerprint: str,
    backup_evidence: str,
    down_revision: str | None,
) -> None:
    if not _NAME.fullmatch(name):
        raise RevisionGenerationError(
            "revision name must be 1-80 safe letters, digits, spaces, '.', '_' or '-'"
        )
    if not _FINGERPRINT.fullmatch(proposal_fingerprint):
        raise RevisionGenerationError("proposal fingerprint must be 64 lowercase hex digits")
    if not _FINGERPRINT.fullmatch(database_fingerprint):
        raise RevisionGenerationError("database fingerprint must be 64 lowercase hex digits")
    if (
        not backup_evidence
        or len(backup_evidence) > 240
        or backup_evidence != backup_evidence.strip()
        or not backup_evidence.isprintable()
    ):
        raise RevisionGenerationError(
            "backup evidence must be a printable, trimmed reference of 1-240 characters"
        )
    if down_revision is not None and not _DOWN_REVISION.fullmatch(down_revision):
        raise RevisionGenerationError(
            "down revision must contain 1-64 letters, digits, or underscores"
        )


def _render_script(
    model: ApplicationModel,
    engine: Engine,
    proposal: MigrationProposal,
    desired: Mapping[tuple[str | None, str], _DesiredTable],
    *,
    name: str,
    down_revision: str | None,
    backup_evidence: str,
    acknowledgements: tuple[str, ...],
) -> tuple[str, list[str], list[str]]:
    revision = proposal.fingerprint[:12]
    metadata = {
        "application": model.name,
        "application_version": model.version,
        "schema_version": model.schema_version,
        "dialect": proposal.dialect,
        "proposal_fingerprint": proposal.fingerprint,
        "database_fingerprint": proposal.database_fingerprint,
        "backup_evidence": backup_evidence,
        "acknowledged_change_keys": sorted(acknowledgements),
        "change_keys": [change.key for change in proposal.changes],
        "render_only": True,
    }
    upgrade, downgrade = _render_operations(engine, proposal.changes, desired)
    metadata_source = pprint.pformat(metadata, sort_dicts=True, width=88)
    script = "\n".join(
        [
            '"""TIDE review revision: ' + name + ".",
            "",
            "Generation performed no database writes. Executing this Alembic revision",
            "is a separate, currently unsupported TIDE operation.",
            '"""',
            "",
            "from alembic import op",
            "import sqlalchemy as sa",
            "",
            f"revision = {revision!r}",
            f"down_revision = {down_revision!r}",
            "branch_labels = None",
            "depends_on = None",
            "",
            f"tide_metadata = {metadata_source}",
            "",
            "",
            "def upgrade() -> None:",
            *_indented_operations(upgrade),
            "",
            "",
            "def downgrade() -> None:",
            *_indented_operations(downgrade),
            "",
        ]
    )
    return script, upgrade, downgrade


def _render_operations(
    engine: Engine,
    changes: tuple[MigrationChange, ...],
    desired: Mapping[tuple[str | None, str], _DesiredTable],
) -> tuple[list[str], list[str]]:
    by_operation: dict[str, list[MigrationChange]] = {}
    for change in changes:
        by_operation.setdefault(change.operation, []).append(change)

    table_renames = [
        _table_rename(desired, change)
        for change in by_operation.get("rename_table", ())
    ]
    column_renames = [
        _column_rename(desired, change)
        for change in by_operation.get("rename_column", ())
    ]
    created = [
        _table_for_change(desired, change)
        for change in by_operation.get("create_table", ())
    ]
    created = _creation_order(created)
    added_columns = [
        _column_for_change(desired, change)
        for change in by_operation.get("add_column", ())
    ]
    nullable_columns = [
        _column_for_change(desired, change)
        for change in by_operation.get("drop_not_null", ())
    ]
    created_indexes = [
        _index_for_change(desired, change)
        for change in by_operation.get("create_index", ())
    ]

    upgrade: list[str] = []
    downgrade: list[str] = []
    for previous_schema, previous_name, schema, name in table_renames:
        if previous_schema != schema:
            raise RevisionGenerationError(
                "cross-schema table moves are not supported by the initial renderer"
            )
        upgrade.append(
            _call("op.rename_table", previous_name, name, schema=schema)
        )
    for table, previous, current in column_renames:
        upgrade.append(
            _call(
                "op.alter_column",
                table.name,
                previous,
                new_column_name=current,
                schema=table.schema,
            )
        )
    for table in created:
        upgrade.append("\n".join(_create_table_lines(table, engine)))
    for table in created:
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            upgrade.append(_create_index_call(index, table, engine))
    for table, column in added_columns:
        upgrade.append(
            _call(
                "op.add_column",
                table.name,
                _Raw(_column_expression(column)),
                schema=table.schema,
            )
        )
    for table, column in nullable_columns:
        upgrade.append(
            _call(
                "op.alter_column",
                table.name,
                column.name,
                existing_type=_Raw(_type_expression(column.type)),
                nullable=True,
                schema=table.schema,
            )
        )
    for table, index in created_indexes:
        upgrade.append(_create_index_call(index, table, engine))

    for table, index in reversed(created_indexes):
        downgrade.append(
            _call(
                "op.drop_index",
                _index_name(index),
                table_name=table.name,
                schema=table.schema,
            )
        )
    for table, column in reversed(nullable_columns):
        downgrade.append(
            _call(
                "op.alter_column",
                table.name,
                column.name,
                existing_type=_Raw(_type_expression(column.type)),
                nullable=False,
                schema=table.schema,
            )
        )
    for table, column in reversed(added_columns):
        downgrade.append(
            _call("op.drop_column", table.name, column.name, schema=table.schema)
        )
    for table in reversed(created):
        downgrade.append(_call("op.drop_table", table.name, schema=table.schema))
    for table, previous, current in reversed(column_renames):
        downgrade.append(
            _call(
                "op.alter_column",
                table.name,
                current,
                new_column_name=previous,
                schema=table.schema,
            )
        )
    for previous_schema, previous_name, schema, name in reversed(table_renames):
        downgrade.append(_call("op.rename_table", name, previous_name, schema=schema))
    return upgrade, downgrade


def _table_for_change(
    desired: Mapping[tuple[str | None, str], _DesiredTable],
    change: MigrationChange,
) -> Table:
    for key, table_spec in desired.items():
        if _qualified_name(*key) == change.object_name:
            return table_spec.table
    raise RevisionGenerationError(f"cannot bind revision change {change.key}")


def _column_for_change(
    desired: Mapping[tuple[str | None, str], _DesiredTable],
    change: MigrationChange,
) -> tuple[Table, Column[Any]]:
    for table_spec in desired.values():
        table = table_spec.table
        table_name = _qualified_name(table.schema, table.name)
        for column in table.columns:
            if f"{table_name}.{column.name}" == change.object_name:
                return table, column
    raise RevisionGenerationError(f"cannot bind revision change {change.key}")


def _table_rename(
    desired: Mapping[tuple[str | None, str], _DesiredTable],
    change: MigrationChange,
) -> tuple[str | None, str, str | None, str]:
    for table_spec in desired.values():
        previous = table_spec.renamed_from
        table = table_spec.table
        if previous is None:
            continue
        expected = (
            f"{_qualified_name(*previous)} -> "
            f"{_qualified_name(table.schema, table.name)}"
        )
        if expected == change.object_name:
            return previous[0], previous[1], table.schema, table.name
    raise RevisionGenerationError(f"cannot bind revision change {change.key}")


def _column_rename(
    desired: Mapping[tuple[str | None, str], _DesiredTable],
    change: MigrationChange,
) -> tuple[Table, str, str]:
    for table_spec in desired.values():
        table = table_spec.table
        table_name = _qualified_name(table.schema, table.name)
        for previous, current, _migration_id in table_spec.column_renames:
            if f"{table_name}.{previous} -> {current}" == change.object_name:
                return table, previous, current
    raise RevisionGenerationError(f"cannot bind revision change {change.key}")


def _index_for_change(
    desired: Mapping[tuple[str | None, str], _DesiredTable],
    change: MigrationChange,
) -> tuple[Table, Any]:
    for table_spec in desired.values():
        table = table_spec.table
        table_name = _qualified_name(table.schema, table.name)
        for index in table.indexes:
            columns = tuple(str(expression.name) for expression in index.expressions)
            expected = f"{table_name}.index[{', '.join(columns)}]"
            if expected == change.object_name and not index.unique:
                return table, index
    raise RevisionGenerationError(f"cannot bind revision change {change.key}")


def _creation_order(tables: list[Table]) -> list[Table]:
    remaining = {(table.schema, table.name): table for table in tables}
    result: list[Table] = []
    while remaining:
        available: list[tuple[tuple[str | None, str], Table]] = []
        for key, table in remaining.items():
            dependencies = {
                (constraint.referred_table.schema, constraint.referred_table.name)
                for constraint in table.foreign_key_constraints
            }
            if not (dependencies & remaining.keys()):
                available.append((key, table))
        if not available:
            raise RevisionGenerationError(
                "new tables contain a foreign-key cycle unsupported by the initial renderer"
            )
        for key, table in sorted(
            available,
            key=lambda item: ((item[0][0] or ""), item[0][1].casefold()),
        ):
            result.append(table)
            del remaining[key]
    return result


def _create_table_lines(table: Table, engine: Engine) -> list[str]:
    arguments = [_column_expression(column) for column in table.columns]
    primary_key = next(
        (
            constraint
            for constraint in table.constraints
            if isinstance(constraint, PrimaryKeyConstraint)
        ),
        None,
    )
    if primary_key is not None and primary_key.columns:
        arguments.append(
            _constraint_expression(
                "sa.PrimaryKeyConstraint",
                [column.name for column in primary_key.columns],
                primary_key.name,
            )
        )
    for constraint in sorted(
        (
            item
            for item in table.constraints
            if isinstance(item, UniqueConstraint)
        ),
        key=lambda item: tuple(column.name for column in item.columns),
    ):
        arguments.append(
            _constraint_expression(
                "sa.UniqueConstraint",
                [column.name for column in constraint.columns],
                constraint.name,
            )
        )
    for constraint in sorted(
        table.foreign_key_constraints,
        key=lambda item: tuple(element.parent.name for element in item.elements),
    ):
        local = [element.parent.name for element in constraint.elements]
        remote = [element.target_fullname for element in constraint.elements]
        kwargs: dict[str, Any] = {}
        if constraint.name is not None:
            kwargs["name"] = constraint.name
        if constraint.ondelete is not None:
            kwargs["ondelete"] = constraint.ondelete
        arguments.append(
            _call("sa.ForeignKeyConstraint", local, remote, **kwargs)
        )

    lines = ["op.create_table(", f"    {table.name!r},"]
    lines.extend(f"    {argument}," for argument in arguments)
    if table.schema is not None:
        lines.append(f"    schema={table.schema!r},")
    lines.append(")")
    return lines


def _column_expression(column: Column[Any]) -> str:
    if column.server_default is not None and column.server_default is not column.identity:
        raise RevisionGenerationError(
            "server-default rendering is not supported by the initial renderer"
        )
    arguments = [repr(column.name), _type_expression(column.type)]
    if column.identity is not None:
        arguments.append("sa.Identity()")
    arguments.append(f"nullable={column.nullable!r}")
    return f"sa.Column({', '.join(arguments)})"


def _type_expression(value: Any, *, include_variants: bool = True) -> str:
    if isinstance(value, BigInteger):
        expression = "sa.BigInteger()"
    elif isinstance(value, Integer):
        expression = "sa.Integer()"
    elif isinstance(value, Numeric):
        arguments: list[str] = []
        if value.precision is not None:
            arguments.append(f"precision={value.precision!r}")
        if value.scale is not None:
            arguments.append(f"scale={value.scale!r}")
        arguments.append(f"asdecimal={value.asdecimal!r}")
        expression = f"sa.Numeric({', '.join(arguments)})"
    elif isinstance(value, Boolean):
        expression = "sa.Boolean()"
    elif isinstance(value, DateTime):
        expression = f"sa.DateTime(timezone={value.timezone!r})"
    elif isinstance(value, Date):
        expression = "sa.Date()"
    elif isinstance(value, UnicodeText):
        expression = "sa.UnicodeText()"
    elif isinstance(value, Unicode):
        length = "" if value.length is None else repr(value.length)
        expression = f"sa.Unicode({length})"
    elif isinstance(value, Text):
        expression = "sa.Text()"
    elif isinstance(value, String):
        length = "" if value.length is None else repr(value.length)
        expression = f"sa.String({length})"
    else:
        raise RevisionGenerationError(
            f"SQL type {type(value).__name__} is unsupported by the initial renderer"
        )
    variants = getattr(value, "_variant_mapping", {}) if include_variants else {}
    for dialect, variant in sorted(variants.items()):
        expression += (
            f".with_variant({_type_expression(variant, include_variants=False)}, "
            f"{dialect!r})"
        )
    return expression


def _constraint_expression(
    function: str,
    columns: list[str],
    name: str | None,
) -> str:
    arguments = ", ".join(repr(column) for column in columns)
    if name is not None:
        arguments += f", name={name!r}"
    return f"{function}({arguments})"


def _create_index_call(index: Any, table: Table, engine: Engine) -> str:
    columns = []
    for expression in index.expressions:
        name = getattr(expression, "name", None)
        if not isinstance(name, str):
            raise RevisionGenerationError(
                "expression indexes are unsupported by the initial renderer"
            )
        columns.append(name)
    kwargs: dict[str, Any] = {
        "unique": bool(index.unique),
        "schema": table.schema,
    }
    where = index.dialect_options["mssql"].get("where")
    if where is not None:
        rendered = str(
            where.compile(
                dialect=engine.dialect,
                compile_kwargs={"literal_binds": True},
            )
        )
        kwargs["mssql_where"] = _Raw(f"sa.text({rendered!r})")
    return _call(
        "op.create_index",
        _index_name(index),
        table.name,
        columns,
        **kwargs,
    )


def _index_name(index: Any) -> str:
    if not isinstance(index.name, str) or not index.name:
        raise RevisionGenerationError(
            "all rendered indexes require a deterministic declared name"
        )
    return index.name


@dataclass(frozen=True, slots=True)
class _Raw:
    value: str


def _call(function: str, *arguments: Any, **keywords: Any) -> str:
    rendered = [argument.value if isinstance(argument, _Raw) else repr(argument) for argument in arguments]
    rendered.extend(
        f"{key}={value.value if isinstance(value, _Raw) else repr(value)}"
        for key, value in keywords.items()
        if value is not None
    )
    return f"{function}({', '.join(rendered)})"


def _indented_operations(operations: list[str]) -> list[str]:
    if not operations:
        return ["    pass"]
    result: list[str] = []
    for operation in operations:
        result.extend(f"    {line}" for line in operation.splitlines())
    return result


def _output_directory(
    model: ApplicationModel,
    output_dir: str | Path | None,
) -> Path:
    root = model.project_root.resolve()
    candidate = (
        root / "migrations" / "versions"
        if output_dir is None
        else Path(output_dir)
    )
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RevisionGenerationError(
            "revision output directory must remain inside the application root"
        ) from error
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RevisionGenerationError(
            f"revision output directory could not be created ({type(error).__name__})"
        ) from error
    if not resolved.is_dir():
        raise RevisionGenerationError("revision output path is not a directory")
    return resolved


def _write_artifacts(
    path: Path,
    script: str,
    manifest_path: Path,
    manifest: Mapping[str, Any],
) -> None:
    created: list[Path] = []
    try:
        _write_new(path, script)
        created.append(path)
        _write_new(
            manifest_path,
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        created.append(manifest_path)
    except FileExistsError as error:
        for item in created:
            item.unlink(missing_ok=True)
        raise RevisionGenerationError(
            "revision output already exists; artifacts are never overwritten"
        ) from error
    except OSError as error:
        path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise RevisionGenerationError(
            f"revision artifacts could not be written safely ({type(error).__name__})"
        ) from error


def _reserve_revision(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RevisionGenerationError(
            "this revision is already being rendered or requires stale-lock review"
        ) from error
    except OSError as error:
        raise RevisionGenerationError(
            f"revision reservation could not be created ({type(error).__name__})"
        ) from error
    else:
        os.close(descriptor)


def _write_new(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
