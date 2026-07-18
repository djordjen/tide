"""Transactional-enough in-memory persistence for headless contract tests."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any, Iterable, Mapping

from tide.compiler.expressions import evaluate_expression
from tide.data.repository import (
    DeleteCollection,
    DeleteReference,
    QuerySpec,
    RelationshipLoadPlan,
    RowPolicyMismatch,
    SortField,
    matches_filter,
    query_sort_key,
)
from tide.runtime.errors import (
    ConcurrencyError,
    DeleteRestricted,
    NotFoundError,
    RelationshipExpansionLimit,
)


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
        relationships: RelationshipLoadPlan | None = None,
    ) -> list[dict[str, Any]]:
        if query.cursor is not None:
            raise ValueError("opaque cursors must be resolved by RecordsService")
        if query.limit < 1 or query.limit > 501:
            raise ValueError("repository query limit must be between 1 and 501")
        if query.after is not None and not query.sort:
            raise ValueError("query cursor boundary requires an effective sort")
        if query.after is not None and len(query.after) != len(query.sort):
            raise ValueError("query cursor boundary does not match the effective sort")
        records = self.all(entity)
        if relationships is not None:
            records = [
                _apply_relationship_plan(entity, record, relationships, depth=0)
                for record in records
            ]
        records = [
            record
            for record in records
            if all(bool(evaluate_expression(criteria, record)) for criteria in row_criteria)
        ]
        for condition in query.filters:
            records = [
                record for record in records if matches_filter(record, condition)
            ]
        if query.after is not None:
            records = [
                record
                for record in records
                if _record_is_after(record, query.sort, query.after)
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
        relationships: RelationshipLoadPlan | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(entity, {}).get(identity)
            if record is None:
                raise NotFoundError(f"{entity} {identity!r} was not found")
            result = deepcopy(record)
            if relationships is not None:
                result = _apply_relationship_plan(
                    entity,
                    result,
                    relationships,
                    depth=0,
                )
            if not all(
                bool(evaluate_expression(criteria, result))
                for criteria in row_criteria
            ):
                raise RowPolicyMismatch
            return result

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
    ) -> None:
        with self._lock:
            current = next(
                (
                    record
                    for record in self._entity_records(entity, collections)
                    if record.get(primary_key) == identity
                ),
                None,
            )
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

            snapshot = deepcopy(self._records)
            try:
                self._delete_entity(
                    entity,
                    identity,
                    references=references,
                    collections=collections,
                    visited=set(),
                )
            except Exception:
                self._records = snapshot
                raise

    def _delete_entity(
        self,
        entity: str,
        identity: Any,
        *,
        references: tuple[DeleteReference, ...],
        collections: tuple[DeleteCollection, ...],
        visited: set[tuple[str, Any]],
    ) -> None:
        key = entity, identity
        if key in visited:
            return
        visited.add(key)
        for reference in references:
            if reference.target_entity != entity:
                continue
            related = [
                record
                for record in self._entity_records(
                    reference.source_entity,
                    collections,
                )
                if record.get(reference.source_field) == identity
            ]
            if not related:
                continue
            relationship = f"{reference.source_entity}.{reference.source_field}"
            if reference.on_delete == "restrict":
                raise DeleteRestricted(entity, identity, relationship)
            if reference.on_delete == "set_null":
                for record in related:
                    record[reference.source_field] = None
                continue
            for record in related:
                self._delete_entity(
                    reference.source_entity,
                    record[reference.source_primary_key],
                    references=references,
                    collections=collections,
                    visited=visited,
                )
        self._remove_entity(entity, identity, collections)

    def _entity_records(
        self,
        entity: str,
        collections: tuple[DeleteCollection, ...],
        visiting: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        if entity in visiting:
            return list(self._records.get(entity, {}).values())
        records = list(self._records.get(entity, {}).values())
        for collection in collections:
            if collection.child_entity != entity:
                continue
            for parent in self._entity_records(
                collection.parent_entity,
                collections,
                visiting | {entity},
            ):
                children = parent.get(collection.parent_field) or []
                if isinstance(children, list):
                    records.extend(
                        child for child in children if isinstance(child, dict)
                    )
        return records

    def _remove_entity(
        self,
        entity: str,
        identity: Any,
        collections: tuple[DeleteCollection, ...],
    ) -> None:
        self._records.get(entity, {}).pop(identity, None)
        for collection in collections:
            if collection.child_entity != entity:
                continue
            for parent in self._entity_records(collection.parent_entity, collections):
                children = parent.get(collection.parent_field)
                if not isinstance(children, list):
                    continue
                parent[collection.parent_field] = [
                    child
                    for child in children
                    if not (
                        isinstance(child, dict)
                        and child.get(collection.child_primary_key) == identity
                    )
                ]


def _record_is_after(
    record: dict[str, Any],
    sort_fields: tuple[SortField, ...],
    boundary: tuple[Any, ...],
) -> bool:
    for sort, boundary_value in zip(sort_fields, boundary):
        value = record.get(sort.field)
        value_rank = _null_rank(value, sort.descending)
        boundary_rank = _null_rank(boundary_value, sort.descending)
        if value_rank != boundary_rank:
            return value_rank > boundary_rank
        if value is None:
            continue
        if value == boundary_value:
            continue
        return value < boundary_value if sort.descending else value > boundary_value
    return False


def _apply_relationship_plan(
    entity: str,
    record: dict[str, Any],
    plan: RelationshipLoadPlan,
    *,
    depth: int,
) -> dict[str, Any]:
    result = deepcopy(record)
    for load in plan.loads:
        if load.source_entity != entity:
            continue
        relationship = f"{entity}.{load.field}"
        source_items = result.get(load.field) or []
        if not isinstance(source_items, (list, tuple)):
            raise ValueError(f"relationship {relationship!r} is not a collection")
        if not all(isinstance(item, Mapping) for item in source_items):
            raise ValueError(f"relationship {relationship!r} contains an invalid record")
        items = [
            deepcopy(dict(item))
            for item in source_items
            if all(
                bool(evaluate_expression(criteria, item))
                for criteria in plan.criteria_for_entity(load.target_entity)
            )
        ]
        if items and depth >= plan.max_depth:
            raise RelationshipExpansionLimit(relationship, "depth")
        if len(items) > plan.max_items:
            raise RelationshipExpansionLimit(relationship, "item")
        if load.order_by:
            items.sort(key=lambda item: query_sort_key(item.get(load.order_by)))
        result[load.field] = [
            _apply_relationship_plan(
                load.target_entity,
                item,
                plan,
                depth=depth + 1,
            )
            for item in items
        ]
    return result


def _null_rank(value: Any, descending: bool) -> int:
    if descending:
        return 0 if value is None else 1
    return 1 if value is None else 0
