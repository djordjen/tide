"""Persistence contract consumed by application services."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


NO_PARAMETERS: Mapping[str, Any] = MappingProxyType({})
"""Empty binding set for row criteria that name no ``$`` parameter."""


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


class RowPolicyMismatch(Exception):
    """A row exists, but it does not satisfy repository-supplied criteria."""


class WriteIntegrityError(Exception):
    """The database refused a write for an integrity constraint.

    Raised instead of letting a driver-specific error escape, so the service
    can decide what the constraint means -- a duplicate a caller can fix reads
    very differently from a foreign key the caller cannot see. The original
    error stays attached as the cause.
    """

    def __init__(self, entity: str) -> None:
        super().__init__(f"{entity} write violated a database constraint")
        self.entity = entity


@runtime_checkable
class Repository(Protocol):
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
    ) -> tuple[DeletedRecord, ...]: ...
