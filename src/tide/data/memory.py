"""Transactional-enough in-memory persistence for headless contract tests."""

from __future__ import annotations

from contextlib import contextmanager
from copy import copy, deepcopy
from dataclasses import dataclass
from threading import RLock, get_ident
from typing import Any, Iterable, Iterator, Mapping, Sequence

from tide.compiler.expressions import evaluate_expression
from tide.data.repository import (
    DeleteCollection,
    DeleteReference,
    DeletedRecord,
    DuplicateIdentityError,
    NO_PARAMETERS,
    OnDeleted,
    OnWritten,
    QuerySpec,
    RelationshipLoadPlan,
    RowPolicyMismatch,
    SortField,
    UnitOfWorkBypassed,
    UnitOfWorkClosed,
    UnitOfWorkFailed,
    matches_filter,
    scoped,
    query_sort_key,
)
from tide.runtime.errors import (
    ConcurrencyError,
    DeleteRestricted,
    NotFoundError,
    RelationshipExpansionLimit,
)


@dataclass
class _Scope:
    """One in-memory unit of work: what to put back, and whether to.

    The undo is a snapshot rather than a log because that is what `write` and
    `delete` already did by hand for their single-call case, and a dictionary
    of records is small enough that copying it is cheaper than replaying an
    inverse of every mutation. Identity allocation is deliberately outside it:
    a database sequence does not give numbers back on rollback either, and a
    reused identity is worse than a gap.
    """

    snapshot: dict[str, dict[Any, dict[str, Any]]]
    doomed: bool = False
    closed: bool = False


