"""Read-only, deterministic database migration proposals."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal, Mapping

from sqlalchemy import Engine, Table, UniqueConstraint, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from tide.compiler.normalized import ApplicationModel
from tide.data.sqlalchemy import SQLAlchemyRepository
from tide.data.sqlalchemy_actions import SQLAlchemyActionExecutionStore
from tide.data.sqlalchemy_cursors import SQLAlchemyCursorStore
from tide.runtime.errors import TideRuntimeError


MigrationSafety = Literal["additive", "data_required", "destructive", "manual"]
MigrationScope = Literal["application", "framework"]
REVISION_RENDERABLE_OPERATIONS = frozenset(
    {
        "create_table",
        "add_column",
        "drop_not_null",
        "create_index",
        "rename_table",
        "rename_column",
    }
)


class MigrationPlanningError(TideRuntimeError):
    """The current database could not be safely inspected for a proposal."""

    code = "migration_planning_error"


def _revision_renderable(change: MigrationChange) -> bool:
    if change.operation not in REVISION_RENDERABLE_OPERATIONS:
        return False
    return change.operation != "add_column" or change.safety == "additive"


@dataclass(frozen=True, slots=True)
class MigrationChange:
    operation: str
    object_type: str
    object_name: str
    scope: MigrationScope
    safety: MigrationSafety
    current: str | None
    desired: str | None
    reason: str

    @property
    def key(self) -> str:
        return f"{self.scope}:{self.object_name}:{self.operation}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "operation": self.operation,
            "object_type": self.object_type,
            "object_name": self.object_name,
            "scope": self.scope,
            "safety": self.safety,
            "current": self.current,
            "desired": self.desired,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class MigrationProposal:
    application: str
    application_version: str
    schema_version: str
    dialect: str
    database_mode: str
    kind: Literal["migration_proposal", "compatibility_report"]
    changes: tuple[MigrationChange, ...]
    database_fingerprint: str | None
    fingerprint: str

    @property
    def clean(self) -> bool:
        return not self.changes

    @property
    def requires_backup(self) -> bool:
        return self.kind == "migration_proposal" and bool(self.changes)

    @property
    def revision_blocked(self) -> bool:
        return self.kind != "migration_proposal" or any(
            not _revision_renderable(change) for change in self.changes
        )

    @property
    def required_acknowledgements(self) -> tuple[str, ...]:
        if self.kind != "migration_proposal":
            return ()
        return tuple(
            change.key
            for change in self.changes
            if change.safety != "additive" and _revision_renderable(change)
        )

    def as_dict(self) -> dict[str, Any]:
        counts = {
            safety: sum(change.safety == safety for change in self.changes)
            for safety in ("additive", "data_required", "destructive", "manual")
        }
        return {
            "application": self.application,
            "application_version": self.application_version,
            "schema_version": self.schema_version,
            "dialect": self.dialect,
            "database_mode": self.database_mode,
            "kind": self.kind,
            "clean": self.clean,
            "writes_performed": False,
            "revision_generation_available": self.kind == "migration_proposal",
            "revision_render_only": self.kind == "migration_proposal",
            "migration_apply_available": False,
            "requires_backup": self.requires_backup,
            "revision_blocked": self.revision_blocked,
            "required_acknowledgements": list(self.required_acknowledgements),
            "rename_inference_performed": False,
            "explicit_rename_changes": sum(
                change.operation
                in {"rename_table", "rename_column", "move_table_schema"}
                for change in self.changes
            ),
            "comparison_limits": [
                "constraint and index names are not semantic identity",
                "filtered-index predicates are not compared in this initial proposal",
                "database-inherited string collations are not compared because the "
                "current model cannot declare collation",
                "server defaults, identity options, computed expressions, and check "
                "constraints are not compared in this initial proposal",
                "renames are recognized only from explicit migration_id and "
                "renamed_from declarations",
            ],
            "counts": counts,
            "changes": [change.as_dict() for change in self.changes],
            "database_fingerprint": self.database_fingerprint,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class _DesiredTable:
    table: Table
    scope: MigrationScope
    migration_id: str | None = None
    renamed_from: tuple[str | None, str] | None = None
    column_renames: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _TableMatch:
    desired_key: tuple[str | None, str]
    actual_key: tuple[str | None, str]
    table_spec: _DesiredTable
    column_matches: Mapping[str, str]


def propose_migration(
    model: ApplicationModel,
    bind: str | URL | Engine,
) -> MigrationProposal:
    """Inspect a database and return a deterministic proposal without issuing DDL."""

    repository: SQLAlchemyRepository | None = None
    owns_engine = not isinstance(bind, Engine)
    try:
        _require_existing_database(bind)
        repository = SQLAlchemyRepository(model, bind)
        dialect = repository.engine.dialect.name
        if str(model.database["mode"]) == "legacy":
            changes = _legacy_compatibility_changes(repository)
            return _proposal(
                model,
                dialect,
                "compatibility_report",
                changes,
                database_fingerprint=None,
            )

        desired = _desired_tables(repository)
        inspector = inspect(repository.engine)
        database_fingerprint = _managed_schema_fingerprint(
            repository.engine,
            inspector,
            desired,
        )
        changes = _managed_changes(repository.engine, desired, inspector=inspector)
        return _proposal(
            model,
            dialect,
            "migration_proposal",
            changes,
            database_fingerprint=database_fingerprint,
        )
    except MigrationPlanningError:
        raise
    except (NotImplementedError, SQLAlchemyError, TideRuntimeError, ValueError) as error:
        raise MigrationPlanningError(
            f"database migration inspection failed safely ({type(error).__name__})"
        ) from error
    finally:
        if repository is not None and owns_engine:
            repository.dispose()


def _require_existing_database(bind: str | URL | Engine) -> None:
    if isinstance(bind, Engine):
        return
    try:
        parsed = make_url(bind)
    except (SQLAlchemyError, ValueError) as error:
        raise MigrationPlanningError("database URL is invalid") from error
    if parsed.get_backend_name() != "sqlite" or parsed.database in {
        None,
        "",
        ":memory:",
    }:
        return
    if parsed.query.get("uri") or str(parsed.database).startswith("file:"):
        raise MigrationPlanningError(
            "SQLite URI diff requires a caller-owned preconfigured Engine"
        )
    if not Path(str(parsed.database)).expanduser().resolve().is_file():
        raise MigrationPlanningError(
            "read-only database diff requires an existing SQLite database file"
        )


def _proposal(
    model: ApplicationModel,
    dialect: str,
    kind: Literal["migration_proposal", "compatibility_report"],
    changes: list[MigrationChange],
    *,
    database_fingerprint: str | None,
) -> MigrationProposal:
    ordered = tuple(
        sorted(
            changes,
            key=lambda change: (
                change.scope,
                change.object_name.casefold(),
                change.operation,
                change.current or "",
                change.desired or "",
            ),
        )
    )
    identity = {
        "application": model.name,
        "application_version": model.version,
        "schema_version": model.schema_version,
        "dialect": dialect,
        "database_mode": str(model.database["mode"]),
        "kind": kind,
        "database_fingerprint": database_fingerprint,
        "changes": [change.as_dict() for change in ordered],
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return MigrationProposal(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        dialect=dialect,
        database_mode=str(model.database["mode"]),
        kind=kind,
        changes=ordered,
        database_fingerprint=database_fingerprint,
        fingerprint=fingerprint,
    )


def _legacy_compatibility_changes(
    repository: SQLAlchemyRepository,
) -> list[MigrationChange]:
    return [
        MigrationChange(
            operation="compatibility_issue",
            object_type="mapping",
            object_name=issue.object_name,
            scope="application",
            safety="manual",
            current=None,
            desired="compatible external object",
            reason=issue.message,
        )
        for issue in repository.schema_issues()
    ]


def _desired_tables(repository: SQLAlchemyRepository) -> dict[tuple[str | None, str], _DesiredTable]:
    cursor_store = SQLAlchemyCursorStore(repository.engine, mode="managed")
    action_store = SQLAlchemyActionExecutionStore(repository.engine, mode="managed")
    result: dict[tuple[str | None, str], _DesiredTable] = {}
    application_tables: list[_DesiredTable] = []
    for entity_name, entity in repository.model.entities.items():
        table = repository.table(entity_name)
        storage = entity.metadata.get("storage") or {}
        renamed_from = storage.get("renamed_from")
        previous_table = None
        if renamed_from:
            previous_table = (
                renamed_from.get("schema", table.schema),
                str(renamed_from["table"]),
            )
        column_renames = tuple(
            (
                str(field.metadata["renamed_from"]),
                table.c[field_name].name,
                str(field.metadata["migration_id"]),
            )
            for field_name, field in entity.fields.items()
            if field.metadata.get("renamed_from") is not None
        )
        application_tables.append(
            _DesiredTable(
                table,
                "application",
                migration_id=storage.get("migration_id"),
                renamed_from=previous_table,
                column_renames=column_renames,
            )
        )

    for scope, tables in (
        ("application", (item for item in application_tables)),
        ("framework", cursor_store.metadata.tables.values()),
        ("framework", action_store.metadata.tables.values()),
    ):
        for table in tables:
            table_spec = table if isinstance(table, _DesiredTable) else _DesiredTable(
                table,
                scope,
            )
            key = (table_spec.table.schema, table_spec.table.name)
            if key in result:
                raise MigrationPlanningError(
                    f"multiple managed tables map to {_qualified_name(*key)}"
                )
            result[key] = table_spec
    return result


def _managed_changes(
    engine: Engine,
    desired: Mapping[tuple[str | None, str], _DesiredTable],
    *,
    inspector: Any | None = None,
) -> list[MigrationChange]:
    inspector = inspector or inspect(engine)
    changes: list[MigrationChange] = []
    actual_tables = _actual_table_keys(inspector, desired)

    matched: dict[tuple[str | None, str], tuple[str | None, str]] = {}
    consumed_actual: set[tuple[str | None, str]] = set()
    for key, table_spec in desired.items():
        table = table_spec.table
        table_name = _qualified_name(*key)
        previous = table_spec.renamed_from
        current_exists = key in actual_tables
        previous_exists = previous in actual_tables if previous is not None else False
        if previous is not None and current_exists and previous_exists:
            changes.append(
                MigrationChange(
                    operation="rename_table_conflict",
                    object_type="table",
                    object_name=(
                        f"{_qualified_name(*previous)} -> {table_name}"
                    ),
                    scope=table_spec.scope,
                    safety="manual",
                    current="both previous and desired tables exist",
                    desired=table_name,
                    reason=(
                        f"declared migration identity {table_spec.migration_id!r} is "
                        "ambiguous until one physical table is selected"
                    ),
                )
            )
            matched[key] = key
            consumed_actual.update((key, previous))
            continue
        if current_exists:
            matched[key] = key
            consumed_actual.add(key)
            continue
        if previous is not None and previous_exists:
            operation = (
                "rename_table" if previous[0] == key[0] else "move_table_schema"
            )
            changes.append(
                MigrationChange(
                    operation=operation,
                    object_type="table",
                    object_name=f"{_qualified_name(*previous)} -> {table_name}",
                    scope=table_spec.scope,
                    safety="manual",
                    current=_qualified_name(*previous),
                    desired=table_name,
                    reason=(
                        f"explicit rename declared by migration identity "
                        f"{table_spec.migration_id!r}"
                    ),
                )
            )
            matched[key] = previous
            consumed_actual.add(previous)
            continue
        if previous is not None:
            changes.append(
                MigrationChange(
                    operation="rename_table_source_missing",
                    object_type="table",
                    object_name=f"{_qualified_name(*previous)} -> {table_name}",
                    scope=table_spec.scope,
                    safety="manual",
                    current=None,
                    desired=table_name,
                    reason=(
                        "neither the declared previous table nor the desired table exists"
                    ),
                )
            )
            continue
        changes.append(
            MigrationChange(
                operation="create_table",
                object_type="table",
                object_name=table_name,
                scope=table_spec.scope,
                safety="additive",
                current=None,
                desired=_table_summary(table, engine),
                reason="the managed table is absent",
            )
        )

    for schema, name in sorted(
        actual_tables - consumed_actual,
        key=lambda value: ((value[0] or ""), value[1].casefold()),
    ):
        changes.append(
            MigrationChange(
                operation="drop_table_candidate",
                object_type="table",
                object_name=_qualified_name(schema, name),
                scope="application",
                safety="destructive",
                current="table exists",
                desired=None,
                reason=(
                    "the managed model does not declare this table; TIDE does not infer "
                    "whether it was removed, renamed, or externally added"
                ),
            )
        )

    table_aliases = {actual: expected for expected, actual in matched.items()}
    table_matches: list[_TableMatch] = []
    column_aliases: dict[tuple[str | None, str], Mapping[str, str]] = {}
    for desired_key, actual_key in matched.items():
        table_spec = desired[desired_key]
        column_matches, column_changes = _resolve_column_matches(
            engine,
            inspector,
            table_spec,
            actual_key,
        )
        changes.extend(column_changes)
        aliases = {actual: expected for expected, actual in column_matches.items()}
        column_aliases[actual_key] = aliases
        table_matches.append(
            _TableMatch(
                desired_key,
                actual_key,
                table_spec,
                column_matches,
            )
        )

    for match in table_matches:
        changes.extend(
            _table_changes(
                engine,
                inspector,
                match,
                table_aliases,
                column_aliases,
            )
        )
    return changes


def _actual_table_keys(
    inspector: Any,
    desired: Mapping[tuple[str | None, str], _DesiredTable],
) -> set[tuple[str | None, str]]:
    actual_tables: set[tuple[str | None, str]] = set()
    schemas = {schema for schema, _name in desired}
    tables_by_schema: dict[str | None, set[str]] = {}
    for schema in sorted(schemas, key=lambda value: value or ""):
        names = set(inspector.get_table_names(schema=schema))
        tables_by_schema[schema] = names
        actual_tables.update((schema, name) for name in names)
    for table_spec in desired.values():
        previous = table_spec.renamed_from
        if previous is None or previous[0] in schemas:
            continue
        previous_names = tables_by_schema.get(previous[0])
        if previous_names is None:
            previous_names = set(inspector.get_table_names(schema=previous[0]))
            tables_by_schema[previous[0]] = previous_names
        if previous[1] in previous_names:
            # Inspect only the declared source in a schema that TIDE does not
            # otherwise own; unrelated tables there are not drop candidates.
            actual_tables.add(previous)
    return actual_tables


def _managed_schema_fingerprint(
    engine: Engine,
    inspector: Any,
    desired: Mapping[tuple[str | None, str], _DesiredTable],
) -> str:
    tables: list[dict[str, Any]] = []
    for schema, name in sorted(
        _actual_table_keys(inspector, desired),
        key=lambda value: ((value[0] or ""), value[1].casefold()),
    ):
        key = (schema, name)
        columns = [
            {
                "name": str(column["name"]),
                "type": _type_signature(column["type"], engine),
                "nullable": bool(column.get("nullable", True)),
            }
            for column in inspector.get_columns(name, schema=schema)
        ]
        primary_key = tuple(
            str(column)
            for column in inspector.get_pk_constraint(name, schema=schema).get(
                "constrained_columns"
            )
            or ()
        )
        foreign_keys = sorted(
            _actual_foreign_key(foreign_key, key, {}, {})
            for foreign_key in inspector.get_foreign_keys(name, schema=schema)
        )
        indexes = sorted(
            _actual_index_signatures(
                inspector,
                actual_key=key,
                column_aliases={},
            )
        )
        tables.append(
            {
                "schema": schema,
                "name": name,
                "columns": columns,
                "primary_key": primary_key,
                "foreign_keys": foreign_keys,
                "indexes": indexes,
            }
        )
    payload = {
        "dialect": engine.dialect.name,
        "tables": tables,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _resolve_column_matches(
    engine: Engine,
    inspector: Any,
    table_spec: _DesiredTable,
    actual_key: tuple[str | None, str],
) -> tuple[dict[str, str], list[MigrationChange]]:
    table = table_spec.table
    actual_schema, actual_table = actual_key
    table_name = _qualified_name(table.schema, table.name)
    changes: list[MigrationChange] = []
    actual_columns = {
        str(column["name"]): column
        for column in inspector.get_columns(actual_table, schema=actual_schema)
    }
    desired_columns = {column.name: column for column in table.columns}
    rename_by_desired = {
        desired_name: (previous_name, migration_id)
        for previous_name, desired_name, migration_id in table_spec.column_renames
    }
    matches: dict[str, str] = {}
    consumed_actual: set[str] = set()
    for name, desired_column in desired_columns.items():
        rename = rename_by_desired.get(name)
        current_exists = name in actual_columns
        previous_name = rename[0] if rename is not None else None
        previous_exists = (
            previous_name in actual_columns if previous_name is not None else False
        )
        if rename is not None and current_exists and previous_exists:
            changes.append(
                MigrationChange(
                    operation="rename_column_conflict",
                    object_type="column",
                    object_name=f"{table_name}.{previous_name} -> {name}",
                    scope=table_spec.scope,
                    safety="manual",
                    current="both previous and desired columns exist",
                    desired=_desired_column_summary(desired_column, engine),
                    reason=(
                        f"declared migration identity {rename[1]!r} is ambiguous until "
                        "one physical column is selected"
                    ),
                )
            )
            matches[name] = name
            consumed_actual.update((name, previous_name))
            continue
        if current_exists:
            matches[name] = name
            consumed_actual.add(name)
            continue
        if rename is not None and previous_exists:
            changes.append(
                MigrationChange(
                    operation="rename_column",
                    object_type="column",
                    object_name=f"{table_name}.{previous_name} -> {name}",
                    scope=table_spec.scope,
                    safety="manual",
                    current=_column_summary(actual_columns[previous_name], engine),
                    desired=_desired_column_summary(desired_column, engine),
                    reason=f"explicit rename declared by migration identity {rename[1]!r}",
                )
            )
            matches[name] = previous_name
            consumed_actual.add(previous_name)
            continue
        if rename is not None:
            changes.append(
                MigrationChange(
                    operation="rename_column_source_missing",
                    object_type="column",
                    object_name=f"{table_name}.{previous_name} -> {name}",
                    scope=table_spec.scope,
                    safety="manual",
                    current=None,
                    desired=_desired_column_summary(desired_column, engine),
                    reason=(
                        "neither the declared previous column nor the desired column exists"
                    ),
                )
            )
            continue
        safety: MigrationSafety = (
            "additive" if desired_column.nullable else "data_required"
        )
        changes.append(
            MigrationChange(
                operation="add_column",
                object_type="column",
                object_name=f"{table_name}.{name}",
                scope=table_spec.scope,
                safety=safety,
                current=None,
                desired=_desired_column_summary(desired_column, engine),
                reason=(
                    "a nullable column can be added without inventing existing values"
                    if desired_column.nullable
                    else "existing rows require a reviewed value or backfill before NOT NULL"
                ),
            )
        )

    for name in sorted(actual_columns.keys() - consumed_actual, key=str.casefold):
        changes.append(
            MigrationChange(
                operation="drop_column_candidate",
                object_type="column",
                object_name=f"{table_name}.{name}",
                scope=table_spec.scope,
                safety="destructive",
                current=_column_summary(actual_columns[name], engine),
                desired=None,
                reason=(
                    "the managed model does not declare this column; TIDE does not infer "
                    "whether it was removed or renamed"
                ),
            )
        )
    return matches, changes


def _table_changes(
    engine: Engine,
    inspector: Any,
    match: _TableMatch,
    table_aliases: Mapping[
        tuple[str | None, str],
        tuple[str | None, str],
    ],
    column_aliases: Mapping[tuple[str | None, str], Mapping[str, str]],
) -> list[MigrationChange]:
    table_spec = match.table_spec
    table = table_spec.table
    actual_schema, actual_table = match.actual_key
    table_name = _qualified_name(table.schema, table.name)
    changes: list[MigrationChange] = []
    actual_columns = {
        str(column["name"]): column
        for column in inspector.get_columns(actual_table, schema=actual_schema)
    }
    desired_columns = {column.name: column for column in table.columns}
    for name, actual_name in sorted(match.column_matches.items()):
        desired = desired_columns[name]
        actual = actual_columns[actual_name]
        actual_type = _type_signature(actual["type"], engine)
        desired_type = _type_signature(desired.type, engine)
        if actual_type != desired_type:
            changes.append(
                MigrationChange(
                    operation="alter_column_type",
                    object_type="column",
                    object_name=f"{table_name}.{name}",
                    scope=table_spec.scope,
                    safety="manual",
                    current=actual_type,
                    desired=desired_type,
                    reason=(
                        "type conversion and capacity effects are dialect- and "
                        "data-dependent"
                    ),
                )
            )
        actual_nullable = bool(actual.get("nullable", True))
        if actual_nullable != desired.nullable:
            if desired.nullable:
                operation = "drop_not_null"
                safety = "additive"
                reason = "the model permits null values"
            else:
                operation = "set_not_null"
                safety = "data_required"
                reason = "existing rows must be checked or backfilled before NOT NULL"
            changes.append(
                MigrationChange(
                    operation=operation,
                    object_type="column",
                    object_name=f"{table_name}.{name}",
                    scope=table_spec.scope,
                    safety=safety,
                    current="nullable" if actual_nullable else "not null",
                    desired="nullable" if desired.nullable else "not null",
                    reason=reason,
                )
            )

    actual_primary_key = tuple(
        inspector.get_pk_constraint(actual_table, schema=actual_schema).get(
            "constrained_columns"
        )
        or ()
    )
    aliases = column_aliases[match.actual_key]
    actual_primary_key = tuple(aliases.get(name, name) for name in actual_primary_key)
    desired_primary_key = tuple(column.name for column in table.primary_key.columns)
    if actual_primary_key != desired_primary_key:
        changes.append(
            MigrationChange(
                operation="alter_primary_key",
                object_type="constraint",
                object_name=f"{table_name}.primary_key",
                scope=table_spec.scope,
                safety="manual",
                current=", ".join(actual_primary_key) or None,
                desired=", ".join(desired_primary_key) or None,
                reason="primary-key changes affect identity, references, and row addressing",
            )
        )

    changes.extend(
        _foreign_key_changes(
            inspector,
            match,
            table_aliases,
            column_aliases,
        )
    )
    changes.extend(_index_changes(inspector, match, aliases))
    return changes


def _foreign_key_changes(
    inspector: Any,
    match: _TableMatch,
    table_aliases: Mapping[
        tuple[str | None, str],
        tuple[str | None, str],
    ],
    column_aliases: Mapping[tuple[str | None, str], Mapping[str, str]],
) -> list[MigrationChange]:
    table_spec = match.table_spec
    table = table_spec.table
    table_name = _qualified_name(table.schema, table.name)
    desired = {_desired_foreign_key(constraint) for constraint in table.foreign_key_constraints}
    actual = {
        _actual_foreign_key(
            foreign_key,
            match.actual_key,
            table_aliases,
            column_aliases,
        )
        for foreign_key in inspector.get_foreign_keys(
            match.actual_key[1],
            schema=match.actual_key[0],
        )
    }
    changes: list[MigrationChange] = []
    for signature in sorted(desired - actual):
        changes.append(
            MigrationChange(
                operation="add_foreign_key",
                object_type="constraint",
                object_name=f"{table_name}.foreign_key[{_foreign_key_text(signature)}]",
                scope=table_spec.scope,
                safety="data_required",
                current=None,
                desired=_foreign_key_text(signature),
                reason="existing reference values must be validated before enforcement",
            )
        )
    for signature in sorted(actual - desired):
        changes.append(
            MigrationChange(
                operation="drop_foreign_key_candidate",
                object_type="constraint",
                object_name=f"{table_name}.foreign_key[{_foreign_key_text(signature)}]",
                scope=table_spec.scope,
                safety="manual",
                current=_foreign_key_text(signature),
                desired=None,
                reason="constraint removal or rename intent must be reviewed explicitly",
            )
        )
    return changes


def _index_changes(
    inspector: Any,
    match: _TableMatch,
    column_aliases: Mapping[str, str],
) -> list[MigrationChange]:
    table_spec = match.table_spec
    table = table_spec.table
    table_name = _qualified_name(table.schema, table.name)
    desired = _desired_indexes(table)
    actual = _actual_indexes(
        inspector,
        table,
        actual_key=match.actual_key,
        column_aliases=column_aliases,
    )
    changes: list[MigrationChange] = []
    for unique, columns in sorted(desired - actual):
        changes.append(
            MigrationChange(
                operation="add_unique" if unique else "create_index",
                object_type="index",
                object_name=f"{table_name}.index[{', '.join(columns)}]",
                scope=table_spec.scope,
                safety="data_required" if unique else "additive",
                current=None,
                desired=_index_text(unique, columns),
                reason=(
                    "existing duplicates must be checked before uniqueness is enforced"
                    if unique
                    else "the declared lookup index is absent"
                ),
            )
        )
    for unique, columns in sorted(actual - desired):
        changes.append(
            MigrationChange(
                operation="drop_index_candidate",
                object_type="index",
                object_name=f"{table_name}.index[{', '.join(columns)}]",
                scope=table_spec.scope,
                safety="manual",
                current=_index_text(unique, columns),
                desired=None,
                reason="operator-created indexes and constraint intent cannot be inferred",
            )
        )
    return changes


def _desired_foreign_key(constraint: Any) -> tuple[str, str, str, str]:
    local = ",".join(element.parent.name for element in constraint.elements)
    remote_table = _qualified_name(
        constraint.referred_table.schema,
        constraint.referred_table.name,
    )
    remote = ",".join(element.column.name for element in constraint.elements)
    return local, remote_table, remote, _on_delete(constraint.ondelete)


def _actual_foreign_key(
    foreign_key: Mapping[str, Any],
    source_actual_key: tuple[str | None, str],
    table_aliases: Mapping[
        tuple[str | None, str],
        tuple[str | None, str],
    ],
    column_aliases: Mapping[tuple[str | None, str], Mapping[str, str]],
) -> tuple[str, str, str, str]:
    local_aliases = column_aliases.get(source_actual_key, {})
    local = ",".join(
        local_aliases.get(str(value), str(value))
        for value in foreign_key.get("constrained_columns") or ()
    )
    remote_actual_key = (
        foreign_key.get("referred_schema"),
        str(foreign_key.get("referred_table") or ""),
    )
    remote_desired_key = table_aliases.get(remote_actual_key, remote_actual_key)
    remote_table = _qualified_name(*remote_desired_key)
    remote_aliases = column_aliases.get(remote_actual_key, {})
    remote = ",".join(
        remote_aliases.get(str(value), str(value))
        for value in foreign_key.get("referred_columns") or ()
    )
    options = foreign_key.get("options") or {}
    return local, remote_table, remote, _on_delete(options.get("ondelete"))


def _desired_indexes(table: Table) -> set[tuple[bool, tuple[str, ...]]]:
    signatures = {
        (True, tuple(column.name for column in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    signatures.update(
        (
            bool(index.unique),
            tuple(str(expression.name) for expression in index.expressions),
        )
        for index in table.indexes
    )
    return signatures


def _actual_indexes(
    inspector: Any,
    table: Table,
    *,
    actual_key: tuple[str | None, str] | None = None,
    column_aliases: Mapping[str, str] | None = None,
) -> set[tuple[bool, tuple[str, ...]]]:
    if actual_key is None:
        actual_key = (table.schema, table.name)
    if column_aliases is None:
        column_aliases = {}
    return _actual_index_signatures(
        inspector,
        actual_key=actual_key,
        column_aliases=column_aliases,
    )


def _actual_index_signatures(
    inspector: Any,
    *,
    actual_key: tuple[str | None, str],
    column_aliases: Mapping[str, str],
) -> set[tuple[bool, tuple[str, ...]]]:
    actual_schema, actual_table = actual_key

    def normalized_columns(values: Any) -> tuple[str, ...]:
        return tuple(
            column_aliases.get(str(column), str(column)) for column in values or ()
        )

    try:
        unique_constraints = inspector.get_unique_constraints(
            actual_table,
            schema=actual_schema,
        )
    except NotImplementedError:
        # SQL Server exposes unique constraints through index reflection.
        unique_constraints = ()
    signatures = {
        (
            True,
            normalized_columns(unique.get("column_names")),
        )
        for unique in unique_constraints
    }
    signatures.update(
        (
            bool(index.get("unique")),
            normalized_columns(index.get("column_names")),
        )
        for index in inspector.get_indexes(actual_table, schema=actual_schema)
        if not index.get("duplicates_constraint")
    )
    return {signature for signature in signatures if signature[1]}


def _type_signature(value: Any, engine: Engine) -> str:
    rendered = value.compile(dialect=engine.dialect)
    normalized = re.sub(r"\s+", " ", str(rendered).strip()).upper()
    return re.sub(r"\s+COLLATE\s+\S+", "", normalized)


def _table_summary(table: Table, engine: Engine) -> str:
    columns = ", ".join(
        f"{column.name} {_type_signature(column.type, engine)}"
        for column in table.columns
    )
    return f"table ({columns})"


def _column_summary(column: Mapping[str, Any], engine: Engine) -> str:
    nullable = "NULL" if bool(column.get("nullable", True)) else "NOT NULL"
    return f"{_type_signature(column['type'], engine)} {nullable}"


def _desired_column_summary(column: Any, engine: Engine) -> str:
    nullable = "NULL" if column.nullable else "NOT NULL"
    return f"{_type_signature(column.type, engine)} {nullable}"


def _foreign_key_text(signature: tuple[str, str, str, str]) -> str:
    local, remote_table, remote, on_delete = signature
    return f"({local}) -> {remote_table}({remote}) ON DELETE {on_delete}"


def _index_text(unique: bool, columns: tuple[str, ...]) -> str:
    kind = "unique" if unique else "index"
    return f"{kind} ({', '.join(columns)})"


def _on_delete(value: Any) -> str:
    return str(value or "NO ACTION").strip().upper().replace("_", " ")


def _qualified_name(schema: Any, name: str) -> str:
    return f"{schema}.{name}" if schema else name
