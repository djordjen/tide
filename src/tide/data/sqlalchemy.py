"""Synchronous SQLAlchemy Core persistence for compiled TIDE models."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
import re
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    delete,
    event,
    func,
    inspect,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.type_api import TypeEngine

from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.runtime.errors import ConcurrencyError, NotFoundError, TideRuntimeError


class SchemaManagementError(TideRuntimeError):
    code = "schema_management_forbidden"


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    entity: str
    object_name: str
    message: str

    def __str__(self) -> str:
        return f"{self.entity} ({self.object_name}): {self.message}"


class SchemaCompatibilityError(TideRuntimeError):
    code = "schema_incompatible"

    def __init__(self, issues: Iterable[SchemaIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(str(issue) for issue in self.issues))


class SQLAlchemyRepository:
    """Dictionary repository backed by a synchronous SQLAlchemy engine.

    Construction never performs DDL. Managed applications must call
    :meth:`create_schema` explicitly. Legacy applications cannot call it.
    """

    def __init__(self, model: ApplicationModel, bind: str | Engine):
        self.model = model
        self.mode = str(model.database["mode"])
        self.engine = bind if isinstance(bind, Engine) else _create_engine(bind)
        _enable_sqlite_foreign_keys(self.engine)
        self.metadata = MetaData()
        self._tables = _build_tables(model, self.metadata)

    def table(self, entity: str) -> Table:
        return self._tables[entity]

    def create_schema(self) -> None:
        if self.mode != "managed":
            raise SchemaManagementError(
                "legacy database mode forbids creating or changing schema objects"
            )
        self.metadata.create_all(self.engine)

    def schema_issues(self) -> tuple[SchemaIssue, ...]:
        inspector = inspect(self.engine)
        issues: list[SchemaIssue] = []
        for entity_name, table in self._tables.items():
            object_name = _qualified_name(table.schema, table.name)
            if not inspector.has_table(table.name, schema=table.schema):
                issues.append(
                    SchemaIssue(entity_name, object_name, "mapped table does not exist")
                )
                continue

            actual_columns = {
                column["name"]: column
                for column in inspector.get_columns(table.name, schema=table.schema)
            }
            for expected in table.columns:
                actual = actual_columns.get(expected.name)
                if actual is None:
                    issues.append(
                        SchemaIssue(
                            entity_name,
                            f"{object_name}.{expected.name}",
                            "mapped column does not exist",
                        )
                    )
                    continue
                if not _types_compatible(
                    expected.type, actual["type"], self.engine.dialect.name
                ):
                    issues.append(
                        SchemaIssue(
                            entity_name,
                            f"{object_name}.{expected.name}",
                            f"incompatible type {actual['type']}; expected {expected.type}",
                        )
                    )
                else:
                    capacity_issue = _type_capacity_issue(expected.type, actual["type"])
                    if capacity_issue:
                        issues.append(
                            SchemaIssue(
                                entity_name,
                                f"{object_name}.{expected.name}",
                                capacity_issue,
                            )
                        )
                if expected.nullable and not actual.get("nullable", True) and not _has_default(actual):
                    issues.append(
                        SchemaIssue(
                            entity_name,
                            f"{object_name}.{expected.name}",
                            "database requires a value but the TIDE field is optional",
                        )
                    )

            mapped_names = {column.name for column in table.columns}
            for name, actual in actual_columns.items():
                if name in mapped_names or actual.get("nullable", True) or _has_default(actual):
                    continue
                issues.append(
                    SchemaIssue(
                        entity_name,
                        f"{object_name}.{name}",
                        "unmapped database column requires a value",
                    )
                )

            expected_primary_key = [column.name for column in table.primary_key.columns]
            actual_primary_key = inspector.get_pk_constraint(
                table.name, schema=table.schema
            ).get("constrained_columns") or []
            if actual_primary_key != expected_primary_key:
                issues.append(
                    SchemaIssue(
                        entity_name,
                        object_name,
                        "primary key mismatch: "
                        f"database has {actual_primary_key}, expected {expected_primary_key}",
                    )
                )
        return tuple(issues)

    def validate_schema(self) -> None:
        issues = self.schema_issues()
        if issues:
            raise SchemaCompatibilityError(issues)

    def seed(
        self,
        entity: str,
        records: Iterable[dict[str, Any]],
        *,
        primary_key: str = "id",
    ) -> None:
        expected_key = _primary_key(self.model.entity(entity))
        if primary_key != expected_key:
            raise ValueError(
                f"seed primary key {primary_key!r} does not match {expected_key!r}"
            )
        with self.engine.begin() as connection:
            for values in records:
                self._write_entity(
                    connection,
                    entity,
                    dict(values),
                    expected_version=None,
                    is_new=True,
                )

    def all(self, entity: str) -> list[dict[str, Any]]:
        table = self.table(entity)
        primary_key = _primary_key(self.model.entity(entity))
        with self.engine.connect() as connection:
            rows = connection.execute(select(table).order_by(table.c[primary_key])).mappings()
            return [self._hydrate(connection, entity, row) for row in rows]

    def get(self, entity: str, identity: Any) -> dict[str, Any]:
        with self.engine.connect() as connection:
            return self._get(connection, entity, identity)

    def exists(self, entity: str, identity: Any) -> bool:
        table = self.table(entity)
        primary_key = _primary_key(self.model.entity(entity))
        statement = select(table.c[primary_key]).where(
            table.c[primary_key] == identity
        )
        with self.engine.connect() as connection:
            return connection.execute(statement).first() is not None

    def peek_next_identity(self, entity: str) -> int:
        table = self.table(entity)
        primary_key = _primary_key(self.model.entity(entity))
        statement = select(func.max(table.c[primary_key]))
        with self.engine.connect() as connection:
            current = connection.execute(statement).scalar_one_or_none()
        return int(current or 0) + 1

    def write(
        self,
        entity: str,
        values: dict[str, Any],
        *,
        primary_key: str,
        version_field: str | None,
        expected_version: int | None,
        is_new: bool,
    ) -> dict[str, Any]:
        expected_key = _primary_key(self.model.entity(entity))
        expected_version_field = _version_field(self.model.entity(entity))
        if primary_key != expected_key or version_field != expected_version_field:
            raise ValueError("repository write metadata does not match the compiled entity")
        with self.engine.begin() as connection:
            return self._write_entity(
                connection,
                entity,
                dict(values),
                expected_version=expected_version,
                is_new=is_new,
            )

    def dispose(self) -> None:
        self.engine.dispose()

    def _write_entity(
        self,
        connection: Connection,
        entity_name: str,
        values: dict[str, Any],
        *,
        expected_version: int | None,
        is_new: bool,
    ) -> dict[str, Any]:
        entity = self.model.entity(entity_name)
        table = self.table(entity_name)
        primary_key = _primary_key(entity)
        version_field = _version_field(entity)
        scalar_values = _scalar_values(entity, values)

        if is_new:
            if scalar_values.get(primary_key) is None:
                scalar_values.pop(primary_key, None)
            if version_field:
                scalar_values[version_field] = 1
            result = connection.execute(insert(table).values(**scalar_values))
            if primary_key not in scalar_values:
                scalar_values[primary_key] = result.inserted_primary_key[0]
            identity = scalar_values[primary_key]
        else:
            identity = scalar_values.get(primary_key)
            if identity is None:
                raise ValueError(f"{entity_name} update requires {primary_key!r}")
            update_values = dict(scalar_values)
            update_values.pop(primary_key, None)
            criteria = table.c[primary_key] == identity
            if version_field:
                criteria = criteria & (table.c[version_field] == expected_version)
                update_values[version_field] = int(expected_version or 0) + 1
            result = connection.execute(
                update(table).where(criteria).values(**update_values)
            )
            if result.rowcount != 1:
                actual = connection.execute(
                    select(table.c[version_field] if version_field else table.c[primary_key]).where(
                        table.c[primary_key] == identity
                    )
                ).scalar_one_or_none()
                if actual is None:
                    raise NotFoundError(f"{entity_name} {identity!r} was not found")
                raise ConcurrencyError(expected_version, actual if version_field else None)

        for field_name, field in entity.fields.items():
            if field.metadata["type"] != "collection" or field_name not in values:
                continue
            self._sync_collection(
                connection,
                entity,
                field,
                identity,
                values.get(field_name) or [],
            )
        return self._get(connection, entity_name, identity)

    def _sync_collection(
        self,
        connection: Connection,
        parent: NormalizedEntity,
        collection: NormalizedField,
        parent_identity: Any,
        items: Iterable[Mapping[str, Any]],
    ) -> None:
        if collection.target_entity is None:
            return
        inverse_name = collection.metadata.get("inverse")
        if not inverse_name:
            raise ValueError(
                f"collection {parent.name}.{collection.name} requires an inverse reference"
            )
        target = self.model.entity(collection.target_entity)
        target_table = self.table(target.name)
        target_key = _primary_key(target)
        inverse_column = target_table.c[inverse_name]
        existing = set(
            connection.execute(
                select(target_table.c[target_key]).where(
                    inverse_column == parent_identity
                )
            ).scalars()
        )
        retained: set[Any] = set()
        for source in items:
            item = dict(source)
            item[inverse_name] = parent_identity
            identity = item.get(target_key)
            child_is_new = identity is None or identity not in existing
            stored = self._write_entity(
                connection,
                target.name,
                item,
                expected_version=(
                    item.get(_version_field(target)) if _version_field(target) else None
                ),
                is_new=child_is_new,
            )
            retained.add(stored[target_key])

        if collection.metadata.get("orphan_delete"):
            orphaned = existing - retained
            if orphaned:
                connection.execute(
                    delete(target_table).where(target_table.c[target_key].in_(orphaned))
                )

    def _get(
        self, connection: Connection, entity_name: str, identity: Any
    ) -> dict[str, Any]:
        entity = self.model.entity(entity_name)
        table = self.table(entity_name)
        primary_key = _primary_key(entity)
        row = connection.execute(
            select(table).where(table.c[primary_key] == identity)
        ).mappings().first()
        if row is None:
            raise NotFoundError(f"{entity_name} {identity!r} was not found")
        return self._hydrate(connection, entity_name, row)

    def _hydrate(
        self,
        connection: Connection,
        entity_name: str,
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        entity = self.model.entity(entity_name)
        table = self.table(entity_name)
        values = {
            field_name: row[table.c[field_name]]
            for field_name, field in entity.fields.items()
            if _is_persisted(field)
        }
        for field_name, field in entity.fields.items():
            if field.metadata["type"] != "collection" or field.target_entity is None:
                continue
            inverse_name = field.metadata.get("inverse")
            if not inverse_name:
                values[field_name] = []
                continue
            target = self.model.entity(field.target_entity)
            target_table = self.table(target.name)
            statement = select(target_table).where(
                target_table.c[inverse_name] == values[_primary_key(entity)]
            )
            order_by = field.metadata.get("order_by")
            if order_by:
                statement = statement.order_by(target_table.c[order_by])
            children = []
            for child_row in connection.execute(statement).mappings():
                child = self._hydrate(connection, target.name, child_row)
                child.pop(inverse_name, None)
                children.append(child)
            values[field_name] = children
        return values


def _create_engine(url: str) -> Engine:
    parsed = make_url(url)
    options: dict[str, Any] = {"future": True}
    if parsed.get_backend_name() == "sqlite" and parsed.database in {
        None,
        "",
        ":memory:",
    }:
        options.update(
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(url, **options)


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def _build_tables(
    model: ApplicationModel, metadata: MetaData
) -> dict[str, Table]:
    tables: dict[str, Table] = {}
    physical_names: set[tuple[str | None, str]] = set()
    for entity_name, entity in model.entities.items():
        storage = entity.metadata.get("storage") or {}
        table_name = storage.get("table") or _managed_table_name(entity_name)
        schema = storage.get("schema")
        physical_name = (schema, table_name)
        if physical_name in physical_names:
            raise ValueError(f"multiple entities map to {_qualified_name(schema, table_name)}")
        physical_names.add(physical_name)

        columns = [
            _build_column(model, field)
            for field in entity.fields.values()
            if _is_persisted(field)
        ]
        tables[entity_name] = Table(table_name, metadata, *columns, schema=schema)
    return tables


def _build_column(model: ApplicationModel, field: NormalizedField) -> Column[Any]:
    data_type = _sql_type(model, field)
    arguments: list[Any] = []
    if field.metadata["type"] == "reference" and field.target_entity:
        target = model.entity(field.target_entity)
        target_key = target.field(_primary_key(target))
        target_storage = target.metadata.get("storage") or {}
        target_table = target_storage.get("table") or _managed_table_name(target.name)
        target_schema = target_storage.get("schema")
        target_column = _column_name(target_key)
        arguments.append(
            ForeignKey(
                _qualified_name(target_schema, target_table, target_column),
                ondelete=str(field.metadata.get("on_delete", "restrict")).upper().replace("_", " "),
            )
        )
    return Column(
        _column_name(field),
        data_type,
        *arguments,
        key=field.name,
        primary_key=bool(field.metadata.get("primary_key")),
        nullable=not bool(field.metadata.get("required") or field.metadata.get("primary_key")),
        unique=bool(field.metadata.get("unique")),
    )


def _sql_type(model: ApplicationModel, field: NormalizedField) -> TypeEngine[Any]:
    metadata = field.metadata
    field_type = metadata["type"]
    if field_type == "reference" and field.target_entity:
        target = model.entity(field.target_entity)
        return _sql_type(model, target.field(_primary_key(target)))
    if field_type == "integer":
        return Integer()
    if field_type == "decimal":
        precision = metadata.get("precision")
        scale = metadata.get("scale")
        return Numeric(precision=precision, scale=scale, asdecimal=True)
    if field_type == "boolean":
        return Boolean()
    if field_type == "date":
        return Date()
    if field_type == "datetime":
        return DateTime(timezone=True)
    if field_type == "choice":
        length = metadata.get("length") or max(
            (len(choice) for choice in metadata.get("choices", ())), default=255
        )
        return String(length)
    return String(metadata.get("length") or 255)


def _scalar_values(entity: NormalizedEntity, values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field_name: value
        for field_name, value in values.items()
        if field_name in entity.fields and _is_persisted(entity.fields[field_name])
    }


def _is_persisted(field: NormalizedField) -> bool:
    if field.metadata["type"] == "collection":
        return False
    computed = field.metadata.get("computed")
    return not computed or computed.get("materialization") != "virtual"


def _column_name(field: NormalizedField) -> str:
    if field.metadata["type"] == "reference":
        return str(field.metadata.get("storage") or f"{field.name}_id")
    return str(field.metadata.get("column") or field.name)


def _primary_key(entity: NormalizedEntity) -> str:
    return next(
        name for name, field in entity.fields.items() if field.metadata.get("primary_key")
    )


def _version_field(entity: NormalizedEntity) -> str | None:
    return next(
        (
            name
            for name, field in entity.fields.items()
            if field.metadata.get("concurrency_token")
        ),
        None,
    )


def _qualified_name(schema: str | None, table: str, column: str | None = None) -> str:
    parts = [part for part in (schema, table, column) if part]
    return ".".join(parts)


def _managed_table_name(entity_name: str) -> str:
    return "_".join(
        re.sub(r"(?<!^)(?=[A-Z])", "_", part).lower()
        for part in entity_name.split(".")
    )


def _has_default(column: Mapping[str, Any]) -> bool:
    return any(
        column.get(name) is not None
        for name in ("default", "computed", "identity")
    ) or bool(column.get("autoincrement"))


def _types_compatible(
    expected: TypeEngine[Any], actual: TypeEngine[Any], dialect: str
) -> bool:
    if isinstance(expected, String):
        return isinstance(actual, String)
    if isinstance(expected, Numeric):
        return isinstance(actual, Numeric)
    if isinstance(expected, Boolean):
        return isinstance(actual, Boolean) or (
            dialect == "sqlite" and isinstance(actual, Integer)
        )
    if isinstance(expected, DateTime):
        return isinstance(actual, DateTime)
    if isinstance(expected, Date):
        return isinstance(actual, Date) and not isinstance(actual, DateTime)
    if isinstance(expected, Integer):
        return isinstance(actual, Integer) and not isinstance(actual, Boolean)
    with suppress(AttributeError):
        return expected._type_affinity is actual._type_affinity
    return type(expected) is type(actual)


def _type_capacity_issue(
    expected: TypeEngine[Any], actual: TypeEngine[Any]
) -> str | None:
    if isinstance(expected, String) and isinstance(actual, String):
        if expected.length and actual.length and actual.length < expected.length:
            return (
                f"database length {actual.length} is smaller than required "
                f"length {expected.length}"
            )
    if isinstance(expected, Numeric) and isinstance(actual, Numeric):
        if expected.precision and actual.precision and actual.precision < expected.precision:
            return (
                f"database precision {actual.precision} is smaller than required "
                f"precision {expected.precision}"
            )
        if expected.scale is not None and actual.scale is not None and actual.scale < expected.scale:
            return (
                f"database scale {actual.scale} is smaller than required scale {expected.scale}"
            )
    return None
