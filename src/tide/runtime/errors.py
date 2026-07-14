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
