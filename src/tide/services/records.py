"""Secured, UI-independent record and query services."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Callable, Mapping

from tide.compiler.expressions import evaluate_expression
from tide.compiler.normalized import ApplicationModel, NormalizedEntity
from tide.data.repository import Repository
from tide.runtime.context import RequestContext
from tide.runtime.errors import (
    AuthorizationError,
    ImmutableFieldError,
    ValidationFailed,
    ValidationIssue,
)
from tide.security.engine import PROTECTED, SecurityEngine
from tide.sessions.record_session import RecordSession


class MutationSource(StrEnum):
    USER = "user"
    ACTION = "action"
    SYSTEM = "system"


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


Generator = Callable[[dict[str, Any], RequestContext, Repository], Any]


class RecordsService:
    def __init__(
        self,
        model: ApplicationModel,
        repository: Repository,
        security: SecurityEngine | None = None,
    ) -> None:
        self.model = model
        self.repository = repository
        self.security = security or SecurityEngine(model)
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
        values = self.repository.get(entity_name, identity)
        self.security.require_row(entity_name, "read", values, context)
        self.security.require_row(entity_name, "update", values, context)
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
        values = self.repository.get(entity_name, identity)
        self.security.require_row(entity_name, "read", values, context)
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
        values = self.repository.get(entity_name, identity)
        self.security.require_row(entity_name, "read", values, context)
        return self._project(entity, values, context)

    def query(
        self,
        entity_name: str,
        query: QuerySpec,
        context: RequestContext,
    ) -> list[dict[str, Any]]:
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "list", context)
        if query.limit < 1 or query.limit > 500:
            raise ValueError("query limit must be between 1 and 500")
        for field_name in [condition.field for condition in query.filters] + [sort.field for sort in query.sort]:
            if field_name not in entity.fields:
                raise ValueError(f"unknown query field {field_name!r}")
            if not self.security.can_read_field(entity_name, field_name, context):
                raise AuthorizationError(f"field {field_name!r} cannot be used for filtering or sorting")
        records = [
            record
            for record in self.repository.all(entity_name)
            if self.security.row_allowed(entity_name, "list", record, context)
        ]
        for condition in query.filters:
            records = [record for record in records if _matches(record.get(condition.field), condition)]
        primary_key = _primary_key(entity)
        sort_fields = list(query.sort)
        if not any(sort.field == primary_key for sort in sort_fields):
            sort_fields.append(SortField(primary_key))
        for sort in reversed(sort_fields):
            records.sort(key=lambda record: _sort_key(record.get(sort.field)), reverse=sort.descending)
        return [self._project(entity, record, context) for record in records[: query.limit]]

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
            self.security.require_row(entity.name, "read", session.original, context)
        elif not session.is_new:
            self.security.require_row(entity.name, "update", session.original, context)
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
        self._validate_uniqueness(entity, values, session.identity)
        stored = self.repository.write(
            entity.name,
            values,
            primary_key=_primary_key(entity),
            version_field=_version_field(entity),
            expected_version=session.expected_version,
            is_new=session.is_new,
        )
        session.identity = stored[_primary_key(entity)]
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

    def _project(self, entity: NormalizedEntity, source: Mapping[str, Any], context: RequestContext) -> dict[str, Any]:
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
                result[field_name] = [self._project(target, item, context) for item in value or []]
            else:
                result[field_name] = deepcopy(value)
        return result


def _primary_key(entity: NormalizedEntity) -> str:
    return next(name for name, field in entity.fields.items() if field.metadata.get("primary_key"))


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


def _matches(value: Any, condition: FilterCondition) -> bool:
    operations = {
        "eq": lambda: value == condition.value,
        "ne": lambda: value != condition.value,
        "lt": lambda: value < condition.value,
        "lte": lambda: value <= condition.value,
        "gt": lambda: value > condition.value,
        "gte": lambda: value >= condition.value,
        "contains": lambda: condition.value in value,
    }
    if condition.operator not in operations:
        raise ValueError(f"unsupported filter operator {condition.operator!r}")
    return bool(operations[condition.operator]())


def _sort_key(value: Any) -> tuple[bool, Any]:
    return value is None, value
