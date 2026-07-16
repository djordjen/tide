"""Permission and policy enforcement over the normalized application model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from tide.compiler.expressions import evaluate_expression
from tide.compiler.normalized import ApplicationModel, NormalizedEntity
from tide.runtime.context import Principal, RequestContext
from tide.runtime.errors import AuthorizationError


@dataclass(frozen=True, slots=True)
class ProtectedValue:
    def __repr__(self) -> str:
        return "ProtectedValue"


PROTECTED = ProtectedValue()


class SecurityEngine:
    def __init__(self, model: ApplicationModel) -> None:
        self.model = model

    def effective_permissions(self, principal: Principal) -> frozenset[str]:
        permissions = set(principal.permissions)
        for role in principal.roles:
            permissions.update(self.model.roles.get(role, ()))
        return frozenset(permissions)

    def has_permission(self, context: RequestContext, permission: str | None) -> bool:
        return permission is not None and permission in self.effective_permissions(context.principal)

    def authorize_entity(self, entity: NormalizedEntity, operation: str, context: RequestContext) -> None:
        if not self.can_access_entity(entity, operation, context):
            raise AuthorizationError(
                f"{context.principal.identifier!r} may not {operation} {entity.name}"
            )

    def can_access_entity(
        self,
        entity: NormalizedEntity,
        operation: str,
        context: RequestContext,
    ) -> bool:
        permissions = entity.metadata.get("permissions", {})
        return self.has_permission(context, permissions.get(operation))

    def authorize_action(self, entity: NormalizedEntity, action: Mapping[str, Any], context: RequestContext) -> None:
        if not self.can_execute_action(action, context):
            raise AuthorizationError(
                f"{context.principal.identifier!r} may not execute this action on {entity.name}"
            )

    def can_execute_action(
        self,
        action: Mapping[str, Any],
        context: RequestContext,
    ) -> bool:
        return bool(
            action.get("unrestricted") is True
            or self.has_permission(context, action.get("permission"))
        )

    def authorize_report(
        self,
        report_name: str,
        report: Mapping[str, Any],
        context: RequestContext,
    ) -> None:
        if not self.can_access_report(report, context):
            raise AuthorizationError(
                f"{context.principal.identifier!r} may not generate report {report_name}"
            )

    def can_access_report(
        self,
        report: Mapping[str, Any],
        context: RequestContext,
    ) -> bool:
        return bool(
            report.get("unrestricted") is True
            or self.has_permission(context, report.get("permission"))
        )

    def can_read_field(self, entity: str, field: str, context: RequestContext) -> bool:
        return self._can_read_field(entity, field, context, frozenset())

    def can_write_field(self, entity: str, field: str, context: RequestContext) -> bool:
        permission = self._field_permission(entity, field, "write")
        return permission is None or self.has_permission(context, permission)

    def row_allowed(
        self,
        entity: str,
        operation: str,
        record: Mapping[str, Any],
        context: RequestContext,
    ) -> bool:
        return all(
            bool(evaluate_expression(criteria, record))
            for criteria in self.row_criteria(entity, operation)
        )

    def row_criteria(self, entity: str, operation: str) -> tuple[str, ...]:
        matching = [
            policy
            for policy in self.model.row_policies
            if policy["entity"] == entity and operation in policy["operations"]
        ]
        return tuple(str(policy["criteria"]) for policy in matching)

    def require_row(
        self,
        entity: str,
        operation: str,
        record: Mapping[str, Any],
        context: RequestContext,
    ) -> None:
        if not self.row_allowed(entity, operation, record, context):
            raise AuthorizationError(
                f"{context.principal.identifier!r} may not {operation} this {entity} record"
            )

    def _field_permission(self, entity: str, field: str, operation: str) -> str | None:
        for policy in self.model.field_policies:
            if policy["entity"] == entity and policy["field"] == field:
                return policy.get(operation)
        return None

    def _can_read_field(
        self,
        entity_name: str,
        field_name: str,
        context: RequestContext,
        visited: frozenset[tuple[str, str]],
    ) -> bool:
        key = entity_name, field_name
        if key in visited:
            return True
        permission = self._field_permission(entity_name, field_name, "read")
        if permission is not None and not self.has_permission(context, permission):
            return False
        entity = self.model.entity(entity_name)
        field = entity.fields[field_name]
        computed = field.metadata.get("computed")
        if not computed:
            return True
        visited = visited | {key}
        for dependency in field.dependencies:
            current_entity = entity
            parts = dependency.split(".")
            for index, part in enumerate(parts):
                if not self._can_read_field(current_entity.name, part, context, visited):
                    return False
                dependency_field = current_entity.fields[part]
                if index < len(parts) - 1 and dependency_field.target_entity:
                    current_entity = self.model.entity(dependency_field.target_entity)
        return True
