"""SQLAlchemy-backed action idempotency and audit storage."""

from __future__ import annotations

from datetime import datetime, timezone
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
    AuditOutcome,
    IdempotencyClaim,
    IdempotencyRecord,
    IdempotencyStatus,
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

    def create_schema(self) -> None:
        if self.mode != "managed":
            raise SchemaManagementError(
                "legacy action-store mode forbids creating or changing schema objects"
            )
        self.metadata.create_all(self.engine)

    def schema_issues(self) -> tuple[SchemaIssue, ...]:
        inspector = inspect(self.engine)
        issues: list[SchemaIssue] = []
        for table in (self.idempotency_table, self.audit_table):
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
    ) -> tuple[ActionAuditEvent, ...]:
        statement = select(self.audit_table)
        if correlation_id is not None:
            statement = statement.where(
                self.audit_table.c.correlation_id == correlation_id
            )
        statement = statement.order_by(self.audit_table.c.sequence)
        try:
            with self.engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as error:
            raise ActionStoreError("could not read action audit") from error
        try:
            return tuple(_audit_event(row) for row in rows)
        except (TypeError, ValueError) as error:
            raise ActionStoreError("stored action audit is invalid") from error

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


def _database_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
