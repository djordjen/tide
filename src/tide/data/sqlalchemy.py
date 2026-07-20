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
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Unicode,
    and_,
    case,
    create_engine,
    delete,
    event,
    func,
    inspect,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine, URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.type_api import TypeEngine

from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.data.repository import (
    DeleteCollection,
    DeleteReference,
    FilterCondition,
    QuerySpec,
    RelationshipLoadPlan,
    RowPolicyMismatch,
    SortField,
)
from tide.data.sql_expressions import QueryTranslationError, translate_expression
from tide.runtime.errors import (
    ConcurrencyError,
    DeleteRestricted,
    NotFoundError,
    RelationshipExpansionLimit,
    TideRuntimeError,
)


class SchemaManagementError(TideRuntimeError):
    code = "schema_management_forbidden"


class DatabaseDriverError(TideRuntimeError):
    code = "database_driver_unavailable"


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

    def __init__(self, model: ApplicationModel, bind: str | URL | Engine):
        self.model = model
        self.mode = str(model.database["mode"])
        self.engine = bind if isinstance(bind, Engine) else _create_engine(bind)
        _enable_sqlite_foreign_keys(self.engine)
        self.metadata = MetaData()
        self._tables = _build_tables(
            model,
            self.metadata,
            dialect_name=self.engine.dialect.name,
        )

    def table(self, entity: str) -> Table:
        return self._tables[entity]

    def create_schema(self) -> None:
        if self.mode != "managed":
            raise SchemaManagementError(
                "legacy database mode forbids creating or changing schema objects"
            )
        self.metadata.create_all(self.engine)

    def check_readiness(self) -> None:
        """Verify connectivity, mapped schema compatibility, and SQL policy support."""
        with self.engine.connect() as connection:
            connection.execute(select(1)).scalar_one()
        self.validate_schema()
        self.validate_query_support()

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

    def validate_query_support(self) -> None:
        relationship_criteria = _model_read_criteria(self.model)
        for policy in self.model.row_policies:
            entity = self.model.entity(str(policy["entity"]))
            table = self.table(entity.name)
            try:
                translate_expression(
                    str(policy["criteria"]),
                    model=self.model,
                    entity=entity,
                    columns=table.c,
                    tables=self._tables,
                    relationship_criteria=relationship_criteria,
                )
            except QueryTranslationError as error:
                raise QueryTranslationError(
                    f"row policy {policy['id']!r} cannot run in SQL: {error}"
                ) from error

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
            rows = (
                connection.execute(select(table).order_by(table.c[primary_key]))
                .mappings()
                .all()
            )
            return [self._hydrate(connection, entity, row) for row in rows]

    def query(
        self,
        entity: str,
        query: QuerySpec,
        *,
        row_criteria: tuple[str, ...] = (),
        relationships: RelationshipLoadPlan | None = None,
    ) -> list[dict[str, Any]]:
        statement = self._query_statement(
            entity,
            query,
            row_criteria=row_criteria,
            relationships=relationships,
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
            return [
                self._hydrate(
                    connection,
                    entity,
                    row,
                    relationships=relationships,
                    depth=0,
                )
                for row in rows
            ]

    def _query_statement(
        self,
        entity: str,
        query: QuerySpec,
        *,
        row_criteria: tuple[str, ...] = (),
        relationships: RelationshipLoadPlan | None = None,
    ) -> Any:
        if query.cursor is not None:
            raise ValueError("opaque cursors must be resolved by RecordsService")
        if query.limit < 1 or query.limit > 501:
            raise ValueError("repository query limit must be between 1 and 501")
        if query.after is not None and not query.sort:
            raise ValueError("query cursor boundary requires an effective sort")
        if query.after is not None and len(query.after) != len(query.sort):
            raise ValueError("query cursor boundary does not match the effective sort")
        normalized_entity = self.model.entity(entity)
        table = self.table(entity)
        predicates = [
            translate_expression(
                criteria,
                model=self.model,
                entity=normalized_entity,
                columns=table.c,
                tables=self._tables,
                relationship_criteria=_plan_relationship_criteria(relationships),
            )
            for criteria in row_criteria
        ]
        predicates.extend(
            _filter_predicate(normalized_entity, table, condition)
            for condition in query.filters
        )
        if query.after is not None:
            predicates.append(
                _cursor_predicate(table, query.sort, query.after)
            )
        statement = select(table)
        if predicates:
            statement = statement.where(and_(*predicates))
        for sort in query.sort:
            column = table.c.get(sort.field)
            if column is None:
                raise QueryTranslationError(
                    f"field {entity}.{sort.field} is not stored and cannot be sorted in SQL"
                )
            null_rank = _sort_null_rank(column, sort.descending)
            statement = statement.order_by(
                null_rank,
                column.desc() if sort.descending else column.asc(),
            )
        return statement.limit(query.limit)

    def get(
        self,
        entity: str,
        identity: Any,
        *,
        row_criteria: tuple[str, ...] = (),
        relationships: RelationshipLoadPlan | None = None,
    ) -> dict[str, Any]:
        with self.engine.connect() as connection:
            return self._get(
                connection,
                entity,
                identity,
                row_criteria=row_criteria,
                relationships=relationships,
            )

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
        row_criteria: tuple[str, ...] = (),
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
                row_criteria=row_criteria,
            )

    def delete(
        self,
        entity: str,
        identity: Any,
        *,
        primary_key: str,
        version_field: str | None,
        expected_version: int | None,
        row_criteria: tuple[str, ...] = (),
        references: tuple[DeleteReference, ...] = (),
        collections: tuple[DeleteCollection, ...] = (),
    ) -> None:
        del collections
        normalized = self.model.entity(entity)
        expected_key = _primary_key(normalized)
        expected_version_field = _version_field(normalized)
        if primary_key != expected_key or version_field != expected_version_field:
            raise ValueError("repository delete metadata does not match the compiled entity")
        table = self.table(entity)
        policy_predicates = tuple(
            translate_expression(
                policy,
                model=self.model,
                entity=normalized,
                columns=table.c,
                tables=self._tables,
                relationship_criteria=_model_read_criteria(self.model),
            )
            for policy in row_criteria
        )
        try:
            with self.engine.begin() as connection:
                current = connection.execute(
                    select(
                        table.c[primary_key],
                        *((table.c[version_field],) if version_field else ()),
                    ).where(table.c[primary_key] == identity)
                ).first()
                if current is None:
                    raise NotFoundError(f"{entity} {identity!r} was not found")
                if policy_predicates and connection.execute(
                    select(table.c[primary_key]).where(
                        table.c[primary_key] == identity,
                        and_(*policy_predicates),
                    )
                ).first() is None:
                    raise RowPolicyMismatch
                actual_version = (
                    current._mapping[version_field] if version_field else None
                )
                if version_field and expected_version != actual_version:
                    raise ConcurrencyError(expected_version, actual_version)

                root_criteria = table.c[primary_key] == identity
                if policy_predicates:
                    root_criteria = root_criteria & and_(*policy_predicates)
                if version_field:
                    root_criteria = root_criteria & (
                        table.c[version_field] == expected_version
                    )
                self._delete_entity(
                    connection,
                    entity,
                    identity,
                    references=references,
                    visited=set(),
                    root_criteria=root_criteria,
                    expected_version=expected_version,
                )
        except DeleteRestricted:
            raise
        except IntegrityError as error:
            raise DeleteRestricted(entity, identity) from error

    def dispose(self) -> None:
        self.engine.dispose()

    def _delete_entity(
        self,
        connection: Connection,
        entity: str,
        identity: Any,
        *,
        references: tuple[DeleteReference, ...],
        visited: set[tuple[str, Any]],
        root_criteria: ColumnElement[bool] | None = None,
        expected_version: int | None = None,
    ) -> None:
        key = entity, identity
        if key in visited:
            return
        visited.add(key)
        for reference in references:
            if reference.target_entity != entity:
                continue
            source = self.table(reference.source_entity)
            related = tuple(
                connection.execute(
                    select(source.c[reference.source_primary_key]).where(
                        source.c[reference.source_field] == identity
                    )
                ).scalars()
            )
            if not related:
                continue
            relationship = f"{reference.source_entity}.{reference.source_field}"
            if reference.on_delete == "restrict":
                raise DeleteRestricted(entity, identity, relationship)
            if reference.on_delete == "set_null":
                connection.execute(
                    update(source)
                    .where(source.c[reference.source_field] == identity)
                    .values({reference.source_field: None})
                )
                continue
            for related_identity in related:
                self._delete_entity(
                    connection,
                    reference.source_entity,
                    related_identity,
                    references=references,
                    visited=visited,
                )

        normalized = self.model.entity(entity)
        table = self.table(entity)
        primary_key = _primary_key(normalized)
        criteria = (
            root_criteria
            if root_criteria is not None
            else table.c[primary_key] == identity
        )
        result = connection.execute(delete(table).where(criteria))
        if result.rowcount == 1 or root_criteria is None:
            return
        version_field = _version_field(normalized)
        current = connection.execute(
            select(
                table.c[primary_key],
                *((table.c[version_field],) if version_field else ()),
            ).where(table.c[primary_key] == identity)
        ).first()
        if current is None:
            raise NotFoundError(f"{entity} {identity!r} was not found")
        actual_version = current._mapping[version_field] if version_field else None
        if actual_version == expected_version:
            raise RowPolicyMismatch
        raise ConcurrencyError(expected_version, actual_version)

    def _write_entity(
        self,
        connection: Connection,
        entity_name: str,
        values: dict[str, Any],
        *,
        expected_version: int | None,
        is_new: bool,
        row_criteria: tuple[str, ...] = (),
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
            policy_predicates = [
                translate_expression(
                    policy,
                    model=self.model,
                    entity=entity,
                    columns=table.c,
                    tables=self._tables,
                    relationship_criteria=_model_read_criteria(self.model),
                )
                for policy in row_criteria
            ]
            if policy_predicates:
                criteria = criteria & and_(*policy_predicates)
            if version_field:
                criteria = criteria & (table.c[version_field] == expected_version)
                update_values[version_field] = int(expected_version or 0) + 1
            result = connection.execute(
                update(table).where(criteria).values(**update_values)
            )
            if result.rowcount != 1:
                current = connection.execute(
                    select(table.c[primary_key], *(
                        (table.c[version_field],) if version_field else ()
                    )).where(table.c[primary_key] == identity)
                ).first()
                if current is None:
                    raise NotFoundError(f"{entity_name} {identity!r} was not found")
                if policy_predicates and connection.execute(
                    select(table.c[primary_key]).where(
                        table.c[primary_key] == identity,
                        and_(*policy_predicates),
                    )
                ).first() is None:
                    raise RowPolicyMismatch
                actual = current._mapping[version_field] if version_field else None
                raise ConcurrencyError(expected_version, actual)

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
        self,
        connection: Connection,
        entity_name: str,
        identity: Any,
        *,
        row_criteria: tuple[str, ...] = (),
        relationships: RelationshipLoadPlan | None = None,
    ) -> dict[str, Any]:
        entity = self.model.entity(entity_name)
        table = self.table(entity_name)
        primary_key = _primary_key(entity)
        predicates = [table.c[primary_key] == identity]
        predicates.extend(
            translate_expression(
                criteria,
                model=self.model,
                entity=entity,
                columns=table.c,
                tables=self._tables,
                relationship_criteria=_plan_relationship_criteria(relationships),
            )
            for criteria in row_criteria
        )
        row = connection.execute(
            select(table).where(and_(*predicates))
        ).mappings().first()
        if row is None:
            if row_criteria and connection.execute(
                select(table.c[primary_key]).where(table.c[primary_key] == identity)
            ).first() is not None:
                raise RowPolicyMismatch
            raise NotFoundError(f"{entity_name} {identity!r} was not found")
        return self._hydrate(
            connection,
            entity_name,
            row,
            relationships=relationships,
            depth=0,
        )

    def _hydrate(
        self,
        connection: Connection,
        entity_name: str,
        row: Mapping[str, Any],
        *,
        relationships: RelationshipLoadPlan | None = None,
        depth: int = 0,
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
            load = (
                relationships.for_field(entity_name, field_name)
                if relationships is not None
                else None
            )
            if relationships is not None and load is None:
                values[field_name] = []
                continue
            relationship = f"{entity_name}.{field_name}"
            inverse_name = field.metadata.get("inverse")
            if not inverse_name:
                values[field_name] = []
                continue
            target = self.model.entity(field.target_entity)
            target_table = self.table(target.name)
            statement = select(target_table).where(
                target_table.c[inverse_name] == values[_primary_key(entity)]
            )
            if load is not None:
                if load.target_entity != target.name:
                    raise ValueError(
                        f"relationship load target for {relationship!r} does not match the model"
                    )
                relationship_predicates = [
                    translate_expression(
                        criteria,
                        model=self.model,
                        entity=target,
                        columns=target_table.c,
                        tables=self._tables,
                        relationship_criteria=_plan_relationship_criteria(
                            relationships
                        ),
                    )
                    for criteria in relationships.criteria_for_entity(
                        load.target_entity
                    )
                ]
                if relationship_predicates:
                    statement = statement.where(and_(*relationship_predicates))
            order_by = field.metadata.get("order_by")
            if order_by:
                statement = statement.order_by(target_table.c[order_by])
            if relationships is not None and depth >= relationships.max_depth:
                if connection.execute(statement.limit(1)).first() is not None:
                    raise RelationshipExpansionLimit(relationship, "depth")
                values[field_name] = []
                continue
            if relationships is not None:
                statement = statement.limit(relationships.max_items + 1)
            children = []
            child_rows = connection.execute(statement).mappings().all()
            if relationships is not None and len(child_rows) > relationships.max_items:
                raise RelationshipExpansionLimit(relationship, "item")
            for child_row in child_rows:
                child = self._hydrate(
                    connection,
                    target.name,
                    child_row,
                    relationships=relationships,
                    depth=depth + 1,
                )
                child.pop(inverse_name, None)
                children.append(child)
            values[field_name] = children
        return values


def _create_engine(url: str | URL) -> Engine:
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
    elif parsed.get_backend_name() == "mssql":
        options["pool_pre_ping"] = True
    try:
        return create_engine(url, **options)
    except ModuleNotFoundError as error:
        if parsed.get_backend_name() == "mssql" and error.name == "pyodbc":
            raise DatabaseDriverError(
                "SQL Server requires the optional 'sqlserver' dependency and "
                "Microsoft ODBC Driver 17 or later; install "
                "tide-framework[sqlserver]"
            ) from error
        raise


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
    model: ApplicationModel,
    metadata: MetaData,
    *,
    dialect_name: str,
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
            _build_column(
                model,
                field,
                filtered_unique=_requires_filtered_unique(field, dialect_name),
            )
            for field in entity.fields.values()
            if _is_persisted(field)
        ]
        table = Table(table_name, metadata, *columns, schema=schema)
        for field in entity.fields.values():
            if not _is_persisted(field) or not _requires_filtered_unique(
                field, dialect_name
            ):
                continue
            column = table.c[field.name]
            Index(
                _filtered_unique_index_name(table_name, column.name),
                column,
                unique=True,
                mssql_where=column.is_not(None),
            )
        tables[entity_name] = table
    return tables


