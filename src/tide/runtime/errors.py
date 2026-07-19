"""UI-independent application-service failures."""

from __future__ import annotations

from dataclasses import dataclass


class TideRuntimeError(Exception):
    code = "runtime_error"


class AuthorizationError(TideRuntimeError):
    code = "forbidden"


class NotFoundError(TideRuntimeError):
    code = "not_found"


class ConcurrencyError(TideRuntimeError):
    code = "stale_version"

    def __init__(self, expected: int | None, actual: int | None):
        self.expected = expected
        self.actual = actual
        super().__init__(f"record version changed: expected {expected}, current {actual}")


class VersionPreconditionRequired(TideRuntimeError):
    code = "version_precondition_required"

    def __init__(self, entity: str) -> None:
        self.entity = entity
        super().__init__(f"mutating {entity} requires an observed record version")


class DeleteRestricted(TideRuntimeError):
    code = "delete_restricted"

    def __init__(self, entity: str, identity: object, relationship: str | None = None):
        self.entity = entity
        self.identity = identity
        self.relationship = relationship
        suffix = f" by {relationship}" if relationship else ""
        super().__init__(f"{entity} {identity!r} cannot be deleted because it is referenced{suffix}")


class ImmutableFieldError(TideRuntimeError):
    code = "immutable_field"

    def __init__(self, field: str, reason: str):
        self.field = field
        super().__init__(f"field {field!r} cannot be changed: {reason}")


class InvalidSessionError(TideRuntimeError):
    code = "invalid_session"


class ActionDisabled(TideRuntimeError):
    code = "action_disabled"


class IdempotencyConflict(TideRuntimeError):
    code = "idempotency_conflict"


class ActionStoreError(TideRuntimeError):
    code = "action_store_error"


class InvalidQueryCursor(TideRuntimeError):
    code = "invalid_query_cursor"

    def __init__(self) -> None:
        super().__init__("query cursor is invalid or expired")


class CursorStoreError(TideRuntimeError):
    code = "cursor_store_error"


class RelationshipExpansionLimit(TideRuntimeError):
    code = "relationship_expansion_limit"

    def __init__(self, relationship: str, limit: str) -> None:
        self.relationship = relationship
        self.limit = limit
        super().__init__(f"relationship {relationship!r} exceeds the {limit} limit")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    rule: str
    message: str
    fields: tuple[str, ...] = ()
    severity: str = "error"


class ValidationFailed(TideRuntimeError):
    code = "validation_failed"

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in self.issues))
