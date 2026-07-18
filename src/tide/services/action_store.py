"""Durable-boundary contracts for action idempotency and audit state."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
import json
import math
from threading import RLock
from typing import Any, Mapping, Protocol, runtime_checkable

from tide.runtime.errors import ActionStoreError


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class AuditOutcome(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    REPLAYED = "replayed"
    CONFLICT = "conflict"
    FAILED = "failed"


class RecordAuditOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class AuditValueMode(StrEnum):
    """Describe whether safe before/after values accompany a changed field."""

    RECORDED = "recorded"
    FIELD_ONLY = "field_only"
    REDACTED = "redacted"


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    key: str
    fingerprint: str
    entity: str
    identity: Any
    principal: str
    correlation_id: str
    status: IdempotencyStatus
    started_at: datetime
    finished_at: datetime | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    record: IdempotencyRecord
    acquired: bool


@dataclass(frozen=True, slots=True)
class ActionAuditEvent:
    event_id: str
    entity: str
    action: str
    identity: Any
    principal: str
    channel: str
    correlation_id: str
    started_at: datetime
    outcome: AuditOutcome = AuditOutcome.STARTED
    finished_at: datetime | None = None
    error_code: str | None = None
    idempotency_key_hash: str | None = None


@dataclass(frozen=True, slots=True)
class AuditFieldChange:
    field: str
    before_present: bool
    after_present: bool
    value_mode: AuditValueMode = AuditValueMode.FIELD_ONLY
    before: Any = None
    after: Any = None

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("audit change field must not be empty")
        if self.value_mode is not AuditValueMode.RECORDED and (
            self.before is not None or self.after is not None
        ):
            raise ValueError("non-recorded audit values must be omitted")
        if (not self.before_present and self.before is not None) or (
            not self.after_present and self.after is not None
        ):
            raise ValueError("absent audit sides cannot contain values")


@dataclass(frozen=True, slots=True)
class RecordAuditEvent:
    event_id: str
    entity: str
    operation: RecordAuditOperation
    identity: Any
    principal: str
    channel: str
    correlation_id: str
    occurred_at: datetime
    source: str
    changes: tuple[AuditFieldChange, ...] = ()

    def __post_init__(self) -> None:
        if not self.event_id or not self.entity:
            raise ValueError("record audit identifiers must not be empty")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("record audit timestamps must be timezone-aware")
        if self.source not in {"user", "action", "system"}:
            raise ValueError("record audit source is invalid")
        if not self.changes or len({change.field for change in self.changes}) != len(
            self.changes
        ):
            raise ValueError("record audit changes must be non-empty and unique")


AuditEvent = ActionAuditEvent | RecordAuditEvent


@runtime_checkable
class ActionExecutionStore(Protocol):
    def get_idempotency(self, key: str) -> IdempotencyRecord | None: ...

    def claim_idempotency(self, record: IdempotencyRecord) -> IdempotencyClaim: ...

    def complete_idempotency(
        self,
        key: str,
        fingerprint: str,
        *,
        finished_at: datetime,
    ) -> None: ...

    def fail_idempotency(
        self,
        key: str,
        fingerprint: str,
        *,
        finished_at: datetime,
        error_code: str,
    ) -> None: ...

    def begin_audit(self, event: ActionAuditEvent) -> None: ...

    def finish_audit(
        self,
        event_id: str,
        *,
        outcome: AuditOutcome,
        finished_at: datetime,
        error_code: str | None = None,
    ) -> None: ...

    def audit_events(
        self,
        *,
        correlation_id: str | None = None,
        entity: str | None = None,
        identity: Any | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> tuple[ActionAuditEvent, ...]: ...

    def record_audit(self, event: RecordAuditEvent) -> None: ...

    def record_audit_events(
        self,
        *,
        correlation_id: str | None = None,
        entity: str | None = None,
        identity: Any | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> tuple[RecordAuditEvent, ...]: ...


class InMemoryActionExecutionStore:
    """Thread-safe process-local implementation of the durable store contract."""

    def __init__(self) -> None:
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._audit: dict[str, ActionAuditEvent] = {}
        self._record_audit: dict[str, RecordAuditEvent] = {}
        self._lock = RLock()

    def get_idempotency(self, key: str) -> IdempotencyRecord | None:
        with self._lock:
            record = self._idempotency.get(key)
            return _copy_idempotency(record) if record is not None else None

    def claim_idempotency(self, record: IdempotencyRecord) -> IdempotencyClaim:
        if record.status is not IdempotencyStatus.IN_PROGRESS:
            raise ActionStoreError("new idempotency records must be in progress")
        with self._lock:
            previous = self._idempotency.get(record.key)
            if previous is not None:
                return IdempotencyClaim(_copy_idempotency(previous), acquired=False)
            stored = _copy_idempotency(record)
            self._idempotency[record.key] = stored
            return IdempotencyClaim(_copy_idempotency(stored), acquired=True)

    def complete_idempotency(
        self,
        key: str,
        fingerprint: str,
        *,
        finished_at: datetime,
    ) -> None:
        with self._lock:
            record = self._matching_idempotency(key, fingerprint)
            if record.status is IdempotencyStatus.COMPLETED:
                return
            if record.status is not IdempotencyStatus.IN_PROGRESS:
                raise ActionStoreError("idempotency execution is not in progress")
            self._idempotency[key] = replace(
                record,
                status=IdempotencyStatus.COMPLETED,
                finished_at=finished_at,
                error_code=None,
            )

    def fail_idempotency(
        self,
        key: str,
        fingerprint: str,
        *,
        finished_at: datetime,
        error_code: str,
    ) -> None:
        with self._lock:
            record = self._matching_idempotency(key, fingerprint)
            if record.status is not IdempotencyStatus.IN_PROGRESS:
                return
            self._idempotency[key] = replace(
                record,
                status=IdempotencyStatus.FAILED,
                finished_at=finished_at,
                error_code=error_code,
            )

    def begin_audit(self, event: ActionAuditEvent) -> None:
        if event.outcome is not AuditOutcome.STARTED or event.finished_at is not None:
            raise ActionStoreError("new audit events must be started and unfinished")
        with self._lock:
            if event.event_id in self._audit:
                raise ActionStoreError("audit event identifier already exists")
            self._audit[event.event_id] = _copy_audit(event)

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
        with self._lock:
            event = self._audit.get(event_id)
            if event is None:
                raise ActionStoreError("audit event was not found")
            if event.outcome is not AuditOutcome.STARTED:
                if event.outcome is outcome:
                    return
                raise ActionStoreError("audit event is already finished")
            self._audit[event_id] = replace(
                event,
                outcome=outcome,
                finished_at=finished_at,
                error_code=error_code,
            )

    def audit_events(
        self,
        *,
        correlation_id: str | None = None,
        entity: str | None = None,
        identity: Any | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> tuple[ActionAuditEvent, ...]:
        _validate_audit_limit(limit)
        with self._lock:
            matching = [
                event
                for event in self._audit.values()
                if correlation_id is None or event.correlation_id == correlation_id
                if entity is None or event.entity == entity
                if identity is None or event.identity == identity
            ]
            if newest_first:
                matching.reverse()
            events = matching if limit is None else matching[:limit]
            return tuple(_copy_audit(event) for event in events)

    def record_audit(self, event: RecordAuditEvent) -> None:
        if not event.changes:
            raise ActionStoreError("record audit events require at least one change")
        with self._lock:
            if event.event_id in self._record_audit:
                raise ActionStoreError("record audit event identifier already exists")
            self._record_audit[event.event_id] = _copy_record_audit(event)

    def record_audit_events(
        self,
        *,
        correlation_id: str | None = None,
        entity: str | None = None,
        identity: Any | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> tuple[RecordAuditEvent, ...]:
        _validate_audit_limit(limit)
        with self._lock:
            matching = [
                event
                for event in self._record_audit.values()
                if correlation_id is None or event.correlation_id == correlation_id
                if entity is None or event.entity == entity
                if identity is None or event.identity == identity
            ]
            if newest_first:
                matching.reverse()
            events = matching if limit is None else matching[:limit]
            return tuple(_copy_record_audit(event) for event in events)

    def _matching_idempotency(
        self,
        key: str,
        fingerprint: str,
    ) -> IdempotencyRecord:
        record = self._idempotency.get(key)
        if record is None:
            raise ActionStoreError("idempotency record was not found")
        if record.fingerprint != fingerprint:
            raise ActionStoreError("idempotency fingerprint does not match")
        return record


def serialize_action_value(value: Any) -> str:
    """Serialize supported action values with explicit type tags."""

    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def deserialize_action_value(value: str) -> Any:
    """Reverse :func:`serialize_action_value` without executable object hooks."""

    return _restore_value(json.loads(value))


def _canonical_value(value: Any) -> list[Any]:
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("action values cannot contain non-finite floats")
        return ["float", repr(value)]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("action mappings require string keys")
        return [
            "mapping",
            [[key, _canonical_value(value[key])] for key in sorted(value)],
        ]
    if isinstance(value, (list, tuple)):
        return ["sequence", [_canonical_value(item) for item in value]]
    raise TypeError(f"unsupported action value type: {type(value).__name__}")


def _restore_value(value: Any) -> Any:
    if not isinstance(value, list) or not value:
        raise ValueError("invalid serialized action value")
    tag = value[0]
    if tag == "null" and len(value) == 1:
        return None
    if len(value) != 2:
        raise ValueError("invalid serialized action value")
    payload = value[1]
    if tag == "boolean" and isinstance(payload, bool):
        return payload
    if tag == "integer" and isinstance(payload, str):
        return int(payload)
    if tag == "string" and isinstance(payload, str):
        return payload
    if tag == "decimal" and isinstance(payload, str):
        return Decimal(payload)
    if tag == "float" and isinstance(payload, str):
        result = float(payload)
        if math.isfinite(result):
            return result
    if tag == "datetime" and isinstance(payload, str):
        return datetime.fromisoformat(payload)
    if tag == "date" and isinstance(payload, str):
        return date.fromisoformat(payload)
    if tag == "mapping" and isinstance(payload, list):
        if not all(
            isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)
            for item in payload
        ):
            raise ValueError("invalid serialized action mapping")
        keys = [item[0] for item in payload]
        if len(set(keys)) != len(keys):
            raise ValueError("serialized action mapping contains duplicate keys")
        return {item[0]: _restore_value(item[1]) for item in payload}
    if tag == "sequence" and isinstance(payload, list):
        return [_restore_value(item) for item in payload]
    raise ValueError("invalid serialized action value")


def _copy_idempotency(record: IdempotencyRecord) -> IdempotencyRecord:
    return replace(record, identity=deepcopy(record.identity))


def _copy_audit(event: ActionAuditEvent) -> ActionAuditEvent:
    return replace(event, identity=deepcopy(event.identity))


def _copy_record_audit(event: RecordAuditEvent) -> RecordAuditEvent:
    return replace(
        event,
        identity=deepcopy(event.identity),
        changes=tuple(
            replace(
                change,
                before=deepcopy(change.before),
                after=deepcopy(change.after),
            )
            for change in event.changes
        ),
    )


def _validate_audit_limit(limit: int | None) -> None:
    if limit is not None and (limit < 1 or limit > 500):
        raise ValueError("audit limit must be between 1 and 500")