class InMemoryRepository:
    def __init__(self) -> None:
        self._records: dict[str, dict[Any, dict[str, Any]]] = {}
        self._next_identity: dict[str, int] = {}
        self._sequences: dict[str, int] = {}
        self._lock = RLock()
        self._scope: _Scope | None = None
        self._open_on_thread: int | None = None

    @contextmanager
    def transaction(self) -> Iterator[InMemoryRepository]:
        """Open a scope whose writes land together or not at all."""

        if self._scope is not None:
            # Joining rather than nesting: an operation built from two smaller
            # ones is still one commit, and only the outermost scope decides.
            with self._joined():
                yield self
            return
        with self._lock:
            scope = _Scope(snapshot=deepcopy(self._records))
            self._open_on_thread = get_ident()
            unit = copy(self)
            unit._scope = scope
            try:
                yield unit
            except BaseException:
                self._records = scope.snapshot
                raise
            else:
                if scope.doomed:
                    self._records = scope.snapshot
                    raise UnitOfWorkFailed(
                        "a failed write left this unit of work unable to commit"
                    )
            finally:
                scope.closed = True
                self._open_on_thread = None

    @contextmanager
    def _joined(self) -> Iterator[None]:
        """Run inside the scope already open, without deciding its outcome."""

        try:
            yield
        except BaseException:
            self._doom()
            raise

    def _active_scope(self) -> _Scope | None:
        scope = self._scope
        if scope is None:
            if self._open_on_thread == get_ident():
                raise UnitOfWorkBypassed(
                    "this repository has an open unit of work; call the "
                    "repository that scope yielded"
                )
            return None
        if scope.closed:
            raise UnitOfWorkClosed(
                "this unit of work belongs to a scope that has already ended"
            )
        return scope

    def _doom(self) -> None:
        scope = self._scope
        if scope is not None:
            scope.doomed = True

    def check_readiness(self) -> None:
        """The process-local store is ready when its synchronization primitive works."""
        with self._lock:
            return

    @scoped(writes=True)
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

    @scoped(writes=False)
    def all(self, entity: str) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(record) for record in self._records.get(entity, {}).values()]

    @scoped(writes=False)
    def query(
        self,
        entity: str,
        query: QuerySpec,
        *,
        row_criteria: tuple[str, ...] = (),
        criteria_parameters: Mapping[str, Any] = NO_PARAMETERS,
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
            if all(bool(evaluate_expression(criteria, record, parameters=criteria_parameters)) for criteria in row_criteria)
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

    @scoped(writes=False)
    def get(
        self,
        entity: str,
        identity: Any,
        *,
        row_criteria: tuple[str, ...] = (),
        criteria_parameters: Mapping[str, Any] = NO_PARAMETERS,
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
                bool(evaluate_expression(criteria, result, parameters=criteria_parameters))
                for criteria in row_criteria
            ):
                raise RowPolicyMismatch
            return result

    @scoped(writes=False)
    def get_many(
        self,
        entity: str,
        identities: Sequence[Any],
        *,
        row_criteria: tuple[str, ...] = (),
        criteria_parameters: Mapping[str, Any] = NO_PARAMETERS,
    ) -> dict[Any, dict[str, Any]]:
        """Return the stored scalars for ``identities`` the criteria admit.

        This repository holds no model, so a collection is recognised by its
        shape rather than by its declared type: children are stored inline as
        a list of mappings, and no scalar field type is ever a list.
        """

        with self._lock:
            stored = self._records.get(entity, {})
            found = {
                identity: {
                    name: deepcopy(value)
                    for name, value in stored[identity].items()
                    if not isinstance(value, (list, tuple))
                }
                for identity in dict.fromkeys(identities)
                if identity in stored
            }
        return {
            identity: values
            for identity, values in found.items()
            if all(
                bool(
                    evaluate_expression(
                        criteria,
                        values,
                        parameters=criteria_parameters,
                    )
                )
                for criteria in row_criteria
            )
        }

    @scoped(writes=False)
    def exists(self, entity: str, identity: Any) -> bool:
        with self._lock:
            return identity in self._records.get(entity, {})

    @scoped(writes=False)
    def unique_conflict(
        self,
        entity: str,
        field: str,
        value: Any,
        *,
        exclude_identity: Any,
    ) -> bool:
        """Report whether another record already holds ``value``.

        A dictionary has no index to ask, so this still walks the entity -- but
        it walks the stored mappings rather than hydrating copies of them with
        their child collections, which is what made the old scan expensive.
        """

        with self._lock:
            return any(
                identity != exclude_identity and record.get(field) == value
                for identity, record in self._records.get(entity, {}).items()
            )

    @scoped(writes=False)
    def peek_next_identity(self, entity: str) -> int:
        with self._lock:
            return self._next_identity.get(entity, 1)

    def next_sequence_value(self, name: str) -> int:
        """Claim the next value of a named sequence, once.

        The lock is what makes it a claim rather than a reading: two callers
        that overlap leave with different numbers, which is the whole point of
        allocating from a sequence instead of from what is currently stored.
        """

        with self._lock:
            value = self._sequences.get(name, 0) + 1
            self._sequences[name] = value
            return value

    def reserve_sequence_value(self, name: str, value: int) -> int:
        """Raise this sequence's floor to ``value``, never lower it.

        Adoption code runs more than once -- a re-import, a retried migration
        step -- and lowering a floor would reissue numbers already handed out,
        which is the defect this exists to prevent.
        """

        with self._lock:
            floor = max(self._sequences.get(name, 0), int(value))
            self._sequences[name] = floor
            return floor

    @scoped(writes=True)
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
    ) -> dict[str, Any]:
        with self._lock:
            bucket = self._records.setdefault(entity, {})
            record = deepcopy(values)
            self._assign_collection_identities(entity, record, collections)
            identity = record.get(primary_key)
            if is_new:
                if identity is None:
                    identity = self._next_identity.get(entity, 1)
                    self._next_identity[entity] = identity + 1
                    record[primary_key] = identity
                if identity in bucket:
                    raise DuplicateIdentityError(entity, identity)
                if version_field:
                    record[version_field] = 1
            else:
                current = bucket.get(identity)
                if current is None:
                    raise NotFoundError(f"{entity} {identity!r} was not found")
                if not all(
                    bool(evaluate_expression(criteria, current, parameters=criteria_parameters))
                    for criteria in row_criteria
                ):
                    raise RowPolicyMismatch
                actual_version = current.get(version_field) if version_field else None
                if version_field and expected_version != actual_version:
                    raise ConcurrencyError(expected_version, actual_version)
                if version_field:
                    record[version_field] = int(actual_version) + 1
            previous = bucket.get(identity)
            bucket[identity] = deepcopy(record)
            if on_written is not None:
                # There is no transaction to enlist in, so undo by hand: the
                # record and whatever accompanies it still have to land
                # together or not at all.
                try:
                    on_written(None, record)
                except Exception:
                    if previous is None:
                        bucket.pop(identity, None)
                    else:
                        bucket[identity] = previous
                    raise
            return record

    @scoped(writes=True)
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
    ) -> tuple[DeletedRecord, ...]:
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
                bool(evaluate_expression(criteria, current, parameters=criteria_parameters))
                for criteria in row_criteria
            ):
                raise RowPolicyMismatch
            actual_version = current.get(version_field) if version_field else None
            if version_field and expected_version != actual_version:
                raise ConcurrencyError(expected_version, actual_version)

            removed = self._removed_records(entity, identity, current, collections)
            snapshot = deepcopy(self._records)
            try:
                self._delete_entity(
                    entity,
                    identity,
                    references=references,
                    collections=collections,
                    visited=set(),
                )
                if on_deleted is not None:
                    on_deleted(None, removed)
            except Exception:
                self._records = snapshot
                raise
            return removed

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

    def _removed_records(
        self,
        entity: str,
        identity: Any,
        record: Mapping[str, Any],
        collections: tuple[DeleteCollection, ...],
    ) -> tuple[DeletedRecord, ...]:
        """Report a record and every embedded child that dies with it.

        Children live inside the parent here, so removing the parent destroys
        them without the reference cascade ever running. They still have to be
        reported, or the same delete would leave a different trail than it does
        against a relational schema.
        """

        removed: list[DeletedRecord] = []
        for collection in collections:
            if collection.parent_entity != entity:
                continue
            children = record.get(collection.parent_field)
            if not isinstance(children, list):
                continue
            for child in children:
                if not isinstance(child, Mapping):
                    continue
                removed.extend(
                    self._removed_records(
                        collection.child_entity,
                        child.get(collection.child_primary_key),
                        child,
                        collections,
                    )
                )
        removed.append(
            DeletedRecord(
                entity=entity,
                identity=identity,
                values=deepcopy(dict(record)),
            )
        )
        return tuple(removed)

    def _assign_collection_identities(
        self,
        entity: str,
        record: dict[str, Any],
        collections: tuple[DeleteCollection, ...],
    ) -> None:
        """Give every embedded collection item its own durable key.

        Children live inside the parent record here, so nothing else would ever
        allocate one. Without a key a commit cannot distinguish an edited row
        from a replacement, and the relational adapter -- where children are
        real rows -- would model the same data differently.
        """

        for collection in collections:
            if collection.parent_entity != entity:
                continue
            children = record.get(collection.parent_field)
            if not isinstance(children, list):
                continue
            key = collection.child_primary_key
            for child in children:
                if not isinstance(child, dict) or child.get(key) is not None:
                    continue
                child_identity = self._next_identity.get(collection.child_entity, 1)
                self._next_identity[collection.child_entity] = child_identity + 1
                child[key] = child_identity

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
                bool(
                    evaluate_expression(
                        criteria, item, parameters=plan.criteria_parameters
                    )
                )
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
