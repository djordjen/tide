"""Persistence contract consumed by application services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


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


def matches_filter(record: Mapping[str, Any], condition: FilterCondition) -> bool:
    value = record.get(condition.field)
    operations = {
        "eq": lambda: value == condition.value,
        "ne": lambda: value != condition.value,
        "lt": lambda: value < condition.value,
        "lte": lambda: value <= condition.value,
        "gt": lambda: value > condition.value,
        "gte": lambda: value >= condition.value,
        "contains": lambda: value is not None and condition.value in value,
        "icontains": lambda: value is not None
        and condition.value.casefold() in value.casefold(),
    }
    if condition.operator not in operations:
        raise ValueError(f"unsupported filter operator {condition.operator!r}")
    return bool(operations[condition.operator]())


def query_sort_key(value: Any) -> tuple[bool, Any]:
    return value is None, value


class RowPolicyMismatch(Exception):
    """A row exists, but it does not satisfy repository-supplied criteria."""


@runtime_checkable
class Repository(Protocol):
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
        relationships: RelationshipLoadPlan | None = None,
    ) -> list[dict[str, Any]]: ...

    def get(
        self,
        entity: str,
        identity: Any,
        *,
        row_criteria: tuple[str, ...] = (),
        relationships: RelationshipLoadPlan | None = None,
    ) -> dict[str, Any]: ...

    def exists(self, entity: str, identity: Any) -> bool: ...

    def peek_next_identity(self, entity: str) -> int: ...

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
        references: tuple[DeleteReference, ...] = (),
        collections: tuple[DeleteCollection, ...] = (),
    ) -> None: ...
