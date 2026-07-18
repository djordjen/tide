"""SQLAlchemy-backed action idempotency and audit storage."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Unicode,
    UnicodeText,
    inspect,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tide.data.sqlalchemy import (
    SchemaCompatibilityError,
    SchemaIssue,
    SchemaManagementError,
    _create_engine,
)
from tide.runtime.errors import ActionStoreError
from tide.services.action_store import (
    ActionAuditEvent,
    AuditFieldChange,
    AuditOutcome,
    AuditValueMode,
    IdempotencyClaim,
    IdempotencyRecord,
    IdempotencyStatus,
    RecordAuditEvent,
    RecordAuditOperation,
    deserialize_action_value,
    serialize_action_value,
)


class SQLAlchemyActionExecutionStore:
    """Durable action state in an explicitly owned SQL schema.

    Construction never emits DDL. ``mode='managed'`` permits an explicit
    :meth:`create_schema`; ``mode='legacy'`` is the safe default and only permits
    compatibility validation against pre-existing tables.
    """

    def __init__(
        self,
        bind: str | URL | Engine,
        *,
        mode: str = "legacy",
        schema: str | None = None,
    ) -> None:
        if mode not in {"managed", "legacy"}:
            raise ValueError("action store mode must be 'managed' or 'legacy'")
        self.mode = mode
        self._owns_engine = not isinstance(bind, Engine)
        self.engine = bind if isinstance(bind, Engine) else _create_engine(bind)
        self.metadata = MetaData()
        self.idempotency_table = Table(
            "tide_action_idempotency",
            self.metadata,
            Column("key", Unicode(255), primary_key=True),
            Column("fingerprint", String(64), nullable=False),
            Column("entity", Unicode(255), nullable=False),
            Column("identity_json", Unicode(2048), nullable=False),
            Column("principal", Unicode(512), nullable=False),
            Column("correlation_id", Unicode(255), nullable=False),
            Column("status", String(16), nullable=False),
            Column("started_at", DateTime(timezone=True), nullable=False),
            Column("finished_at", DateTime(timezone=True)),
            Column("error_code", String(64)),
            schema=schema,
        )
        self.audit_table = Table(
            "tide_action_audit",
            self.metadata,
            Column(
                "sequence",
                BigInteger().with_variant(Integer(), "sqlite"),
                Identity(),
                primary_key=True,
            ),
            Column("event_id", String(64), nullable=False, unique=True),
            Column("entity", Unicode(255), nullable=False),
            Column("action", Unicode(255), nullable=False),
            Column("identity_json", Unicode(2048), nullable=False),
            Column("principal", Unicode(512), nullable=False),
            Column("channel", String(32), nullable=False),
            Column("correlation_id", Unicode(255), nullable=False),
            Column("started_at", DateTime(timezone=True), nullable=False),
            Column("outcome", String(16), nullable=False),
            Column("finished_at", DateTime(timezone=True)),
            Column("error_code", String(64)),
            Column("idempotency_key_hash", String(64)),
            schema=schema,
        )
        Index(
            "ix_tide_action_audit_correlation",
            self.audit_table.c.correlation_id,
        )
        Index(
            "ix_tide_action_audit_started",
            self.audit_table.c.started_at,
        )
        self.record_audit_table = Table(
            "tide_record_audit",
            self.metadata,
            Column(
                "sequence",
                BigInteger().with_variant(Integer(), "sqlite"),
                Identity(),
                primary_key=True,
            ),
            Column("event_id", String(64), nullable=False, unique=True),
            Column("entity", Unicode(255), nullable=False),
            Column("operation", String(16), nullable=False),
            Column("identity_json", Unicode(2048), nullable=False),
            Column("identity_hash", String(64), nullable=False),
            Column("principal", Unicode(512), nullable=False),
            Column("channel", String(32), nullable=False),
            Column("correlation_id", Unicode(255), nullable=False),
            Column("occurred_at", DateTime(timezone=True), nullable=False),
            Column("source", String(16), nullable=False),
            Column("changes_json", UnicodeText(), nullable=False),
            schema=schema,
        )
        Index(
            "ix_tide_record_audit_correlation",
            self.record_audit_table.c.correlation_id,
        )
        Index(
            "ix_tide_record_audit_occurred",
            self.record_audit_table.c.occurred_at,
        )
        Index(
            "ix_tide_record_audit_record",
            self.record_audit_table.c.entity,
            self.record_audit_table.c.identity_hash,
        )

    def create_schema(self) -> None:
        if self.mode != "managed":
            raise SchemaManagementError(
                "legacy action-store mode forbids creating or changing schema objects"
            )
        self.metadata.create_all(self.engine)

    def schema_issues(self) -> tuple[SchemaIssue, ...]:
        inspector = inspect(self.engine)
        issues: list[SchemaIssue] = []
        for table in (
            self.idempotency_table,
            self.audit_table,
            self.record_audit_table,
        ):
            object_name = f"{table.schema}.{table.name}" if table.schema else table.name
            entity = f"tide.action-store.{table.name}"
            if not inspector.has_table(table.name, schema=table.schema):
                issues.append(
                    SchemaIssue(entity, object_name, "mapped table does not exist")
                )
                continue
            actual_columns = {
                str(column["name"])
                for column in inspector.get_columns(table.name, schema=table.schema)
            }
            for column in table.columns:
                if column.name not in actual_columns:
                    issues.append(
                        SchemaIssue(
                            entity,
                            f"{object_name}.{column.name}",
                            "mapped column does not exist",
                        )
                    )
        return tuple(issues)

    def validate_schema(self) -> None:
        issues = self.schema_issues()
        if issues:
            raise SchemaCompatibilityError(issues)

    def get_idempotency(self, key: str) -> IdempotencyRecord | None:
        try:
            with self.engine.connect() as connection:
                row = (
                    connection.execute(
                        select(self.idempotency_table).where(
                            self.idempotency_table.c.key == key
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as error:
            raise ActionStoreError("could not read idempotency state") from error
        if row is None:
            return None
        try:
            return _idempotency_record(row)
        except (TypeError, ValueError) as error:
            raise ActionStoreError("stored idempotency state is invalid") from error

    def claim_idempotency(self, record: IdempotencyRecord) -> IdempotencyClaim:
        if record.status is not IdempotencyStatus.IN_PROGRESS:
            raise ActionStoreError("new idempotency records must be in progress")
        values = {
            "key": record.key,
            "fingerprint": record.fingerprint,
            "entity": record.entity,
            "identity_json": serialize_action_value(record.identity),
            "principal": record.principal,
            "correlation_id": record.correlation_id,
            "status": record.status.value,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "error_code": record.error_code,
        }
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.idempotency_table).values(**values))
        except IntegrityError:
            previous = self.get_idempotency(record.key)
            if previous is None:
                raise ActionStoreError(
                    "idempotency key collision could not be resolved"
                )
            return IdempotencyClaim(previous, acquired=False)
        except SQLAlchemyError as error:
            raise ActionStoreError("could not reserve idempotency key") from error
        return IdempotencyClaim(record, acquired=True)

    def complete_idempotency(
        self,
        key: str,
        fingerprint: str,
        *,
        finished_at: datetime,
    ) -> None:
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(self.idempotency_table)
                    .where(
                        self.idempotency_table.c.key == key,
                        self.idempotency_table.c.fingerprint == fingerprint,
                        self.idempotency_table.c.status
                        == IdempotencyStatus.IN_PROGRESS.value,
                    )
                    .values(
                        status=IdempotencyStatus.COMPLETED.value,
                        finished_at=finished_at,
                        error_code=None,
                    )
                )
        except SQLAlchemyError as error:
            raise ActionStoreError("could not complete idempotency state") from error
        if result.rowcount == 1:
            return
        previous = self.get_idempotency(key)
        if (
            previous is not None
            and previous.fingerprint == fingerprint
            and previous.status is IdempotencyStatus.COMPLETED
        ):
            return
        raise ActionStoreError("idempotency execution is not in progress")

    def fail_idempotency(
        self,
        key: str,
        fingerprint: str,
        *,
        finished_at: datetime,
        error_code: str,
    ) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    update(self.idempotency_table)
                    .where(
                        self.idempotency_table.c.key == key,
                        self.idempotency_table.c.fingerprint == fingerprint,
                        self.idempotency_table.c.status
                        == IdempotencyStatus.IN_PROGRESS.value,
                    )
                    .values(
                        status=IdempotencyStatus.FAILED.value,
                        finished_at=finished_at,
                        error_code=error_code,
                    )
                )
        except SQLAlchemyError as error:
            raise ActionStoreError("could not fail idempotency state") from error

    def begin_audit(self, event: ActionAuditEvent) -> None:
        if event.outcome is not AuditOutcome.STARTED or event.finished_at is not None:
            raise ActionStoreError("new audit events must be started and unfinished")
        values = {
            "event_id": event.event_id,
            "entity": event.entity,
            "action": event.action,
            "identity_json": serialize_action_value(event.identity),
            "principal": event.principal,
            "channel": event.channel,
            "correlation_id": event.correlation_id,
            "started_at": event.started_at,
            "outcome": event.outcome.value,
            "finished_at": event.finished_at,
            "error_code": event.error_code,
            "idempotency_key_hash": event.idempotency_key_hash,
        }
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.audit_table).values(**values))
        except SQLAlchemyError as error:
            raise ActionStoreError("could not begin action audit") from error

    def finish_audit(
        self,
        event_id: str,
        *,
        outcome: AuditOutcome,
        finished_at: datetime,
        error_code: str | None = None,
    ) -> None:
        if outcome is AuditOutcome.STARTED:
            raise ActionStoreError("finished audit events require a terminal outcome")
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(self.audit_table)
                    .where(
                        self.audit_table.c.event_id == event_id,
                        self.audit_table.c.outcome == AuditOutcome.STARTED.value,
                    )
                    .values(
                        outcome=outcome.value,
                        finished_at=finished_at,
                        error_code=error_code,
                    )
                )
        except SQLAlchemyError as error:
            raise ActionStoreError("could not finish action audit") from error
        if result.rowcount == 1:
            return
        event = self._get_audit(event_id)
        if event is not None and event.outcome is outcome:
            return
        raise ActionStoreError("audit event was not found or is already finished")

    def audit_events(
        self,
        *,
        correlation_id: str | None = None,
        entity: str | None = None,
        identity: Any | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> tuple[ActionAuditEvent, ...]:
        if limit is not None and (limit < 1 or limit > 500):
            raise ValueError("audit limit must be between 1 and 500")
        statement = select(self.audit_table)
        if correlation_id is not None:
            statement = statement.where(
                self.audit_table.c.correlation_id == correlation_id
            )
        if entity is not None:
            statement = statement.where(self.audit_table.c.entity == entity)
        if identity is not None:
            statement = statement.where(
                self.audit_table.c.identity_json == serialize_action_value(identity)
            )
        order = (
            self.audit_table.c.sequence.desc()
            if newest_first
            else self.audit_table.c.sequence
        )
        statement = statement.order_by(order)
        if limit is not None:
            statement = statement.limit(limit)
        try:
            with self.engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as error:
            raise ActionStoreError("could not read action audit") from error
        try:
            return tuple(_audit_event(row) for row in rows)
        except (TypeError, ValueError) as error:
            raise ActionStoreError("stored action audit is invalid") from error

    def record_audit(self, event: RecordAuditEvent) -> None:
        if not event.changes:
            raise ActionStoreError("record audit events require at least one change")
        identity_json = serialize_action_value(event.identity)
        values = {
            "event_id": event.event_id,
            "entity": event.entity,
            "operation": event.operation.value,
            "identity_json": identity_json,
            "identity_hash": _identity_hash(identity_json),
            "principal": event.principal,
            "channel": event.channel,
            "correlation_id": event.correlation_id,
            "occurred_at": event.occurred_at,
            "source": event.source,
            "changes_json": _serialize_record_changes(event.changes),
        }
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.record_audit_table).values(**values))
        except SQLAlchemyError as error:
            raise ActionStoreError("could not write record audit") from error

    def record_audit_events(
        self,
        *,
        correlation_id: str | None = None,
        entity: str | None = None,
        identity: Any | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> tuple[RecordAuditEvent, ...]:
        if limit is not None and (limit < 1 or limit > 500):
            raise ValueError("audit limit must be between 1 and 500")
        statement = select(self.record_audit_table)
        if correlation_id is not None:
            statement = statement.where(
                self.record_audit_table.c.correlation_id == correlation_id
            )
        if entity is not None:
            statement = statement.where(self.record_audit_table.c.entity == entity)
        if identity is not None:
            identity_json = serialize_action_value(identity)
            statement = statement.where(
                self.record_audit_table.c.identity_hash
                == _identity_hash(identity_json),
                self.record_audit_table.c.identity_json == identity_json,
            )
        order = (
            self.record_audit_table.c.sequence.desc()
            if newest_first
            else self.record_audit_table.c.sequence
        )
        statement = statement.order_by(order)
        if limit is not None:
            statement = statement.limit(limit)
        try:
            with self.engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as error:
            raise ActionStoreError("could not read record audit") from error
        try:
            return tuple(_record_audit_event(row) for row in rows)
        except (TypeError, ValueError) as error:
            raise ActionStoreError("stored record audit is invalid") from error

    def dispose(self) -> None:
        if self._owns_engine:
            self.engine.dispose()

    def _get_audit(self, event_id: str) -> ActionAuditEvent | None:
        try:
            with self.engine.connect() as connection:
                row = (
                    connection.execute(
                        select(self.audit_table).where(
                            self.audit_table.c.event_id == event_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as error:
            raise ActionStoreError("could not read action audit") from error
        if row is None:
            return None
        try:
            return _audit_event(row)
        except (TypeError, ValueError) as error:
            raise ActionStoreError("stored action audit is invalid") from error


def _idempotency_record(row: Any) -> IdempotencyRecord:
    return IdempotencyRecord(
        key=str(row["key"]),
        fingerprint=str(row["fingerprint"]),
        entity=str(row["entity"]),
        identity=deserialize_action_value(str(row["identity_json"])),
        principal=str(row["principal"]),
        correlation_id=str(row["correlation_id"]),
        status=IdempotencyStatus(str(row["status"])),
        started_at=_database_datetime(row["started_at"]),
        finished_at=(
            _database_datetime(row["finished_at"])
            if row["finished_at"] is not None
            else None
        ),
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
    )


def _audit_event(row: Any) -> ActionAuditEvent:
    return ActionAuditEvent(
        event_id=str(row["event_id"]),
        entity=str(row["entity"]),
        action=str(row["action"]),
        identity=deserialize_action_value(str(row["identity_json"])),
        principal=str(row["principal"]),
        channel=str(row["channel"]),
        correlation_id=str(row["correlation_id"]),
        started_at=_database_datetime(row["started_at"]),
        outcome=AuditOutcome(str(row["outcome"])),
        finished_at=(
            _database_datetime(row["finished_at"])
            if row["finished_at"] is not None
            else None
        ),
        error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        idempotency_key_hash=(
            str(row["idempotency_key_hash"])
            if row["idempotency_key_hash"] is not None
            else None
        ),
    )


def _record_audit_event(row: Any) -> RecordAuditEvent:
    return RecordAuditEvent(
        event_id=str(row["event_id"]),
        entity=str(row["entity"]),
        operation=RecordAuditOperation(str(row["operation"])),
        identity=deserialize_action_value(str(row["identity_json"])),
        principal=str(row["principal"]),
        channel=str(row["channel"]),
        correlation_id=str(row["correlation_id"]),
        occurred_at=_database_datetime(row["occurred_at"]),
        source=str(row["source"]),
        changes=_deserialize_record_changes(str(row["changes_json"])),
    )


def _identity_hash(serialized: str) -> str:
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _serialize_record_changes(changes: tuple[AuditFieldChange, ...]) -> str:
    return serialize_action_value(
        [
            {
                "field": change.field,
                "before_present": change.before_present,
                "after_present": change.after_present,
                "value_mode": change.value_mode.value,
                "before": change.before,
                "after": change.after,
            }
            for change in changes
        ]
    )


def _deserialize_record_changes(value: str) -> tuple[AuditFieldChange, ...]:
    payload = deserialize_action_value(value)
    if not isinstance(payload, list):
        raise ValueError("record audit changes must be a sequence")
    changes: list[AuditFieldChange] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("record audit change must be a mapping")
        field = item.get("field")
        before_present = item.get("before_present")
        after_present = item.get("after_present")
        if (
            not isinstance(field, str)
            or not field
            or not isinstance(before_present, bool)
            or not isinstance(after_present, bool)
        ):
            raise ValueError("record audit change has an invalid shape")
        changes.append(
            AuditFieldChange(
                field=field,
                before_present=before_present,
                after_present=after_present,
                value_mode=AuditValueMode(str(item.get("value_mode"))),
                before=item.get("before"),
                after=item.get("after"),
            )
        )
    if not changes or len({change.field for change in changes}) != len(changes):
        raise ValueError("record audit changes must be non-empty and unique")
    return tuple(changes)


def _database_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
