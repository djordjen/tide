"""Secured, renderer-neutral access to safe record history."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from tide.compiler.normalized import ApplicationModel
from tide.runtime import RequestContext
from tide.security import SecurityEngine
from tide.services.action_store import (
    ActionAuditEvent,
    ActionExecutionStore,
    AuditEvent,
    AuditValueMode,
    RecordAuditEvent,
)


@runtime_checkable
class AuditHistoryReader(Protocol):
    """Small read-only contract shared by local and remote renderers."""

    def can_view(self, entity_name: str, context: RequestContext) -> bool: ...

    def for_record(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
        *,
        limit: int = 100,
    ) -> tuple[AuditEvent, ...]: ...


class AuditHistoryService:
    """Authorize and return bounded newest-first action and CRUD history."""

    def __init__(
        self,
        model: ApplicationModel,
        store: ActionExecutionStore,
        security: SecurityEngine | None = None,
    ) -> None:
        self.model = model
        self.store = store
        self.security = security or SecurityEngine(model)

    def can_view(self, entity_name: str, context: RequestContext) -> bool:
        entity = self.model.entity(entity_name)
        return self.security.can_access_entity(entity, "audit", context)

    def for_record(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
        *,
        limit: int = 100,
    ) -> tuple[AuditEvent, ...]:
        if limit < 1 or limit > 500:
            raise ValueError("audit limit must be between 1 and 500")
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "audit", context)
        actions = self.store.audit_events(
            entity=entity_name,
            identity=identity,
            limit=limit,
            newest_first=True,
        )
        records = self.store.record_audit_events(
            entity=entity_name,
            identity=identity,
            limit=limit,
            newest_first=True,
        )
        safe_records = tuple(
            self._safe_record_event(event, context) for event in records
        )
        combined: list[AuditEvent] = [*actions, *safe_records]
        combined.sort(
            key=lambda event: (
                _event_timestamp(event),
                _event_phase(event),
                event.event_id,
            ),
            reverse=True,
        )
        return tuple(combined[:limit])

    def _safe_record_event(
        self,
        event: RecordAuditEvent,
        context: RequestContext,
    ) -> RecordAuditEvent:
        entity = self.model.entity(event.entity)
        changes = []
        for change in event.changes:
            if (
                change.value_mode is AuditValueMode.RECORDED
                and (
                    change.field not in entity.fields
                    or not self.security.can_read_field(
                        event.entity,
                        change.field,
                        context,
                    )
                )
            ):
                changes.append(
                    replace(
                        change,
                        value_mode=AuditValueMode.REDACTED,
                        before=None,
                        after=None,
                    )
                )
            else:
                changes.append(change)
        return replace(event, changes=tuple(changes))


def _event_timestamp(event: AuditEvent) -> datetime:
    if isinstance(event, ActionAuditEvent):
        return event.finished_at or event.started_at
    return event.occurred_at


def _event_phase(event: AuditEvent) -> int:
    """Keep completed actions after their correlated record write on equal ticks."""

    if isinstance(event, ActionAuditEvent):
        return 2 if event.finished_at is not None else 0
    return 1
