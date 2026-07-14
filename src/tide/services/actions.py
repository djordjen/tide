"""First-class domain action execution through secured record services."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from tide.compiler.expressions import evaluate_expression
from tide.compiler.normalized import ApplicationModel
from tide.runtime.context import RequestContext
from tide.runtime.errors import ActionDisabled, IdempotencyConflict, NotFoundError
from tide.security.engine import SecurityEngine
from tide.services.records import MutationSource, RecordsService

ActionHandler = Callable[[dict[str, Any], RequestContext, Mapping[str, Any]], Any]


class ActionService:
    def __init__(
        self,
        model: ApplicationModel,
        records: RecordsService,
        security: SecurityEngine | None = None,
    ) -> None:
        self.model = model
        self.records = records
        self.security = security or records.security
        self._handlers: dict[str, ActionHandler] = {}
        self._idempotency: dict[str, tuple[str, str, Any]] = {}

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
    ) -> dict[str, Any]:
        entity = self.model.entity(entity_name)
        action = entity.actions.get(action_name)
        if action is None:
            raise NotFoundError(f"action {entity_name}.{action_name} was not found")
        self.security.authorize_action(entity, action, context)

        fingerprint = _fingerprint(entity_name, action_name, identity, payload, context)
        if idempotency_key:
            previous = self._idempotency.get(idempotency_key)
            if previous:
                if previous[0] != fingerprint:
                    raise IdempotencyConflict("idempotency key was reused for a different request")
                return self.records.get(previous[1], previous[2], context)
            if not action.get("idempotent"):
                raise IdempotencyConflict("action does not declare idempotent execution")

        session = self.records.begin_action(entity_name, identity, context)
        condition = action.get("enabled_when")
        if condition and not bool(evaluate_expression(condition, session.values)):
            raise ActionDisabled(f"action {entity_name}.{action_name} is disabled")
        reference = action["execute"]
        handler = self._handlers.get(reference)
        if handler is None:
            raise RuntimeError(f"no action handler registered for {reference}")
        handler(session.values, context, payload)
        result = self.records.commit(session, context, source=MutationSource.ACTION)
        if idempotency_key:
            self._idempotency[idempotency_key] = fingerprint, entity_name, identity
        return result


def _fingerprint(
    entity: str,
    action: str,
    identity: Any,
    payload: Mapping[str, Any],
    context: RequestContext,
) -> str:
    serialized = json.dumps(
        {
            "principal": context.principal.identifier,
            "entity": entity,
            "action": action,
            "identity": identity,
            "payload": payload,
        },
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
