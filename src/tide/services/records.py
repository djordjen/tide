"""Secured, UI-independent record and query services."""

from __future__ import annotations

import ast
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import re
from typing import Any, Callable, Mapping

from tide.compiler.expressions import evaluate_expression
from tide.compiler.normalized import ApplicationModel, NormalizedEntity
from tide.data.repository import (
    FilterCondition as FilterCondition,
    QuerySpec,
    RelationshipLoad,
    RelationshipLoadPlan,
    Repository,
    RowPolicyMismatch,
    SortField,
)
from tide.runtime.context import RequestContext
from tide.runtime.errors import (
    AuthorizationError,
    ImmutableFieldError,
    InvalidQueryCursor,
    RelationshipExpansionLimit,
    ValidationFailed,
    ValidationIssue,
)
from tide.security.engine import PROTECTED, SecurityEngine
from tide.services.cursors import (
    CURSOR_VERSION,
    CursorShape,
    CursorState,
    CursorStore,
    InMemoryCursorStore,
    QueryPage,
)
from tide.sessions.record_session import RecordSession


class MutationSource(StrEnum):
    USER = "user"
    ACTION = "action"
    SYSTEM = "system"


Generator = Callable[[dict[str, Any], RequestContext, Repository], Any]


class RecordsService:
    def __init__(
        self,
        model: ApplicationModel,
        repository: Repository,
        security: SecurityEngine | None = None,
        cursor_store: CursorStore | None = None,
        relationship_max_depth: int = 3,
        relationship_max_items: int = 1_000,
    ) -> None:
        if relationship_max_depth < 1:
            raise ValueError("relationship expansion depth must be positive")
        if relationship_max_items < 1:
            raise ValueError("relationship expansion item limit must be positive")
        self.model = model
        self.repository = repository
        self.security = security or SecurityEngine(model)
        self.cursor_store = (
            cursor_store if cursor_store is not None else InMemoryCursorStore()
        )
        self.relationship_max_depth = relationship_max_depth
        self.relationship_max_items = relationship_max_items
        self._generators: dict[str, Generator] = {}

    def register_generator(self, reference: str, generator: Generator) -> None:
        self._generators[reference] = generator

    def create(
        self,
        entity_name: str,
        context: RequestContext,
        values: Mapping[str, Any] | None = None,
    ) -> RecordSession:
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "create", context)
        defaults: dict[str, Any] = {}
        for field_name, field in entity.fields.items():
            metadata = field.metadata
            if field.target_entity and metadata["type"] == "collection":
                defaults[field_name] = []
            elif metadata.get("default_factory") == "today":
                defaults[field_name] = date.today()
            elif "default" in metadata:
                defaults[field_name] = deepcopy(metadata["default"])
        initial = deepcopy(defaults)
        initial.update(deepcopy(dict(values or {})))
        version_field = _version_field(entity)
        return RecordSession(
            entity=entity_name,
            identity=initial.get(_primary_key(entity)),
            original=defaults,
            values=initial,
            expected_version=initial.get(version_field) if version_field else None,
            is_new=True,
        )

    def begin_edit(self, entity_name: str, identity: Any, context: RequestContext) -> RecordSession:
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "read", context)
        self.security.authorize_entity(entity, "update", context)
        values = self._load_authorized(
            entity_name, identity, context, operations=("read", "update")
        )
        version_field = _version_field(entity)
        return RecordSession(
            entity=entity_name,
            identity=identity,
            original=deepcopy(values),
            values=deepcopy(values),
            expected_version=values.get(version_field) if version_field else None,
        )

    def begin_action(self, entity_name: str, identity: Any, context: RequestContext) -> RecordSession:
        """Open an action target without requiring the separate entity-update grant."""

        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "read", context)
        values = self._load_authorized(
            entity_name, identity, context, operations=("read",)
        )
        version_field = _version_field(entity)
        return RecordSession(
            entity=entity_name,
            identity=identity,
            original=deepcopy(values),
            values=deepcopy(values),
            expected_version=values.get(version_field) if version_field else None,
        )

    def get(self, entity_name: str, identity: Any, context: RequestContext) -> dict[str, Any]:
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "read", context)
        values = self._load_authorized(
            entity_name, identity, context, operations=("read",)
        )
        return self._project(entity, values, context)

    def lookup_records(
        self,
        entity_name: str,
        search_fields: tuple[str, ...],
        search_text: str,
        context: RequestContext,
        *,
        limit: int = 20,
    ) -> tuple[dict[str, Any], ...]:
        """Return a bounded secured lookup result, matching any search field."""

        if not search_fields:
            raise ValueError("lookup search requires at least one field")
        if len(set(search_fields)) != len(search_fields):
            raise ValueError("lookup search fields must not be repeated")
        if limit < 1 or limit > 500:
            raise ValueError("lookup limit must be between 1 and 500")
        entity = self.model.entity(entity_name)
        primary_key = _primary_key(entity)
        if not self.security.can_read_field(entity_name, primary_key, context):
            raise AuthorizationError("lookup primary key is not readable")
        sort = (SortField(search_fields[0]),)
        candidate = search_text.strip()
        if not candidate:
            return tuple(
                self.query(
                    entity_name,
                    QuerySpec(sort=sort, limit=limit),
                    context,
                )
            )
        matches: dict[Any, dict[str, Any]] = {}
        for field_name in search_fields:
            page = self.query_page(
                entity_name,
                QuerySpec(
                    filters=(FilterCondition(field_name, "icontains", candidate),),
                    sort=sort,
                    limit=limit,
                ),
                context,
            )
            for record in page.records:
                matches.setdefault(record[primary_key], record)
                if len(matches) >= limit:
                    return tuple(matches.values())
        return tuple(matches.values())

    def apply_reference_selection(
        self,
        entity_name: str,
        field_name: str,
        values: Mapping[str, Any],
        identity: Any,
        context: RequestContext,
    ) -> dict[str, Any]:
        """Apply a secured reference choice and its declarative draft assignments."""

        entity = self.model.entity(entity_name)
        if field_name not in entity.fields:
            raise ValueError(f"unknown field {field_name!r}")
        reference = entity.field(field_name)
        if reference.metadata["type"] != "reference" or not reference.target_entity:
            raise ValueError(f"field {field_name!r} is not a reference")
        if reference.metadata.get("readonly") or reference.metadata.get(
            "write", "normal"
        ) != "normal":
            raise ImmutableFieldError(field_name, "reference field is not user-writable")
        if not self.security.can_write_field(entity_name, field_name, context):
            raise AuthorizationError(f"field {field_name!r} is not writable")

        selected = self.get(reference.target_entity, identity, context)
        target_entity = self.model.entity(reference.target_entity)
        target_key = _primary_key(target_entity)
        if selected.get(target_key) is PROTECTED:
            raise AuthorizationError("lookup primary key is not readable")
        result = deepcopy(dict(values))
        result[field_name] = deepcopy(selected[target_key])
        on_select = reference.metadata.get("on_select", {})
        for destination_name, assignment in on_select.get("assign", {}).items():
            destination = entity.field(destination_name)
            if destination.metadata.get("readonly") or destination.metadata.get(
                "write", "normal"
            ) != "normal":
                raise ImmutableFieldError(
                    destination_name,
                    "selection assignment target is not user-writable",
                )
            if not self.security.can_write_field(entity_name, destination_name, context):
                raise AuthorizationError(f"field {destination_name!r} is not writable")
            current = result.get(destination_name)
            if (
                assignment.get("overwrite", "always") == "when_blank"
                and current is not None
                and current != ""
            ):
                continue
            source_name = assignment["from"]
            source_value = selected.get(source_name)
            if source_value is PROTECTED:
                raise AuthorizationError(
                    f"field {reference.target_entity}.{source_name!s} is not readable"
                )
            result[destination_name] = deepcopy(source_value)
        return result

    def _load_authorized(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
        *,
        operations: tuple[str, ...],
    ) -> dict[str, Any]:
        criteria = tuple(
            criterion
            for operation in operations
            for criterion in self.security.row_criteria(entity_name, operation)
        )
        try:
            values = self.repository.get(
                entity_name,
                identity,
                row_criteria=criteria,
                relationships=self._relationship_plan(
                    entity_name,
                    context,
                    operations=operations,
                ),
            )
        except RowPolicyMismatch as error:
            raise AuthorizationError(
                f"{context.principal.identifier!r} may not access this {entity_name} record"
            ) from error
        for operation in operations:
            self.security.require_row(
                entity_name,
                operation,
                self._policy_values(entity_name, values, operation, context),
                context,
            )
        return values

    def query(
        self,
        entity_name: str,
        query: QuerySpec,
        context: RequestContext,
    ) -> list[dict[str, Any]]:
        return list(self.query_page(entity_name, query, context).records)

    def query_page(
        self,
        entity_name: str,
        query: QuerySpec,
        context: RequestContext,
    ) -> QueryPage:
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "list", context)
        if query.limit < 1 or query.limit > 500:
            raise ValueError("query limit must be between 1 and 500")
        if query.after is not None:
            raise ValueError("query cursor boundaries are internal to RecordsService")
        if query.cursor is not None and (
            not isinstance(query.cursor, str) or not query.cursor
        ):
            raise InvalidQueryCursor
        requested_sort_names = [sort.field for sort in query.sort]
        if len(set(requested_sort_names)) != len(requested_sort_names):
            raise ValueError("query sort fields must not be repeated")
        for field_name in [condition.field for condition in query.filters] + [
            sort.field for sort in query.sort
        ]:
            if field_name not in entity.fields:
                raise ValueError(f"unknown query field {field_name!r}")
            if not self.security.can_read_field(entity_name, field_name, context):
                raise AuthorizationError(f"field {field_name!r} cannot be used for filtering or sorting")
            field = entity.fields[field_name]
            computed = field.metadata.get("computed")
            if field.metadata["type"] == "collection" or (
                computed and computed.get("materialization") == "virtual"
            ):
                raise ValueError(
                    f"field {field_name!r} is not stored and cannot be queried"
                )
        normalized_filters = tuple(
            _normalize_filter(self.model, entity, condition)
            for condition in query.filters
        )
        primary_key = _primary_key(entity)
        sort_fields = list(query.sort)
        if not any(sort.field == primary_key for sort in sort_fields):
            sort_fields.append(SortField(primary_key))
        effective_sort = tuple(sort_fields)
        shape = CursorShape(
            model=(self.model.name, self.model.version, self.model.schema_version),
            entity=entity_name,
            filters=normalized_filters,
            sort=effective_sort,
            limit=query.limit,
            principal=(
                context.principal.identifier,
                tuple(sorted(self.security.effective_permissions(context.principal))),
            ),
        )
        after: tuple[Any, ...] | None = None
        if query.cursor is not None:
            state = self.cursor_store.resolve(query.cursor)
            if (
                state.version != CURSOR_VERSION
                or state.shape != shape
                or len(state.values) != len(effective_sort)
            ):
                raise InvalidQueryCursor
            after = state.values
        repository_query = QuerySpec(
            filters=normalized_filters,
            sort=effective_sort,
            limit=query.limit + 1,
            after=after,
        )
        records = self.repository.query(
            entity_name,
            repository_query,
            row_criteria=self.security.row_criteria(entity_name, "list"),
            relationships=self._relationship_plan(
                entity_name,
                context,
                operations=("list",),
            ),
        )
        policy_cache: dict[tuple[str, Any], dict[str, Any]] = {}
        authorized: list[dict[str, Any]] = []
        for record in records:
            if not self.security.row_allowed(
                entity_name,
                "list",
                self._policy_values(
                    entity_name,
                    record,
                    "list",
                    context,
                    cache=policy_cache,
                ),
                context,
            ):
                raise AuthorizationError("query result failed its row-policy recheck")
            authorized.append(record)

        has_more = len(authorized) > query.limit
        page_records = authorized[: query.limit]
        next_cursor = None
        if has_more and page_records:
            next_cursor = self.cursor_store.issue(
                CursorState(
                    version=CURSOR_VERSION,
                    shape=shape,
                    values=tuple(
                        page_records[-1].get(sort.field)
                        for sort in effective_sort
                    ),
                )
            )
        return QueryPage(
            records=tuple(
                self._project(entity, record, context)
                for record in page_records
            ),
            next_cursor=next_cursor,
        )

    def commit(
        self,
        session: RecordSession,
        context: RequestContext,
        *,
        source: MutationSource = MutationSource.USER,
    ) -> dict[str, Any]:
        session.ensure_active()
        entity = self.model.entity(session.entity)
        operation = "create" if session.is_new else "update"
        if source is MutationSource.ACTION:
            self.security.authorize_entity(entity, "read", context)
        else:
            self.security.authorize_entity(entity, operation, context)
        if not session.is_new and source is MutationSource.ACTION:
            self.security.require_row(
                entity.name,
                "read",
                self._policy_values(
                    entity.name,
                    session.original,
                    "read",
                    context,
                ),
                context,
            )
        elif not session.is_new:
            self.security.require_row(
                entity.name,
                "update",
                self._policy_values(
                    entity.name,
                    session.original,
                    "update",
                    context,
                ),
                context,
            )
        self._enforce_changes(entity, session, context, source)
        values = deepcopy(session.values)
        input_issues = [
            *self._coerce_values(entity.name, values),
            *self._missing_required_inputs(entity, values),
        ]
        if input_issues:
            raise ValidationFailed(input_issues)
        self._apply_generators(entity, values, context)
        self._compute_entity(entity.name, values)
        derived_issues = self._coerce_values(entity.name, values)
        if derived_issues:
            raise ValidationFailed(derived_issues)
        issues = self._validate_entity(entity.name, values)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            raise ValidationFailed(errors)
        if session.is_new:
            self.security.require_row(
                entity.name,
                "create",
                self._policy_values(entity.name, values, "create", context),
                context,
            )
        self._validate_uniqueness(entity, values, session.identity)
        write_operation = "read" if source is MutationSource.ACTION else operation
        try:
            stored = self.repository.write(
                entity.name,
                values,
                primary_key=_primary_key(entity),
                version_field=_version_field(entity),
                expected_version=session.expected_version,
                is_new=session.is_new,
                row_criteria=(
                    ()
                    if session.is_new
                    else self.security.row_criteria(entity.name, write_operation)
                ),
            )
        except RowPolicyMismatch as error:
            raise AuthorizationError(
                f"{context.principal.identifier!r} may not {write_operation} this "
                f"{entity.name} record"
            ) from error
        session.identity = stored[_primary_key(entity)]
        version_field = _version_field(entity)
        session.expected_version = (
            stored.get(version_field) if version_field is not None else None
        )
        session.mark_committed(stored)
        return self._project(entity, stored, context)

    def _missing_required_inputs(
        self, entity: NormalizedEntity, values: Mapping[str, Any]
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field_name, field in entity.fields.items():
            metadata = field.metadata
            if not metadata.get("required"):
                continue
            if metadata.get("generated_by") or metadata.get("primary_key") or metadata.get("computed"):
                continue
            value = values.get(field_name)
            if value is None or value == "":
                issues.append(
                    ValidationIssue("required", f"{field_name} is required", (field_name,))
                )
        return issues

    def rollback(self, session: RecordSession) -> None:
        session.rollback()

    def _enforce_changes(
        self,
        entity: NormalizedEntity,
        session: RecordSession,
        context: RequestContext,
        source: MutationSource,
    ) -> None:
        unknown = set(session.values) - set(entity.fields)
        if unknown:
            raise ValidationFailed(
                [ValidationIssue("unknown_field", f"unknown field {name!r}", (name,)) for name in sorted(unknown)]
            )
        for field_name in session.changed_fields:
            field = entity.fields[field_name]
            metadata = field.metadata
            write_mode = metadata.get("write", "normal")
            if source is not MutationSource.SYSTEM and metadata.get("primary_key"):
                raise ImmutableFieldError(field_name, "primary keys are system-owned")
            if source is MutationSource.USER and (metadata.get("readonly") or write_mode != "normal"):
                raise ImmutableFieldError(field_name, f"write mode is {write_mode}")
            if source is MutationSource.ACTION and write_mode == "system":
                raise ImmutableFieldError(field_name, "field is system-owned")
            if (
                source is MutationSource.ACTION
                and metadata.get("readonly")
                and write_mode == "normal"
                and not metadata.get("computed")
            ):
                raise ImmutableFieldError(field_name, "readonly field is not action-owned")
            if source is not MutationSource.SYSTEM and not self.security.can_write_field(entity.name, field_name, context):
                raise AuthorizationError(f"field {field_name!r} is not writable")
            immutable_when = metadata.get("immutable_when")
            if immutable_when and bool(evaluate_expression(immutable_when, session.original)):
                raise ImmutableFieldError(field_name, f"condition {immutable_when!r} is true")

    def _coerce_values(self, entity_name: str, values: dict[str, Any]) -> list[ValidationIssue]:
        """Coerce present values to their declared field types before any evaluation."""

        entity = self.model.entity(entity_name)
        issues: list[ValidationIssue] = []
        for field_name, field in entity.fields.items():
            value = values.get(field_name)
            if value is None:
                continue
            field_type = field.metadata["type"]
            if field_type == "reference":
                if field.target_entity is None:
                    issues.append(
                        ValidationIssue(
                            "reference",
                            f"{field_name} has no reference target",
                            (field_name,),
                        )
                    )
                    continue
                target = self.model.entity(field.target_entity)
                target_key = target.field(_primary_key(target))
                target_key_type = target_key.metadata["type"]
                coerced, valid = _coerce_scalar(target_key_type, value)
                if not valid:
                    issues.append(
                        ValidationIssue(
                            "type",
                            f"{field_name} must be a {target_key_type} reference value",
                            (field_name,),
                        )
                    )
                    continue
                values[field_name] = coerced
                if not self.repository.exists(field.target_entity, coerced):
                    issues.append(
                        ValidationIssue(
                            "reference",
                            f"{field_name} must reference an existing {field.target_entity}",
                            (field_name,),
                        )
                    )
                continue
            if field_type == "collection":
                if not isinstance(value, list):
                    issues.append(
                        ValidationIssue(
                            "type", f"{field_name} must be a list of records", (field_name,)
                        )
                    )
                    continue
                if field.target_entity:
                    for item in value:
                        if not isinstance(item, dict):
                            issues.append(
                                ValidationIssue(
                                    "type",
                                    f"{field_name} items must be records",
                                    (field_name,),
                                )
                            )
                            continue
                        issues.extend(self._coerce_values(field.target_entity, item))
                continue
            coerced, valid = _coerce_scalar(field_type, value)
            if valid:
                values[field_name] = coerced
            else:
                issues.append(
                    ValidationIssue(
                        "type", f"{field_name} must be a {field_type} value", (field_name,)
                    )
                )
        return issues

    def _apply_generators(self, entity: NormalizedEntity, values: dict[str, Any], context: RequestContext) -> None:
        for field_name, field in entity.fields.items():
            reference = field.metadata.get("generated_by")
            if reference and values.get(field_name) in {None, ""}:
                generator = self._generators.get(reference)
                if generator is None:
                    raise RuntimeError(f"no generator registered for {reference}")
                values[field_name] = generator(values, context, self.repository)

    def _compute_entity(self, entity_name: str, values: dict[str, Any]) -> None:
        entity = self.model.entity(entity_name)
        for field_name, field in entity.fields.items():
            if field.metadata["type"] == "collection" and field.target_entity:
                items = values.get(field_name) or []
                for item in items:
                    self._compute_entity(field.target_entity, item)
        remaining = {
            name
            for name, field in entity.fields.items()
            if field.metadata.get("computed", {}).get("materialization") == "stored"
        }
        while remaining:
            progressed = False
            for field_name in tuple(remaining):
                field = entity.fields[field_name]
                local_dependencies = {dependency.split(".", 1)[0] for dependency in field.dependencies}
                if local_dependencies & remaining:
                    continue
                values[field_name] = evaluate_expression(
                    field.metadata["computed"]["expression"], values
                )
                remaining.remove(field_name)
                progressed = True
            if not progressed:
                raise RuntimeError(f"computed dependency cycle in {entity_name}")

    def _validate_entity(
        self,
        entity_name: str,
        values: dict[str, Any],
        *,
        skip_fields: frozenset[str] = frozenset(),
    ) -> list[ValidationIssue]:
        entity = self.model.entity(entity_name)
        issues: list[ValidationIssue] = []
        for field_name, field in entity.fields.items():
            if field_name in skip_fields:
                continue
            metadata = field.metadata
            value = values.get(field_name)
            if metadata.get("required") and (value is None or value == ""):
                issues.append(ValidationIssue("required", f"{field_name} is required", (field_name,)))
                continue
            if value is not None and metadata.get("minimum") is not None and value < metadata["minimum"]:
                issues.append(ValidationIssue("minimum", f"{field_name} is below its minimum", (field_name,)))
            if value is not None and metadata.get("maximum") is not None and value > metadata["maximum"]:
                issues.append(ValidationIssue("maximum", f"{field_name} exceeds its maximum", (field_name,)))
            if value is not None and metadata["type"] == "choice" and value not in metadata.get("choices", ()):
                issues.append(ValidationIssue("choice", f"{field_name} has an invalid choice", (field_name,)))
            if value is not None and metadata["type"] == "decimal":
                issues.extend(_decimal_shape_issues(field_name, value, metadata))
            edit_mask = metadata.get("edit_mask")
            if (
                value is not None
                and metadata["type"] == "string"
                and isinstance(edit_mask, Mapping)
                and re.fullmatch(str(edit_mask["regex"]), value) is None
            ):
                issues.append(
                    ValidationIssue(
                        "edit_mask",
                        f"{field_name} does not match its required format",
                        (field_name,),
                    )
                )
            if metadata["type"] == "collection" and field.target_entity:
                inverse = metadata.get("inverse")
                for item in value or []:
                    issues.extend(
                        self._validate_entity(
                            field.target_entity,
                            item,
                            skip_fields=frozenset({inverse}) if inverse else frozenset(),
                        )
                    )
        for rule in entity.metadata.get("validations", ()):
            when = rule.get("when")
            if when and not evaluate_expression(when, values):
                continue
            assertion = rule.get("assert")
            if assertion and not evaluate_expression(assertion, values):
                issues.append(
                    ValidationIssue(
                        rule["id"],
                        rule["message"],
                        tuple(rule.get("fields", ())),
                        rule.get("severity", "error"),
                    )
                )
        return issues

    def _validate_uniqueness(self, entity: NormalizedEntity, values: dict[str, Any], identity: Any) -> None:
        for field_name, field in entity.fields.items():
            if not field.metadata.get("unique"):
                continue
            if values.get(field_name) is None:
                continue
            for record in self.repository.all(entity.name):
                if record.get(_primary_key(entity)) != identity and record.get(field_name) == values.get(field_name):
                    raise ValidationFailed(
                        [ValidationIssue("unique", f"{field_name} must be unique", (field_name,))]
                    )

    def _relationship_plan(
        self,
        entity_name: str,
        context: RequestContext,
        *,
        operations: tuple[str, ...],
    ) -> RelationshipLoadPlan:
        loads: dict[tuple[str, str], RelationshipLoad] = {}
        visited: set[tuple[str, tuple[str, ...]]] = set()

        def visit(current_name: str, policy_operations: tuple[str, ...]) -> None:
            visit_key = current_name, policy_operations
            if visit_key in visited:
                return
            visited.add(visit_key)
            current = self.model.entity(current_name)
            required = _policy_collection_edges(
                self.model,
                self.security,
                current_name,
                policy_operations,
            )
            for field_name, field in current.fields.items():
                if field.metadata["type"] != "collection" or not field.target_entity:
                    continue
                target = self.model.entity(field.target_entity)
                visible = self.security.can_read_field(
                    current_name,
                    field_name,
                    context,
                ) and self.security.can_access_entity(target, "read", context)
                if not visible and (current_name, field_name) not in required:
                    continue
                loads[(current_name, field_name)] = RelationshipLoad(
                    source_entity=current_name,
                    field=field_name,
                    target_entity=target.name,
                    order_by=field.metadata.get("order_by"),
                )
                visit(target.name, ("read",))

        visit(entity_name, operations)
        entity_criteria = tuple(
            (candidate, criteria)
            for candidate in self.model.entities
            if (criteria := self.security.row_criteria(candidate, "read"))
        )
        return RelationshipLoadPlan(
            loads=tuple(loads.values()),
            entity_criteria=entity_criteria,
            max_depth=self.relationship_max_depth,
            max_items=self.relationship_max_items,
        )

    def _project(
        self,
        entity: NormalizedEntity,
        source: Mapping[str, Any],
        context: RequestContext,
        *,
        depth: int = 0,
    ) -> dict[str, Any]:
        values = deepcopy(dict(source))
        for field_name, field in entity.fields.items():
            computed = field.metadata.get("computed")
            if computed and computed.get("materialization") == "virtual":
                values[field_name] = evaluate_expression(computed["expression"], values)
        result: dict[str, Any] = {}
        for field_name, field in entity.fields.items():
            if not self.security.can_read_field(entity.name, field_name, context):
                result[field_name] = PROTECTED
                continue
            value = values.get(field_name)
            if field.metadata["type"] == "collection" and field.target_entity:
                target = self.model.entity(field.target_entity)
                if not self.security.can_access_entity(target, "read", context):
                    result[field_name] = PROTECTED
                    continue
                items = value or []
                if not isinstance(items, (list, tuple)):
                    raise ValueError(
                        f"relationship {entity.name}.{field_name!s} is not a collection"
                    )
                if not all(isinstance(item, Mapping) for item in items):
                    raise ValueError(
                        f"relationship {entity.name}.{field_name!s} contains an invalid record"
                    )
                visible_items = [
                    item
                    for item in items
                    if self.security.row_allowed(
                        target.name,
                        "read",
                        self._policy_values(target.name, item, "read", context),
                        context,
                    )
                ]
                relationship = f"{entity.name}.{field_name}"
                if visible_items and depth >= self.relationship_max_depth:
                    raise RelationshipExpansionLimit(relationship, "depth")
                if len(visible_items) > self.relationship_max_items:
                    raise RelationshipExpansionLimit(relationship, "item")
                result[field_name] = [
                    self._project(target, item, context, depth=depth + 1)
                    for item in visible_items
                ]
            else:
                result[field_name] = deepcopy(value)
        return result

    def _policy_values(
        self,
        entity_name: str,
        source: Mapping[str, Any],
        operation: str,
        context: RequestContext,
        *,
        cache: dict[tuple[str, Any], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        values = deepcopy(dict(source))
        entity = self.model.entity(entity_name)
        relationship_cache = cache if cache is not None else {}
        paths = {
            path
            for criteria in self.security.row_criteria(entity_name, operation)
            for path in _expression_paths(criteria)
            if path and path[0] in entity.fields
        }
        for path in paths:
            self._expand_policy_path(
                entity,
                values,
                path,
                relationship_cache,
                context,
            )
        return values

    def _expand_policy_path(
        self,
        entity: NormalizedEntity,
        values: dict[str, Any],
        path: tuple[str, ...],
        cache: dict[tuple[str, Any], dict[str, Any]],
        context: RequestContext,
    ) -> None:
        field = entity.fields.get(path[0])
        if field is None or len(path) == 1 or field.target_entity is None:
            return
        value = values.get(field.name)
        if value is None:
            return
        target = self.model.entity(field.target_entity)
        remainder = path[1:]

        if field.metadata["type"] == "collection":
            if not isinstance(value, (list, tuple)):
                raise ValueError(
                    f"collection {entity.name}.{field.name} is not available for policy evaluation"
                )
            expanded: list[dict[str, Any]] = []
            for item in value:
                if not isinstance(item, Mapping):
                    raise ValueError(
                        f"collection {entity.name}.{field.name} contains an invalid policy value"
                    )
                related = deepcopy(dict(item))
                self._expand_policy_path(
                    target,
                    related,
                    remainder,
                    cache,
                    context,
                )
                expanded.append(related)
            values[field.name] = expanded
            return

        if field.metadata["type"] != "reference":
            return
        if isinstance(value, Mapping):
            related = deepcopy(dict(value))
            if not self.security.row_allowed(target.name, "read", related, context):
                raise AuthorizationError("related record failed its row-policy recheck")
        else:
            key = (target.name, value)
            if key not in cache:
                try:
                    cache[key] = self.repository.get(
                        target.name,
                        value,
                        row_criteria=self.security.row_criteria(target.name, "read"),
                        relationships=self._relationship_plan(
                            target.name,
                            context,
                            operations=("read",),
                        ),
                    )
                except RowPolicyMismatch as error:
                    raise AuthorizationError(
                        "related record failed its row-policy recheck"
                    ) from error
            related = deepcopy(cache[key])
        self._expand_policy_path(
            target,
            related,
            remainder,
            cache,
            context,
        )
        values[field.name] = related


def _primary_key(entity: NormalizedEntity) -> str:
    return next(name for name, field in entity.fields.items() if field.metadata.get("primary_key"))


class _ExpressionPathCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.paths: set[tuple[str, ...]] = set()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        parts = _attribute_parts(node)
        if parts:
            self.paths.add(parts)

    def visit_Name(self, node: ast.Name) -> None:
        self.paths.add((node.id,))

    def visit_Call(self, node: ast.Call) -> None:
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)


def _expression_paths(expression: str) -> tuple[tuple[str, ...], ...]:
    tree = ast.parse(expression, mode="eval")
    collector = _ExpressionPathCollector()
    collector.visit(tree)
    return tuple(sorted(collector.paths))


def _policy_collection_edges(
    model: ApplicationModel,
    security: SecurityEngine,
    entity_name: str,
    operations: tuple[str, ...],
) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for operation in operations:
        for criteria in security.row_criteria(entity_name, operation):
            for path in _expression_paths(criteria):
                current = model.entity(entity_name)
                for part in path:
                    field = current.fields.get(part)
                    if field is None:
                        break
                    if field.metadata["type"] == "collection":
                        edges.add((current.name, field.name))
                    if field.target_entity is None:
                        break
                    current = model.entity(field.target_entity)
    return edges


def _attribute_parts(node: ast.Attribute) -> tuple[str, ...]:
    parts: list[str] = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if not isinstance(value, ast.Name):
        return ()
    parts.append(value.id)
    return tuple(reversed(parts))


def _version_field(entity: NormalizedEntity) -> str | None:
    return next(
        (name for name, field in entity.fields.items() if field.metadata.get("concurrency_token")),
        None,
    )


def _coerce_scalar(field_type: str, value: Any) -> tuple[Any, bool]:
    if field_type == "decimal":
        decimal_value = _as_decimal(value)
        return (decimal_value, True) if decimal_value is not None else (value, False)
    if field_type == "integer":
        return value, isinstance(value, int) and not isinstance(value, bool)
    if field_type in {"string", "choice"}:
        return value, isinstance(value, str)
    if field_type == "boolean":
        return value, isinstance(value, bool)
    if field_type == "date":
        return value, isinstance(value, date) and not isinstance(value, datetime)
    if field_type == "datetime":
        return value, isinstance(value, datetime)
    return value, True


def _decimal_shape_issues(
    field_name: str,
    value: Decimal,
    metadata: Mapping[str, Any],
) -> list[ValidationIssue]:
    digits, exponent = value.as_tuple().digits, value.as_tuple().exponent
    if not isinstance(exponent, int):
        return [
            ValidationIssue(
                "decimal",
                f"{field_name} must be a finite decimal value",
                (field_name,),
            )
        ]
    issues: list[ValidationIssue] = []
    fractional_digits = max(-exponent, 0)
    scale = metadata.get("scale")
    if scale is not None and fractional_digits > int(scale):
        issues.append(
            ValidationIssue(
                "scale",
                f"{field_name} allows at most {scale} decimal places",
                (field_name,),
            )
        )
    precision = metadata.get("precision")
    if precision is not None:
        integer_digits = 0 if value.is_zero() else max(len(digits) + exponent, 0)
        allowed_integer_digits = int(precision) - int(scale or 0)
        if integer_digits > allowed_integer_digits:
            issues.append(
                ValidationIssue(
                    "precision",
                    f"{field_name} allows at most {allowed_integer_digits} "
                    "integer digits",
                    (field_name,),
                )
            )
    return issues


def _normalize_filter(
    model: ApplicationModel,
    entity: NormalizedEntity,
    condition: FilterCondition,
) -> FilterCondition:
    field = entity.fields[condition.field]
    operator = condition.operator
    allowed = {"eq", "ne", "lt", "lte", "gt", "gte", "contains", "icontains"}
    if operator not in allowed:
        raise ValueError(f"unsupported filter operator {operator!r}")
    field_type = field.metadata["type"]
    if operator in {"contains", "icontains"}:
        if field_type not in {"string", "choice"} or not isinstance(
            condition.value, str
        ):
            raise ValueError(
                f"{operator} filters require a string field and value"
            )
        return condition
    if condition.value is None:
        if operator not in {"eq", "ne"}:
            raise ValueError("null supports only eq and ne filters")
        return condition
    if field_type == "reference" and field.target_entity:
        target = model.entity(field.target_entity)
        field_type = target.field(_primary_key(target)).metadata["type"]
    coerced, valid = _coerce_scalar(field_type, condition.value)
    if not valid:
        raise ValueError(
            f"filter value for {condition.field!r} must be a {field_type} value"
        )
    if field_type == "boolean" and operator not in {"eq", "ne"}:
        raise ValueError("boolean fields support only eq and ne filters")
    return FilterCondition(condition.field, operator, coerced)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, int):
        candidate = Decimal(value)
    elif isinstance(value, float):
        candidate = Decimal(str(value))
    elif isinstance(value, str):
        try:
            candidate = Decimal(value.strip())
        except InvalidOperation:
            return None
    else:
        return None
    return candidate if candidate.is_finite() else None