def _build_column(
    model: ApplicationModel,
    field: NormalizedField,
    *,
    filtered_unique: bool = False,
) -> Column[Any]:
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
                ondelete=_sql_on_delete(field.metadata.get("on_delete")),
                link_to_name=True,
            )
        )
    return Column(
        _column_name(field),
        data_type,
        *arguments,
        key=field.name,
        primary_key=bool(field.metadata.get("primary_key")),
        nullable=not bool(field.metadata.get("required") or field.metadata.get("primary_key")),
        unique=bool(field.metadata.get("unique")) and not filtered_unique,
    )


def _requires_filtered_unique(field: NormalizedField, dialect_name: str) -> bool:
    return bool(
        dialect_name == "mssql"
        and field.metadata.get("unique")
        and not field.metadata.get("required")
        and not field.metadata.get("primary_key")
    )


def _filtered_unique_index_name(table_name: str, column_name: str) -> str:
    return f"ux_{table_name}_{column_name}_not_null"[:128]


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
        return Unicode(length)
    return Unicode(metadata.get("length") or 255)


def _scalar_values(entity: NormalizedEntity, values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field_name: value
        for field_name, value in values.items()
        if field_name in entity.fields and _is_persisted(entity.fields[field_name])
    }


def _filter_predicate(
    entity: NormalizedEntity,
    table: Table,
    condition: FilterCondition,
) -> ColumnElement[bool]:
    column = table.c.get(condition.field)
    if column is None:
        raise QueryTranslationError(
            f"field {entity.name}.{condition.field} is not stored and cannot be filtered in SQL"
        )
    value = condition.value
    if condition.operator == "eq":
        return column.is_(None) if value is None else column == value
    if condition.operator == "ne":
        return column.is_not(None) if value is None else column != value
    if condition.operator == "lt":
        return column < value
    if condition.operator == "lte":
        return column <= value
    if condition.operator == "gt":
        return column > value
    if condition.operator == "gte":
        return column >= value
    if condition.operator == "contains":
        return column.contains(value)
    if condition.operator == "icontains":
        return func.lower(column).contains(value.casefold())
    raise ValueError(f"unsupported filter operator {condition.operator!r}")


