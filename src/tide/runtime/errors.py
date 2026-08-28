"""UI-independent application-service failures."""

from __future__ import annotations

from dataclasses import dataclass


class TideRuntimeError(Exception):
    code = "runtime_error"


class AuthorizationError(TideRuntimeError):
    code = "forbidden"


class NotFoundError(TideRuntimeError):
    code = "not_found"


class NullVersion:
    """The asserted version of a row whose concurrency token is NULL.

    Adopted tables hold rows whose token was never written, and the write
    path heals them on first save. ``None`` cannot carry that assertion --
    it already means "no version was supplied" at every precondition
    boundary -- so the wire's ``"null"`` travels as this instead: it
    compares equal to a loaded NULL, and the write that follows assigns
    version 1.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<null version>"


NULL_VERSION = NullVersion()


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


class QueryFieldError(ValueError):
    """A structured query named a field that cannot be used.

    A `ValueError` so the callers that already catch one keep working, and a
    type of its own so the transport can tell a message TIDE composed from one
    a library raised. The difference is between telling a client "unknown query
    field 'nope'", which is true, safe and actionable, and handing it
    "[<class 'decimal.ConversionSyntax'>]", which is none of those.
    """


class InvalidQueryCursor(TideRuntimeError):
    code = "invalid_query_cursor"

    def __init__(self) -> None:
        super().__init__("query cursor is invalid or expired")


class CursorStoreError(TideRuntimeError):
    code = "cursor_store_error"


class SessionStoreError(TideRuntimeError):
    code = "session_store_error"


class ServerLeaseError(TideRuntimeError):
    code = "server_lease_error"


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
