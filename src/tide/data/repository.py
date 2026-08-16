"""Persistence contract consumed by application services."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from functools import wraps
from types import MappingProxyType
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    Protocol,
    Sequence,
    TypeVar,
    runtime_checkable,
)


NO_PARAMETERS: Mapping[str, Any] = MappingProxyType({})
"""Empty binding set for row criteria that name no ``$`` parameter."""

BATCH_IDENTITY_LIMIT = 500
"""How many identities one batched load may name in a single statement.

Every driver caps bound parameters -- SQLite at 999 by default, SQL Server at
2,100 -- and a page of records with several reference columns can ask about
more identities than that. Batches split at this width, so the cap is the
adapter's business and not the caller's.
"""


@dataclass(frozen=True, slots=True)
class FilterCondition:
    field: str
    operator: str
    value: Any


@dataclass(frozen=True, slots=True)
class SortField:
    field: str
    descending: bool = False


@dataclass(frozen=True, slots=True)
class QuerySpec:
    filters: tuple[FilterCondition, ...] = ()
    sort: tuple[SortField, ...] = ()
    limit: int = 100
    cursor: str | None = None
    after: tuple[Any, ...] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RelationshipLoad:
    source_entity: str
    field: str
    target_entity: str
    order_by: str | None = None


@dataclass(frozen=True, slots=True)
class RelationshipLoadPlan:
    loads: tuple[RelationshipLoad, ...] = ()
    entity_criteria: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Bindings for any ``$`` parameter the entity criteria name, carried with
    # them so hydration evaluates the same predicate the root query did.
    criteria_parameters: Mapping[str, Any] = field(
        default_factory=lambda: NO_PARAMETERS
    )
    max_depth: int = 3
    max_items: int = 1_000

    def __post_init__(self) -> None:
        if self.max_depth < 1:
            raise ValueError("relationship expansion depth must be positive")
        if self.max_items < 1:
            raise ValueError("relationship expansion item limit must be positive")
        keys = [(load.source_entity, load.field) for load in self.loads]
        if len(set(keys)) != len(keys):
            raise ValueError("relationship load fields must not be repeated")
        criteria_entities = [entity for entity, _criteria in self.entity_criteria]
        if len(set(criteria_entities)) != len(criteria_entities):
            raise ValueError("relationship criteria entities must not be repeated")

    def for_field(
        self,
        source_entity: str,
        field: str,
    ) -> RelationshipLoad | None:
        return next(
            (
                load
                for load in self.loads
                if load.source_entity == source_entity and load.field == field
            ),
            None,
        )

    def criteria_for_entity(self, entity: str) -> tuple[str, ...]:
        return next(
            (
                criteria
                for criteria_entity, criteria in self.entity_criteria
                if criteria_entity == entity
            ),
            (),
        )


@dataclass(frozen=True, slots=True)
class DeleteReference:
    """One stored reference that can affect deletion of its target record."""

    source_entity: str
    source_field: str
    source_primary_key: str
    target_entity: str
    on_delete: str


@dataclass(frozen=True, slots=True)
class DeleteCollection:
    """One embedded collection used by document-shaped repositories."""

    parent_entity: str
    parent_field: str
    child_entity: str
    child_primary_key: str


@dataclass(frozen=True, slots=True)
class DeletedRecord:
    """One row a delete removed, including rows it reached through a cascade.

    Only the repository knows what a delete actually touched: the caller's
    loaded copy is bounded and policy-filtered, so auditing from it would
    under-report. Reporting removals keeps the trail complete.
    """

    entity: str
    identity: Any
    values: Mapping[str, Any]


FILTER_OPERATORS = frozenset(
    {"eq", "ne", "lt", "lte", "gt", "gte", "contains", "icontains"}
)


def matches_filter(record: Mapping[str, Any], condition: FilterCondition) -> bool:
    """Apply one filter with the null semantics the SQL adapter produces.

    SQL evaluates every comparison involving NULL as unknown and drops the row,
    so a stored null matches nothing here either. Only a null *criterion* asks
    about presence, which SQL spells `IS NULL` / `IS NOT NULL`.
    """

    if condition.operator not in FILTER_OPERATORS:
        raise ValueError(f"unsupported filter operator {condition.operator!r}")
    value = record.get(condition.field)
    if condition.value is None:
        if condition.operator == "eq":
            return value is None
        if condition.operator == "ne":
            return value is not None
        return False
    if value is None:
        return False
    operations = {
        "eq": lambda: value == condition.value,
        "ne": lambda: value != condition.value,
        "lt": lambda: value < condition.value,
        "lte": lambda: value <= condition.value,
        "gt": lambda: value > condition.value,
        "gte": lambda: value >= condition.value,
        "contains": lambda: condition.value in value,
        "icontains": lambda: condition.value.casefold() in value.casefold(),
    }
    return bool(operations[condition.operator]())


def query_sort_key(value: Any) -> tuple[bool, Any]:
    return value is None, value


OnWritten = Callable[[Any, Mapping[str, Any]], None]
"""Called inside a write's transaction, with its connection and stored values.