def _cursor_predicate(
    table: Table,
    sort_fields: tuple[SortField, ...],
    boundary: tuple[Any, ...],
) -> ColumnElement[bool]:
    prefix: list[ColumnElement[bool]] = []
    branches: list[ColumnElement[bool]] = []
    for sort, boundary_value in zip(sort_fields, boundary):
        column = table.c.get(sort.field)
        if column is None:
            raise QueryTranslationError(
                f"field {table.name}.{sort.field} is not stored and cannot be paged in SQL"
            )
        null_rank = _sort_null_rank(column, sort.descending)
        boundary_rank = _cursor_null_rank(boundary_value, sort.descending)
        branches.append(and_(*prefix, null_rank > boundary_rank))
        prefix.append(null_rank == boundary_rank)
        if boundary_value is None:
            prefix.append(column.is_(None))
            continue
        comparison = (
            column < boundary_value
            if sort.descending
            else column > boundary_value
        )
        branches.append(and_(*prefix, comparison))
        prefix.append(column == boundary_value)
    return or_(*branches)


def _sort_null_rank(
    column: ColumnElement[Any],
    descending: bool,
) -> ColumnElement[int]:
    return case(
        (column.is_(None), 0 if descending else 1),
        else_=1 if descending else 0,
    )


def _cursor_null_rank(value: Any, descending: bool) -> int:
    if descending:
        return 0 if value is None else 1
    return 1 if value is None else 0


def _plan_relationship_criteria(
    plan: RelationshipLoadPlan | None,
) -> dict[str, tuple[str, ...]]:
    if plan is None:
        return {}
    return dict(plan.entity_criteria)


def _model_read_criteria(
    model: ApplicationModel,
) -> dict[str, tuple[str, ...]]:
    return {
        entity_name: tuple(
            str(policy["criteria"])
            for policy in model.row_policies
            if policy["entity"] == entity_name and "read" in policy["operations"]
        )
        for entity_name in model.entities
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


def _sql_on_delete(value: Any) -> str:
    action = str(value or "restrict").lower()
    return {
        "restrict": "NO ACTION",
        "cascade": "CASCADE",
        "set_null": "SET NULL",
    }[action]


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
