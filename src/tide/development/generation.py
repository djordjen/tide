"""Structured, no-write application-generation proposals for developer tools."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
import json
import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tide.model.source import FieldType


QUALIFIED_IDENTIFIER = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$"
)
SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
APPLICATION_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class GenerationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlannedSequenceNumber(GenerationModel):
    """A constrained framework-owned sequence-number template."""

    prefix: str = Field(default="", max_length=24)
    width: int = Field(default=6, ge=1, le=18)
    separator: Literal["-", "/", ""] = "-"
    date_field: str | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> PlannedSequenceNumber:
        if self.date_field is not None and not SIMPLE_IDENTIFIER.fullmatch(
            self.date_field
        ):
            raise ValueError("sequence date_field must be a simple identifier")
        if any(ord(character) < 32 for character in self.prefix):
            raise ValueError("sequence prefix must not contain control characters")
        return self


class PlannedField(GenerationModel):
    """A logical field definition without source paths or executable code."""

    name: str = Field(min_length=1)
    type: FieldType
    label: str | None = None
    required: bool = False
    primary_key: bool = False
    unique: bool = False
    readonly: bool = False
    length: int | None = Field(default=None, ge=1)
    precision: int | None = Field(default=None, ge=1)
    scale: int | None = Field(default=None, ge=0)
    choices: tuple[str, ...] = ()
    target: str | None = None
    inverse: str | None = None
    on_delete: Literal["restrict", "cascade", "set_null"] | None = None
    cascade: tuple[Literal["create", "update", "delete"], ...] = ()
    orphan_delete: bool = False
    default: Any = None
    default_factory: Literal["today"] | None = None
    computed_expression: str | None = Field(default=None, min_length=1)
    computed_materialization: Literal["virtual", "stored"] = "stored"
    sequence: PlannedSequenceNumber | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> PlannedField:
        if not SIMPLE_IDENTIFIER.fullmatch(self.name):
            raise ValueError("field name must be a simple identifier")
        relationship = self.type in {"reference", "collection"}
        if relationship != (self.target is not None):
            raise ValueError(
                "reference and collection fields require exactly one target"
            )
        if self.target is not None and not QUALIFIED_IDENTIFIER.fullmatch(self.target):
            raise ValueError("field target must be a qualified entity identifier")
        if self.primary_key and relationship:
            raise ValueError("relationship fields cannot be primary keys")
        if self.type == "choice" and not self.choices:
            raise ValueError("choice fields require at least one choice")
        if self.type != "choice" and self.choices:
            raise ValueError("choices may be declared only for choice fields")
        if self.scale is not None and self.precision is None:
            raise ValueError("field scale requires precision")
        if self.precision is not None and self.scale is not None:
            if self.scale > self.precision:
                raise ValueError("field scale cannot exceed precision")
        if self.type != "collection" and (self.cascade or self.orphan_delete):
            raise ValueError("cascade and orphan_delete require a collection field")
        if self.type != "reference" and self.on_delete is not None:
            raise ValueError("on_delete requires a reference field")
        value_sources = sum(
            value is not None
            for value in (
                self.default_factory,
                self.computed_expression,
                self.sequence,
            )
        ) + int(self.default is not None)
        if value_sources > 1:
            raise ValueError(
                "default, default_factory, computed_expression, and sequence "
                "are mutually exclusive"
            )
        if self.default_factory == "today" and self.type != "date":
            raise ValueError("today default_factory requires a date field")
        if self.computed_expression is not None and not self.readonly:
            raise ValueError("computed fields must be readonly")
        if self.sequence is not None and (self.type != "string" or not self.readonly):
            raise ValueError("sequence fields must be readonly strings")
        return self


class CreateApplicationOperation(GenerationModel):
    operation: Literal["create_application"] = "create_application"
    application_id: str
    name: str = Field(min_length=1)
    version: str = "0.1.0"
    database_mode: Literal["managed", "legacy"] = "managed"

    @model_validator(mode="after")
    def valid_application_id(self) -> CreateApplicationOperation:
        if not APPLICATION_ID.fullmatch(self.application_id):
            raise ValueError(
                "application_id must be lowercase kebab-case and 2-63 characters"
            )
        return self


class DefineEntityOperation(GenerationModel):
    operation: Literal["define_entity"] = "define_entity"
    entity: str
    label: str | None = None
    display: str | None = None
    fields: tuple[PlannedField, ...] = Field(min_length=1, max_length=100)
    list_permission: str | None = None
    read_permission: str | None = None
    create_permission: str | None = None
    update_permission: str | None = None
    expose_tui: bool = True
    expose_rest: tuple[
        Literal["list", "get", "create", "update", "delete"], ...
    ] = ()
    expose_mcp: tuple[Literal["schema", "record", "search"], ...] = ()

    @model_validator(mode="after")
    def valid_entity(self) -> DefineEntityOperation:
        if not QUALIFIED_IDENTIFIER.fullmatch(self.entity):
            raise ValueError("entity must be a qualified identifier")
        names = [field.name for field in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("entity fields must not be repeated")
        if sum(field.primary_key for field in self.fields) != 1:
            raise ValueError("entity requires exactly one primary-key field")
        permissions = (
            self.list_permission,
            self.read_permission,
            self.create_permission,
            self.update_permission,
        )
        if any(
            permission is not None and not QUALIFIED_IDENTIFIER.fullmatch(permission)
            for permission in permissions
        ):
            raise ValueError("entity permissions must be qualified identifiers")
        if len(set(self.expose_rest)) != len(self.expose_rest):
            raise ValueError("REST operations must not be repeated")
        if len(set(self.expose_mcp)) != len(self.expose_mcp):
            raise ValueError("MCP capabilities must not be repeated")
        return self


class DefineRoleOperation(GenerationModel):
    operation: Literal["define_role"] = "define_role"
    role: str
    grants: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_role(self) -> DefineRoleOperation:
        if not SIMPLE_IDENTIFIER.fullmatch(self.role):
            raise ValueError("role must be a simple identifier")
        if len(set(self.grants)) != len(self.grants):
            raise ValueError("role grants must not be repeated")
        if any(not QUALIFIED_IDENTIFIER.fullmatch(item) for item in self.grants):
            raise ValueError("role grants must be qualified permission identifiers")
        return self


class DefineStateTransitionOperation(GenerationModel):
    """A safe built-in workflow template, not arbitrary generated Python."""

    operation: Literal["define_state_transition"] = "define_state_transition"
    entity: str
    action: str
    label: str
    state_field: str
    from_values: tuple[str, ...] = Field(min_length=1)
    to_value: str
    permission: str
    requires_collection: str | None = None
    stamp_datetime_field: str | None = None
    stamp_principal_field: str | None = None
    expose_rest: bool = False
    expose_mcp: bool = False
    idempotent: bool = True

    @model_validator(mode="after")
    def valid_transition(self) -> DefineStateTransitionOperation:
        if not QUALIFIED_IDENTIFIER.fullmatch(self.entity):
            raise ValueError("transition entity must be qualified")
        for value, label in (
            (self.action, "action"),
            (self.state_field, "state_field"),
        ):
            if not SIMPLE_IDENTIFIER.fullmatch(value):
                raise ValueError(f"{label} must be a simple identifier")
        for optional in (
            self.requires_collection,
            self.stamp_datetime_field,
            self.stamp_principal_field,
        ):
            if optional is not None and not SIMPLE_IDENTIFIER.fullmatch(optional):
                raise ValueError("transition field names must be simple identifiers")
        if not QUALIFIED_IDENTIFIER.fullmatch(self.permission):
            raise ValueError("transition permission must be qualified")
        if len(set(self.from_values)) != len(self.from_values):
            raise ValueError("transition source values must not be repeated")
        if self.to_value in self.from_values:
            raise ValueError("transition target must differ from source values")
        return self


class DefineRecordReportOperation(GenerationModel):
    operation: Literal["define_record_report"] = "define_record_report"
    report: str
    title: str
    entity: str
    permission: str
    header_fields: tuple[str, ...] = Field(min_length=1)
    detail_collection: str
    detail_columns: tuple[str, ...] = Field(min_length=1)
    footer_fields: tuple[str, ...] = ()
    expose_rest: bool = True
    expose_mcp: bool = False
    pdf_enabled: bool = True

    @model_validator(mode="after")
    def valid_report(self) -> DefineRecordReportOperation:
        if not QUALIFIED_IDENTIFIER.fullmatch(self.report):
            raise ValueError("report must be a qualified identifier")
        if not QUALIFIED_IDENTIFIER.fullmatch(self.entity):
            raise ValueError("report entity must be qualified")
        if not QUALIFIED_IDENTIFIER.fullmatch(self.permission):
            raise ValueError("report permission must be qualified")
        field_names = (
            *self.header_fields,
            self.detail_collection,
            *self.detail_columns,
            *self.footer_fields,
        )
        if any(not SIMPLE_IDENTIFIER.fullmatch(item) for item in field_names):
            raise ValueError("report field names must be simple identifiers")
        return self


GenerationOperation = Annotated[
    Union[
        CreateApplicationOperation,
        DefineEntityOperation,
        DefineRoleOperation,
        DefineStateTransitionOperation,
        DefineRecordReportOperation,
    ],
    Field(discriminator="operation"),
]


class ApplicationGenerationPlan(GenerationModel):
    """An ordered logical change set proposed by an AI or designer."""

    operations: tuple[GenerationOperation, ...] = Field(min_length=1, max_length=200)


class GenerationIssue(GenerationModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    operation_index: int | None = None
    path: tuple[str | int, ...] = ()


class ApplicationGenerationProposal(GenerationModel):
    """A deterministic no-write proposal requiring explicit later approval."""

    proposal_id: str
    valid: bool
    approval_required: Literal[True] = True
    writes_performed: Literal[False] = False
    application_id: str | None = None
    summary: str
    permissions: tuple[str, ...] = ()
    operations: tuple[GenerationOperation, ...]
    issues: tuple[GenerationIssue, ...] = ()


class ApplicationGenerationService:
    """Validate structured application intent without touching the filesystem."""

    def propose(
        self,
        plan: ApplicationGenerationPlan,
    ) -> ApplicationGenerationProposal:
        issues: list[GenerationIssue] = []
        operations = plan.operations
        create_indexes = [
            index
            for index, operation in enumerate(operations)
            if isinstance(operation, CreateApplicationOperation)
        ]
        if create_indexes != [0]:
            issues.append(
                _error(
                    "TIDEGEN001",
                    "a new-application plan must begin with exactly one "
                    "create_application operation",
                )
            )
        entities = _unique_operations(
            operations,
            DefineEntityOperation,
            "entity",
            "TIDEGEN002",
            issues,
        )
        roles = _unique_operations(
            operations,
            DefineRoleOperation,
            "role",
            "TIDEGEN003",
            issues,
        )
        reports = _unique_operations(
            operations,
            DefineRecordReportOperation,
            "report",
            "TIDEGEN004",
            issues,
        )
        transitions: dict[tuple[str, str], DefineStateTransitionOperation] = {}
        for index, operation in enumerate(operations):
            if not isinstance(operation, DefineStateTransitionOperation):
                continue
            key = operation.entity, operation.action
            if key in transitions:
                issues.append(
                    _error(
                        "TIDEGEN005",
                        f"duplicate state transition {operation.entity}.{operation.action}",
                        index,
                    )
                )
            transitions[key] = operation

        for index, operation in enumerate(operations):
            if isinstance(operation, DefineEntityOperation):
                _validate_entity_references(operation, index, entities, issues)
                _validate_field_templates(operation, index, issues)
            elif isinstance(operation, DefineStateTransitionOperation):
                _validate_transition(operation, index, entities, issues)
            elif isinstance(operation, DefineRecordReportOperation):
                _validate_report(operation, index, entities, issues)

        permissions = _declared_permissions(
            entities.values(), transitions.values(), reports.values()
        )
        for index, operation in enumerate(operations):
            if not isinstance(operation, DefineRoleOperation):
                continue
            for grant_index, grant in enumerate(operation.grants):
                if grant not in permissions:
                    issues.append(
                        _error(
                            "TIDEGEN006",
                            f"role {operation.role!r} grants undeclared permission {grant!r}",
                            index,
                            ("grants", grant_index),
                        )
                    )
        granted = {grant for role in roles.values() for grant in role.grants}
        for permission in sorted(permissions - granted):
            issues.append(
                GenerationIssue(
                    severity="warning",
                    code="TIDEGEN101",
                    message=f"declared permission {permission!r} is not granted to any role",
                )
            )
        if not reports:
            issues.append(
                GenerationIssue(
                    severity="warning",
                    code="TIDEGEN102",
                    message="the proposal defines no printable record report",
                )
            )

        canonical = json.dumps(
            plan.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        proposal_id = "tide-plan-" + sha256(canonical.encode("utf-8")).hexdigest()[:24]
        application = next(
            (
                operation
                for operation in operations
                if isinstance(operation, CreateApplicationOperation)
            ),
            None,
        )
        error_count = sum(issue.severity == "error" for issue in issues)
        return ApplicationGenerationProposal(
            proposal_id=proposal_id,
            valid=error_count == 0,
            application_id=application.application_id if application else None,
            summary=(
                f"{len(entities)} entities, {len(transitions)} workflows, "
                f"{len(reports)} reports, and {len(roles)} roles; "
                f"{error_count} semantic errors"
            ),
            permissions=tuple(sorted(permissions)),
            operations=operations,
            issues=tuple(issues),
        )


def _unique_operations(
    operations: tuple[GenerationOperation, ...],
    expected_type: type[Any],
    attribute: str,
    code: str,
    issues: list[GenerationIssue],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for index, operation in enumerate(operations):
        if not isinstance(operation, expected_type):
            continue
        key = str(getattr(operation, attribute))
        if key in result:
            issues.append(_error(code, f"duplicate {attribute} {key!r}", index))
        result[key] = operation
    return result


def _validate_entity_references(
    entity: DefineEntityOperation,
    index: int,
    entities: dict[str, DefineEntityOperation],
    issues: list[GenerationIssue],
) -> None:
    for field_index, field in enumerate(entity.fields):
        if field.target is None:
            continue
        target = entities.get(field.target)
        if target is None:
            issues.append(
                _error(
                    "TIDEGEN007",
                    f"field {entity.entity}.{field.name} targets unknown entity {field.target!r}",
                    index,
                    ("fields", field_index, "target"),
                )
            )
            continue
        if field.inverse is not None:
            inverse = next(
                (item for item in target.fields if item.name == field.inverse),
                None,
            )
            if inverse is None or inverse.target != entity.entity:
                issues.append(
                    _error(
                        "TIDEGEN008",
                        f"inverse {field.target}.{field.inverse} does not point to "
                        f"{entity.entity}",
                        index,
                        ("fields", field_index, "inverse"),
                    )
                )


def _validate_field_templates(
    entity: DefineEntityOperation,
    index: int,
    issues: list[GenerationIssue],
) -> None:
    fields = {field.name: field for field in entity.fields}
    for field_index, field in enumerate(entity.fields):
        if field.sequence is None or field.sequence.date_field is None:
            continue
        date_field = fields.get(field.sequence.date_field)
        if date_field is None or date_field.type != "date":
            issues.append(
                _error(
                    "TIDEGEN017",
                    f"sequence date_field {field.sequence.date_field!r} must be a "
                    "date field on the same entity",
                    index,
                    ("fields", field_index, "sequence", "date_field"),
                )
            )


def _validate_transition(
    transition: DefineStateTransitionOperation,
    index: int,
    entities: dict[str, DefineEntityOperation],
    issues: list[GenerationIssue],
) -> None:
    entity = entities.get(transition.entity)
    if entity is None:
        issues.append(
            _error(
                "TIDEGEN009",
                f"transition targets unknown entity {transition.entity!r}",
                index,
            )
        )
        return
    fields = {field.name: field for field in entity.fields}
    state = fields.get(transition.state_field)
    if state is None or state.type != "choice":
        issues.append(
            _error(
                "TIDEGEN010",
                "state transitions require a choice state_field",
                index,
                ("state_field",),
            )
        )
    elif any(
        value not in state.choices
        for value in (*transition.from_values, transition.to_value)
    ):
        issues.append(
            _error(
                "TIDEGEN011",
                "transition values must be declared choices of the state field",
                index,
            )
        )
    expected_types = (
        (transition.requires_collection, "collection"),
        (transition.stamp_datetime_field, "datetime"),
        (transition.stamp_principal_field, "string"),
    )
    for name, expected in expected_types:
        if name is not None and (name not in fields or fields[name].type != expected):
            issues.append(
                _error(
                    "TIDEGEN012",
                    f"transition field {name!r} must be a {expected} field",
                    index,
                )
            )


def _validate_report(
    report: DefineRecordReportOperation,
    index: int,
    entities: dict[str, DefineEntityOperation],
    issues: list[GenerationIssue],
) -> None:
    entity = entities.get(report.entity)
    if entity is None:
        issues.append(
            _error(
                "TIDEGEN013", f"report targets unknown entity {report.entity!r}", index
            )
        )
        return
    fields = {field.name: field for field in entity.fields}
    for name in (*report.header_fields, *report.footer_fields):
        if name not in fields or fields[name].type == "collection":
            issues.append(
                _error(
                    "TIDEGEN014",
                    f"report root field {name!r} is missing or is a collection",
                    index,
                )
            )
    detail = fields.get(report.detail_collection)
    if detail is None or detail.type != "collection" or detail.target not in entities:
        issues.append(
            _error(
                "TIDEGEN015",
                "report detail_collection must target a proposed collection entity",
                index,
            )
        )
        return
    target_fields = {field.name for field in entities[detail.target].fields}
    for name in report.detail_columns:
        if name not in target_fields:
            issues.append(
                _error(
                    "TIDEGEN016",
                    f"report detail column {name!r} is not present on {detail.target}",
                    index,
                )
            )


def _declared_permissions(
    entities: Iterable[DefineEntityOperation],
    transitions: Iterable[DefineStateTransitionOperation],
    reports: Iterable[DefineRecordReportOperation],
) -> set[str]:
    permissions = {
        permission
        for entity in entities
        for permission in (
            entity.list_permission,
            entity.read_permission,
            entity.create_permission,
            entity.update_permission,
        )
        if permission is not None
    }
    permissions.update(item.permission for item in transitions)
    permissions.update(item.permission for item in reports)
    return permissions


def _error(
    code: str,
    message: str,
    operation_index: int | None = None,
    path: tuple[str | int, ...] = (),
) -> GenerationIssue:
    return GenerationIssue(
        severity="error",
        code=code,
        message=message,
        operation_index=operation_index,
        path=path,
    )
