"""Transactional-enough in-memory persistence for headless contract tests."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any, Iterable

from tide.compiler.expressions import evaluate_expression
from tide.data.repository import (
    QuerySpec,
    RowPolicyMismatch,
    matches_filter,
    query_sort_key,
)
from tide.runtime.errors import ConcurrencyError, NotFoundError


class InMemoryRepository:
    def __init__(self) -> None:
        self._records: dict[str, dict[Any, dict[str, Any]]] = {}
        self._next_identity: dict[str, int] = {}
        self._lock = RLock()

    def seed(self, entity: str, records: Iterable[dict[str, Any]], *, primary_key: str = "id") -> None:
        with self._lock:
            bucket = self._records.setdefault(entity, {})
            for source in records:
                record = deepcopy(source)
                identity = record[primary_key]
                bucket[identity] = record
                if isinstance(identity, int):
                    self._next_identity[entity] = max(
                        self._next_identity.get(entity, 1), identity + 1
                    )

    def all(self, entity: str) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(record) for record in self._records.get(entity, {}).values()]

    def query(
        self,
        entity: str,
        query: QuerySpec,
        *,
        row_criteria: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        records = [
            record
            for record in self.all(entity)
            if all(bool(evaluate_expression(criteria, record)) for criteria in row_criteria)
        ]
        for condition in query.filters:
            records = [
                record for record in records if matches_filter(record, condition)
            ]
        for sort in reversed(query.sort):
            records.sort(
                key=lambda record: query_sort_key(record.get(sort.field)),
                reverse=sort.descending,
            )
        return records[: query.limit]

    def get(
        self,
        entity: str,
        identity: Any,
        *,
        row_criteria: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(entity, {}).get(identity)
            if record is None:
                raise NotFoundError(f"{entity} {identity!r} was not found")
            if not all(
                bool(evaluate_expression(criteria, record))
                for criteria in row_criteria
            ):
                raise RowPolicyMismatch
            return deepcopy(record)

    def exists(self, entity: str, identity: Any) -> bool:
        with self._lock:
            return identity in self._records.get(entity, {})

    def peek_next_identity(self, entity: str) -> int:
        with self._lock:
            return self._next_identity.get(entity, 1)

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
    ) -> dict[str, Any]:
        with self._lock:
            bucket = self._records.setdefault(entity, {})
            record = deepcopy(values)
            identity = record.get(primary_key)
            if is_new:
                if identity is None:
                    identity = self._next_identity.get(entity, 1)
                    self._next_identity[entity] = identity + 1
                    record[primary_key] = identity
                if identity in bucket:
                    raise ConcurrencyError(None, None)
                if version_field:
                    record[version_field] = 1
            else:
                current = bucket.get(identity)
                if current is None:
                    raise NotFoundError(f"{entity} {identity!r} was not found")
                if not all(
                    bool(evaluate_expression(criteria, current))
                    for criteria in row_criteria
                ):
                    raise RowPolicyMismatch
                actual_version = current.get(version_field) if version_field else None
                if version_field and expected_version != actual_version:
                    raise ConcurrencyError(expected_version, actual_version)
                if version_field:
                    record[version_field] = int(actual_version) + 1
            bucket[identity] = deepcopy(record)
            return record