Whatever it writes commits with the record or not at all. The first argument is
the adapter's transaction handle -- a SQLAlchemy `Connection`, or `None` for an
adapter that has none -- and is only meaningful to something that already knows
which adapter it is talking to.
"""


OnDeleted = Callable[[Any, tuple["DeletedRecord", ...]], None]
"""Called inside a delete's transaction, with its connection and what it removed.

The counterpart of `OnWritten`, and needed for the same reason: a record that
is gone and a record of its going have to land together. It matters more here,
because a create that is not audited can still be inspected afterwards and a
delete that is not audited leaves nothing to inspect. A cascade removes several
rows in one call, so the callback is handed all of them at once rather than
one at a time.
"""


def sequence_name(entity: str, field: str) -> str:
    """Name the sequence a generated field allocates from.

    The name is a plain string the repository knows nothing about, and three
    different places have to arrive at the same one: the generator that hands
    out values, the seeding or import that puts existing rows in, and whatever
    raises the floor when TIDE is adopted over a database that already has
    them. Deriving it from the entity and field is what stops those three
    remembering a string separately.

    A field that wants a sequence of its own name -- two fields sharing one, or
    a name that should outlive a rename -- would declare it, and this becomes
    the default rather than the rule. Nothing needs that yet.
    """

    return f"{entity}.{field}"


class RowPolicyMismatch(Exception):
    """A row exists, but it does not satisfy repository-supplied criteria."""


class WriteIntegrityError(Exception):
    """The database refused a write for an integrity constraint.

    Raised instead of letting a driver-specific error escape, so the service
    can decide what the constraint means -- a duplicate a caller can fix reads
    very differently from a foreign key the caller cannot see. The original
    error stays attached as the cause.
    """

    def __init__(self, entity: str, message: str | None = None) -> None:
        super().__init__(message or f"{entity} write violated a database constraint")
        self.entity = entity


class DuplicateIdentityError(WriteIntegrityError):
    """A new record was written under an identity another record already holds.

    A `WriteIntegrityError` because that is what it is -- a constraint the
    store refused -- but the one constraint worth naming, because it says
    which record and which number rather than that something went wrong.

    Nothing a client sends can cause this: the service refuses a caller-supplied
    primary key before any repository sees it. So reaching here means identity
    allocation issued a number twice, which is the server's fault and stays a
    server error; it was previously reported as a stale version, which told the
    caller to refresh and retry a write that can only fail again.
    """

    def __init__(self, entity: str, identity: Any) -> None:
        super().__init__(entity, f"{entity} {identity!r} already exists")
        self.identity = identity


class UnitOfWorkClosed(Exception):
    """A unit of work was used after the scope that owns it ended.

    The scope is the lifetime: once it commits or rolls back there is no
    transaction left to join, so a call arriving late would land outside the
    atomicity the caller asked for. Refusing is the only honest answer --
    silently running it is exactly the bug this whole contract exists to
    remove.
    """


class UnitOfWorkBypassed(Exception):
    """The repository a scope came from was used while that scope was open.

    What such a call does is not something the adapters can agree on, which
    is why it is refused rather than defined. The document store holds one
    lock and one snapshot, so a write that slips past the scope on the same
    thread is undone with it; a database hands out a second connection that
    commits on its own -- unless the pool is handing back the same one, as
    SQLite in memory does, in which case it joins after all. Three answers to
    one question is not a contract.

    A caller that meant to write outside the scope wants a second repository;
    a caller that meant to write inside it wants the object the scope yielded,
    which is the one thing it had to be holding to reach here.
    """


class UnitOfWorkFailed(Exception):
    """A scope was left in a state its own failure had already condemned.

    A failed write inside a unit of work dooms the whole scope, because
    without savepoints there is no way to undo just that write. Catching the
    error and carrying on would otherwise commit whatever ran before it,
    which is a partial write wearing a transaction's clothes.
    """



_Method = TypeVar("_Method", bound=Callable[..., Any])


def scoped(*, writes: bool) -> Callable[[_Method], _Method]:
    """Bind one repository method to the unit of work it is called on.

    Two lines every method in both adapters would otherwise repeat: refuse a
    scope that has already ended, and -- for a write -- condemn the scope when
    the call fails, because there is no savepoint to undo just that write
    with. A read that fails changes nothing, so it leaves the scope alone.

    It reaches for `_active_scope` and `_doom` on whatever it decorates, which
    is the only thing the two adapters' scopes have in common: one holds a
    dictionary snapshot and the other a database connection.
    """

    def decorate(method: _Method) -> _Method:
        @wraps(method)
        def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
            self._active_scope()
            try:
                return method(self, *args, **kwargs)
            except BaseException:
                if writes:
                    self._doom()
                raise

        return guarded  # type: ignore[return-value]

    return decorate


@runtime_checkable
class Repository(Protocol):
    def transaction(self) -> AbstractContextManager[Repository]:
        """Open a scope several reads and writes commit or roll back together.

        The scope yields a repository of its own; calls on *that* object join
        it, and calls on the original do not::

            with repository.transaction() as unit:
                unit.write(...)
                unit.write(...)

        Writing it this way rather than passing a `unit=` to every method is
        deliberate. A parameter can be forgotten at one call site out of ten,
        and the result is a write that quietly commits on its own inside what
        the reader takes to be a transaction; an object cannot be forgotten,
        because there is nothing else to call.

        Four rules, and both adapters keep all four:

        * a clean exit commits, and an exception rolls back;
        * a failed **write** dooms the scope: the unit refuses further work
          and the exit rolls back even if the caller swallowed the error.
          There are no savepoints to undo one write with, and how much of a
          refused statement survives is the backend's business -- some abort
          everything after it, SQLite carries on. A failed *read*
          changes nothing and does not doom, so a `NotFoundError` a caller
          means to handle stays handleable;
        * `transaction()` on a unit *joins* the scope it is already in rather
          than nesting a new one, so an operation built from two smaller ones
          is still a single commit. Only the outermost scope decides;
        * using a unit after its scope has ended raises `UnitOfWorkClosed`.

        Sequence allocation deliberately stays outside any scope it is called
        from -- see `next_sequence_value`, which commits its claim immediately
        so that an abandoned write leaves a gap rather than a held lock.
        """
        ...

    def check_readiness(self) -> None:
        """Raise when the persistence dependency cannot safely serve requests."""
        ...

    def seed(
        self,
        entity: str,
        records: Iterable[dict[str, Any]],
        *,
        primary_key: str = "id",
    ) -> None: ...

    def all(self, entity: str) -> list[dict[str, Any]]: ...

    def query(
        self,
        entity: str,
        query: QuerySpec,
        *,
        row_criteria: tuple[str, ...] = (),
        criteria_parameters: Mapping[str, Any] = NO_PARAMETERS,
        relationships: RelationshipLoadPlan | None = None,
    ) -> list[dict[str, Any]]: ...

    def get(
        self,
        entity: str,
        identity: Any,
        *,
        row_criteria: tuple[str, ...] = (),
        criteria_parameters: Mapping[str, Any] = NO_PARAMETERS,
        relationships: RelationshipLoadPlan | None = None,
    ) -> dict[str, Any]: ...

    def get_many(
        self,
        entity: str,
        identities: Sequence[Any],
        *,
        row_criteria: tuple[str, ...] = (),
        criteria_parameters: Mapping[str, Any] = NO_PARAMETERS,
    ) -> dict[Any, dict[str, Any]]:
        """Return the stored rows for ``identities``, keyed by identity.

        An identity the criteria refuse and an identity that is not there
        are both simply absent: the caller asked about rows it already holds
        references to, and telling those two apart would answer a question
        the row policy exists to refuse.

        Only stored scalar values come back. Child collections are left out
        rather than returned unfiltered, so nothing here can widen what a
        policy-filtered read would have shown.
        """
        ...

    def exists(self, entity: str, identity: Any) -> bool: ...

    def unique_conflict(
        self,
        entity: str,
        field: str,
        value: Any,
        *,
        exclude_identity: Any,
    ) -> bool: ...

    def peek_next_identity(self, entity: str) -> int: ...

    def next_sequence_value(self, name: str) -> int: ...

    def reserve_sequence_value(self, name: str, value: int) -> int: ...

    def write(
        self,
        entity: str,
        values: dict[str, Any],
        *,
        primary_key: str,
        version_field: str | None,
        expected_version: int | None,
        is_new: bool,
        row_criteria: tuple[str, ...] = (),
        criteria_parameters: Mapping[str, Any] = NO_PARAMETERS,
        references: tuple[DeleteReference, ...] = (),
        collections: tuple[DeleteCollection, ...] = (),
        on_written: OnWritten | None = None,
    ) -> dict[str, Any]: ...

    def delete(
        self,
        entity: str,
        identity: Any,
        *,
        primary_key: str,
        version_field: str | None,
        expected_version: int | None,
        row_criteria: tuple[str, ...] = (),
        criteria_parameters: Mapping[str, Any] = NO_PARAMETERS,
        references: tuple[DeleteReference, ...] = (),
        collections: tuple[DeleteCollection, ...] = (),
        on_deleted: OnDeleted | None = None,
    ) -> tuple[DeletedRecord, ...]: ...
