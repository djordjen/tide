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
    ) -> list[dict[str, Any]]: ...

    def get(
        self,
        entity: str,
        identity: Any,
        *,
        row_criteria: tuple[str, ...] = (),
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
