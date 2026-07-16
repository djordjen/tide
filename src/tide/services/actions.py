"""First-class domain action execution through secured record services."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any, Callable, Mapping
from uuid import uuid4

from tide.compiler.expressions import evaluate_expression
from tide.compiler.normalized import ApplicationModel
from tide.runtime.context import RequestContext
from tide.runtime.errors import (
    ActionDisabled,
    ActionStoreError,
    ConcurrencyError,
    IdempotencyConflict,
    NotFoundError,
    TideRuntimeError,
)
from tide.security.engine import SecurityEngine
from tide.services.action_store import (
    ActionAuditEvent,
    ActionExecutionStore,
    AuditOutcome,
    IdempotencyRecord,
    IdempotencyStatus,
    InMemoryActionExecutionStore,
    serialize_action_value,
)
from tide.services.records import MutationSource, RecordsService

ActionHandler = Callable[[dict[str, Any], RequestContext, Mapping[str, Any]], Any]


class ActionService:
    def __init__(
        self,
        model: ApplicationModel,
        records: RecordsService,
        security: SecurityEngine | None = None,
        *,
        execution_store: ActionExecutionStore | None = None,
        clock: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.model = model
        self.records = records
        self.security = security or records.security
        self.execution_store = execution_store or InMemoryActionExecutionStore()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._event_id_factory = event_id_factory or (lambda: str(uuid4()))
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, reference: str, handler: ActionHandler) -> None:
        self._handlers[reference] = handler

    def execute(
        self,
        entity_name: str,
        action_name: str,
        identity: Any,
        payload: Mapping[str, Any],
        context: RequestContext,
        *,
        idempotency_key: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        entity = self.model.entity(entity_name)
        action = entity.actions.get(action_name)
        if action is None:
            raise NotFoundError(f"action {entity_name}.{action_name} was not found")
        self.security.authorize_action(entity, action, context)

        key = _validate_idempotency_key(idempotency_key)
        audit = (
            self._begin_audit(
                entity_name,
                action_name,
                identity,
                context,
                key,
            )
            if action.get("audit", True)
            else None
        )
        claimed: IdempotencyRecord | None = None
        outcome = AuditOutcome.SUCCEEDED
        try:
            fingerprint = (
                _fingerprint(entity_name, action_name, identity, payload, context)
                if key is not None
                else None
            )
            if key is not None and not action.get("idempotent"):
                raise IdempotencyConflict(
                    "action does not declare idempotent execution"
                )

            previous = (
                self.execution_store.get_idempotency(key) if key is not None else None
            )
            if previous is not None:
                result = self._replay(previous, str(fingerprint), context)
                outcome = AuditOutcome.REPLAYED
            else:
                session = self.records.begin_action(entity_name, identity, context)
                if (
                    expected_version is not None
                    and session.expected_version != expected_version
                ):
                    raise ConcurrencyError(
                        expected_version,
                        session.expected_version,
                    )
                if expected_version is not None:
                    session.expected_version = expected_version
                condition = action.get("enabled_when")
                if condition and not bool(
                    evaluate_expression(condition, session.values)
                ):
                    raise ActionDisabled(
                        f"action {entity_name}.{action_name} is disabled"
                    )
                reference = action["execute"]
                handler = self._handlers.get(reference)
                if handler is None:
                    raise RuntimeError(f"no action handler registered for {reference}")

                replayed = False
                if key is not None:
                    claim = self.execution_store.claim_idempotency(
                        IdempotencyRecord(
                            key=key,
                            fingerprint=str(fingerprint),
                            entity=entity_name,
                            identity=identity,
                            principal=context.principal.identifier,
                            correlation_id=context.correlation_id,
                            status=IdempotencyStatus.IN_PROGRESS,
                            started_at=self._now(),
                        )
                    )
                    if claim.acquired:
                        claimed = claim.record
                    else:
                        result = self._replay(
                            claim.record,
                            str(fingerprint),
                            context,
                        )
                        outcome = AuditOutcome.REPLAYED
                        replayed = True

                if not replayed:
                    handler(session.values, context, payload)
                    result = self.records.commit(
                        session,
                        context,
                        source=MutationSource.ACTION,
                    )
                    if claimed is not None:
                        self.execution_store.complete_idempotency(
                            claimed.key,
                            claimed.fingerprint,
                            finished_at=self._now(),
                        )
        except Exception as error:
            self._record_failure(claimed, audit, error)
            raise

        self._finish_audit(audit, outcome=outcome)
        return result

    def _replay(
        self,
        previous: IdempotencyRecord,
        fingerprint: str,
        context: RequestContext,
    ) -> dict[str, Any]:
        if previous.fingerprint != fingerprint:
            raise IdempotencyConflict(
                "idempotency key was reused for a different request"
            )
        if previous.status is IdempotencyStatus.IN_PROGRESS:
            raise IdempotencyConflict("idempotent execution is still in progress")
        if previous.status is IdempotencyStatus.FAILED:
            raise IdempotencyConflict(
                "previous idempotent execution failed and requires reconciliation"
            )
        return self.records.get(previous.entity, previous.identity, context)

    def _begin_audit(
        self,
        entity: str,
        action: str,
        identity: Any,
        context: RequestContext,
        idempotency_key: str | None,
    ) -> ActionAuditEvent:
        event = ActionAuditEvent(
            event_id=self._event_id_factory(),
            entity=entity,
            action=action,
            identity=identity,
            principal=context.principal.identifier,
            channel=str(context.channel),
            correlation_id=context.correlation_id,
            started_at=self._now(),
            idempotency_key_hash=(
                hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
                if idempotency_key is not None
                else None
            ),
        )
        self.execution_store.begin_audit(event)
        return event

    def _finish_audit(
        self,
        event: ActionAuditEvent | None,
        *,
        outcome: AuditOutcome,
        error_code: str | None = None,
    ) -> None:
        if event is None:
            return
        self.execution_store.finish_audit(
            event.event_id,
            outcome=outcome,
            finished_at=self._now(),
            error_code=error_code,
        )

    def _record_failure(
        self,
        claimed: IdempotencyRecord | None,
        audit: ActionAuditEvent | None,
        error: Exception,
    ) -> None:
        error_code = (
            error.code if isinstance(error, TideRuntimeError) else "internal_error"
        )
        store_error: Exception | None = None
        if claimed is not None:
            try:
                self.execution_store.fail_idempotency(
                    claimed.key,
                    claimed.fingerprint,
                    finished_at=self._now(),
                    error_code=error_code,
                )
            except Exception as persistence_error:  # preserve fail-closed state errors
                store_error = persistence_error
        try:
            self._finish_audit(
                audit,
                outcome=(
                    AuditOutcome.CONFLICT
                    if isinstance(error, IdempotencyConflict)
                    else AuditOutcome.FAILED
                ),
                error_code=error_code,
            )
        except Exception as persistence_error:
            store_error = store_error or persistence_error
        if store_error is not None:
            raise store_error from error

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ActionStoreError("action store timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


def _fingerprint(
    entity: str,
    action: str,
    identity: Any,
    payload: Mapping[str, Any],
    context: RequestContext,
) -> str:
    serialized = serialize_action_value(
        {
            "principal": context.principal.identifier,
            "entity": entity,
            "action": action,
            "identity": identity,
            "payload": payload,
        }
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 255:
        raise IdempotencyConflict(
            "idempotency keys must be non-empty strings of at most 255 characters"
        )
    return value
