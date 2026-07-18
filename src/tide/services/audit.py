"""Secured, renderer-neutral access to safe action audit history."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from tide.compiler.normalized import ApplicationModel
from tide.runtime import RequestContext
from tide.security import SecurityEngine
from tide.services.action_store import ActionAuditEvent, ActionExecutionStore


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
    ) -> tuple[ActionAuditEvent, ...]: ...


class AuditHistoryService:
    """Authorize and return bounded newest-first action history."""

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
    ) -> tuple[ActionAuditEvent, ...]:
        if limit < 1 or limit > 500:
            raise ValueError("audit limit must be between 1 and 500")
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "audit", context)
        return self.store.audit_events(
            entity=entity_name,
            identity=identity,
            limit=limit,
            newest_first=True,
        )
