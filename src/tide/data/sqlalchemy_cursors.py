"""SQLAlchemy-backed shared storage for opaque query cursors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    Unicode,
    delete,
    inspect,
    insert,
    select,
)
from sqlalchemy.engine import Connection, Engine, URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tide.data.repository import FilterCondition, SortField
from tide.data.sqlalchemy import (
    SchemaCompatibilityError,
    SchemaIssue,
    SchemaManagementError,
    _create_engine,
)
from tide.runtime.errors import CursorStoreError, InvalidQueryCursor
from tide.services.action_store import (
    deserialize_action_value,
    serialize_action_value,
)
from tide.services.cursors import CURSOR_VERSION, CursorShape, CursorState


class SQLAlchemyCursorStore:
    """Durable, process-shared cursor state with hashed bearer tokens.

    Construction never emits DDL. ``mode='managed'`` enables an explicit
    :meth:`create_schema`; the default ``legacy`` mode only validates and uses a
    pre-existing table.
    """

    def __init__(
        self,
        bind: str | URL | Engine,
        *,
        mode: str = "legacy",
        schema: str | None = None,
        ttl_seconds: float = 900,
        max_entries: int = 10_000,
        max_state_bytes: int = 65_536,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if mode not in {"managed", "legacy"}:
            raise ValueError("cursor store mode must be 'managed' or 'legacy'")
        if ttl_seconds <= 0:
            raise ValueError("cursor TTL must be positive")
        if max_entries < 1:
            raise ValueError("cursor store capacity must be positive")
        if max_state_bytes < 1:
            raise ValueError("cursor state size limit must be positive")
        self.mode = mode
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.max_state_bytes = max_state_bytes
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._owns_engine = not isinstance(bind, Engine)
        self.engine = bind if isinstance(bind, Engine) else _create_engine(bind)
        self.metadata = MetaData()
        self.cursor_table = Table(
            "tide_query_cursor",
            self.metadata,
            Column("token_hash", String(64), primary_key=True),
            Column("state_json", Unicode(), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("expires_at", DateTime(timezone=True), nullable=False),
            schema=schema,
        )
        Index("ix_tide_query_cursor_expiry", self.cursor_table.c.expires_at)

    def create_schema(self) -> None:
        if self.mode != "managed":
            raise SchemaManagementError(
                "legacy cursor-store mode forbids creating or changing schema objects"
            )
        self.metadata.create_all(self.engine)

    def schema_issues(self) -> tuple[SchemaIssue, ...]:
        inspector = inspect(self.engine)
        table = self.cursor_table
        object_name = f"{table.schema}.{table.name}" if table.schema else table.name
        entity = "tide.cursor-store"
        if not inspector.has_table(table.name, schema=table.schema):
            return (SchemaIssue(entity, object_name, "mapped table does not exist"),)
        actual_columns = {
            str(column["name"])
            for column in inspector.get_columns(table.name, schema=table.schema)
        }
        return tuple(
            SchemaIssue(
                entity,
                f"{object_name}.{column.name}",
                "mapped column does not exist",
            )
            for column in table.columns
            if column.name not in actual_columns
        )

    def validate_schema(self) -> None:
        issues = self.schema_issues()
        if issues:
            raise SchemaCompatibilityError(issues)

    def issue(self, state: CursorState) -> str:
        if state.version != CURSOR_VERSION:
            raise ValueError(f"unsupported cursor version {state.version}")
        try:
            state_json = _serialize_state(state)
        except (TypeError, ValueError) as error:
            raise ValueError("cursor state contains an unsupported value") from error
        if len(state_json.encode("utf-8")) > self.max_state_bytes:
            raise ValueError(
                f"serialized cursor state exceeds {self.max_state_bytes} bytes"
            )
        now = self._now()
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        for _attempt in range(10):
            token = self._token_factory()
            if not isinstance(token, str) or not token:
                continue
            try:
                with self.engine.connect() as base_connection:
                    connection = base_connection.execution_options(
                        isolation_level="SERIALIZABLE"
                    )
                    with connection.begin():
                        self._purge_expired(connection, now)
                        connection.execute(
                            insert(self.cursor_table).values(
                                token_hash=_token_hash(token),
                                state_json=state_json,
                                created_at=now,
                                expires_at=expires_at,
                            )
                        )
                        self._trim_capacity(connection)
                return token
            except IntegrityError:
                continue
            except SQLAlchemyError as error:
                raise CursorStoreError("could not persist query cursor") from error
        raise RuntimeError("could not allocate a unique query cursor")

    def resolve(self, token: str) -> CursorState:
        if not isinstance(token, str) or not token:
            raise InvalidQueryCursor
        try:
            with self.engine.connect() as connection:
                row = (
                    connection.execute(
                        select(self.cursor_table).where(
                            self.cursor_table.c.token_hash == _token_hash(token)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as error:
            raise CursorStoreError("could not read query cursor") from error
        if row is None:
            raise InvalidQueryCursor
        if _database_datetime(row["expires_at"]) <= self._now():
            self._delete_token_hash(str(row["token_hash"]))
            raise InvalidQueryCursor
        try:
            return _deserialize_state(str(row["state_json"]))
        except (TypeError, ValueError) as error:
            raise CursorStoreError("stored query cursor state is invalid") from error

    def purge_expired(self) -> int:
        try:
            with self.engine.begin() as connection:
                result = self._purge_expired(connection, self._now())
        except SQLAlchemyError as error:
            raise CursorStoreError("could not purge expired query cursors") from error
        return max(int(result.rowcount or 0), 0)

    def dispose(self) -> None:
        if self._owns_engine:
            self.engine.dispose()

    def _purge_expired(self, connection: Connection, now: datetime) -> Any:
        return connection.execute(
            delete(self.cursor_table).where(self.cursor_table.c.expires_at <= now)
        )

    def _trim_capacity(self, connection: Connection) -> None:
        hashes = (
            connection.execute(
                select(self.cursor_table.c.token_hash)
                .order_by(
                    self.cursor_table.c.created_at.desc(),
                    self.cursor_table.c.token_hash.desc(),
                )
                .offset(self.max_entries)
            )
            .scalars()
            .all()
        )
        if hashes:
            connection.execute(
                delete(self.cursor_table).where(
                    self.cursor_table.c.token_hash.in_(hashes)
                )
            )

    def _delete_token_hash(self, token_hash: str) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.cursor_table).where(
                        self.cursor_table.c.token_hash == token_hash
                    )
                )
        except SQLAlchemyError as error:
            raise CursorStoreError("could not remove expired query cursor") from error

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise CursorStoreError("cursor store timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


def _serialize_state(state: CursorState) -> str:
    return serialize_action_value(
        {
            "version": state.version,
            "shape": {
                "model": list(state.shape.model),
                "entity": state.shape.entity,
                "filters": [
                    {
                        "field": condition.field,
                        "operator": condition.operator,
                        "value": condition.value,
                    }
                    for condition in state.shape.filters
                ],
                "sort": [
                    {"field": item.field, "descending": item.descending}
                    for item in state.shape.sort
                ],
                "limit": state.shape.limit,
                "principal": [
                    state.shape.principal[0],
                    list(state.shape.principal[1]),
                ],
            },
            "values": list(state.values),
        }
    )


def _deserialize_state(value: str) -> CursorState:
    payload = deserialize_action_value(value)
    if not isinstance(payload, Mapping):
        raise ValueError("cursor state must be a mapping")
    version = _required_integer(payload, "version")
    shape = _required_mapping(payload, "shape")
    model = _required_sequence(shape, "model", length=3)
    if not all(isinstance(item, str) for item in model):
        raise ValueError("cursor model identity is invalid")
    entity = _required_string(shape, "entity")
    filters = _required_sequence(shape, "filters")
    sort = _required_sequence(shape, "sort")
    principal = _required_sequence(shape, "principal", length=2)
    if not isinstance(principal[0], str) or not isinstance(principal[1], list):
        raise ValueError("cursor principal is invalid")
    if not all(isinstance(permission, str) for permission in principal[1]):
        raise ValueError("cursor permissions are invalid")
    values = _required_sequence(payload, "values")
    return CursorState(
        version=version,
        shape=CursorShape(
            model=(str(model[0]), str(model[1]), str(model[2])),
            entity=entity,
            filters=tuple(_filter_condition(item) for item in filters),
            sort=tuple(_sort_field(item) for item in sort),
            limit=_required_integer(shape, "limit"),
            principal=(str(principal[0]), tuple(principal[1])),
        ),
        values=tuple(values),
    )


def _filter_condition(value: Any) -> FilterCondition:
    if not isinstance(value, Mapping) or "value" not in value:
        raise ValueError("cursor filter is invalid")
    return FilterCondition(
        field=_required_string(value, "field"),
        operator=_required_string(value, "operator"),
        value=value["value"],
    )


def _sort_field(value: Any) -> SortField:
    if not isinstance(value, Mapping):
        raise ValueError("cursor sort is invalid")
    descending = value.get("descending")
    if not isinstance(descending, bool):
        raise ValueError("cursor sort direction is invalid")
    return SortField(
        field=_required_string(value, "field"),
        descending=descending,
    )


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise ValueError(f"cursor {key} must be a mapping")
    return result


def _required_sequence(
    value: Mapping[str, Any],
    key: str,
    *,
    length: int | None = None,
) -> list[Any]:
    result = value.get(key)
    if not isinstance(result, list) or (length is not None and len(result) != length):
        raise ValueError(f"cursor {key} must be a sequence")
    return result


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise ValueError(f"cursor {key} must be a string")
    return result


def _required_integer(value: Mapping[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool):
        raise ValueError(f"cursor {key} must be an integer")
    return result


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _database_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
