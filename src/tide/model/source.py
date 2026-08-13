"""Strict v0.1 authoring models.

These models describe source files. The compiler resolves them into the smaller,
immutable runtime model in :mod:`tide.compiler.normalized`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, Union, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApplicationSource(SourceModel):
    name: str
    version: str


class PathSetSource(SourceModel):
    paths: tuple[str, ...]


class PresentationPathsSource(SourceModel):
    defaults: str | None = None
    formats: str | None = None
    presets: tuple[str, ...] = ()


class DatabaseSource(SourceModel):
    mode: Literal["managed", "legacy"] = "managed"


class ProjectSource(SourceModel):
    schema_version: Literal["0.1"]
    application: ApplicationSource
    database: DatabaseSource = Field(default_factory=DatabaseSource)
    model: PathSetSource
    views: PathSetSource = Field(default_factory=lambda: PathSetSource(paths=()))
    presentation: PresentationPathsSource = Field(default_factory=PresentationPathsSource)
    reports: PathSetSource = Field(default_factory=lambda: PathSetSource(paths=()))
    security: PathSetSource = Field(default_factory=lambda: PathSetSource(paths=()))


class RestExposureSource(SourceModel):
    path: str | None = None
    operations: tuple[Literal["list", "get", "create", "update", "delete"], ...] = ()


class McpExposureSource(SourceModel):
    resources: tuple[Literal["schema", "record", "audit"], ...] = ()
    tools: tuple[Literal["search", "create", "update", "delete"], ...] = ()

    @model_validator(mode="after")
    def unique_capabilities(self) -> McpExposureSource:
        if len(set(self.resources)) != len(self.resources):
            raise ValueError("MCP resources must not be repeated")
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("MCP tools must not be repeated")
        return self


class EntityExposureSource(SourceModel):
    tui: bool = False
    rest: RestExposureSource | bool = False
    mcp: McpExposureSource | bool = False


class EntityPermissionsSource(SourceModel):
    list_: str | None = Field(default=None, alias="list")
    read: str | None = None
    create: str | None = None
    update: str | None = None
    delete: str | None = None
    audit: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ComputedSource(SourceModel):
    expression: str
    materialization: Literal["virtual", "stored", "database"] = "virtual"


FieldType = Literal[
    "string",
    "integer",
    "decimal",
    "boolean",
    "date",
    "datetime",
    "uuid",
    "choice",
    "reference",
    "collection",
]

# Every type above that holds one value of its own. `reference` borrows the
# type of the primary key it points at and `collection` is navigation, so both
# are excluded -- and every layer that dispatches on a field type should be
# reachable from this tuple rather than from a list written out again.
SCALAR_FIELD_TYPES: tuple[str, ...] = tuple(
    field_type
    for field_type in get_args(FieldType)
    if field_type not in {"reference", "collection"}
)


class SelectionAssignmentSource(SourceModel):
    source: str = Field(alias="from", min_length=1)
    overwrite: Literal["always", "when_blank"] = "always"


class SelectionSource(SourceModel):
    assign: dict[str, SelectionAssignmentSource]


class EditMaskSource(SourceModel):
    regex: str = Field(min_length=1)


class FieldSource(SourceModel):
    type: FieldType
    label: str | None = None
    help: str | None = None
    primary_key: bool = False
    required: bool = False
    unique: bool = False
    readonly: bool = False
    searchable: bool = False
    concurrency_token: bool = False
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    default: Any = None
    default_factory: Literal["today"] | None = None
    server_default: Any = None
    format: str | None = None
    edit_mask: str | EditMaskSource | None = None
    choices: tuple[str, ...] = ()
    target: str | None = None
    column: str | None = Field(default=None, min_length=1)
    storage: str | None = None
    migration_id: str | None = Field(default=None, min_length=3)
    renamed_from: str | None = Field(default=None, min_length=1)
    inverse: str | None = None
    on_delete: Literal["restrict", "cascade", "set_null"] | None = None
    lookup_view: str | None = None
    on_select: SelectionSource | None = None
    order_by: str | None = None
    cascade: tuple[Literal["create", "update", "delete"], ...] = ()
    orphan_delete: bool = False
    computed: ComputedSource | None = None
    write: Literal["normal", "action_only", "system"] = "normal"
    immutable_when: str | None = None
    generated_by: str | None = None
    audit: Literal["none", "changes", "values"] = "changes"

    @field_validator("length", "precision", "scale")
    @classmethod
    def non_negative_dimensions(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("must not be negative")
        return value


class ValidationSource(SourceModel):
    id: str
    assert_: str | None = Field(default=None, alias="assert")
    when: str | None = None
    handler: str | None = None
    message: str
    fields: tuple[str, ...] = ()
    run: tuple[str, ...] = ()
    severity: Literal["error", "warning", "info"] = "error"

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


RESERVED_ACTION_NAMES = frozenset({"cancel", "save"})
"""Names the form action bar already uses, so an entity may not reuse them.

A view's `actions:` list mixes both kinds, and every renderer resolves these two
as the built-in discard and commit. A domain action sharing a name is therefore
filtered out as though it were the built-in and never reaches a form -- silently,
because the action is still exposed over REST and MCP, so only the screen is
missing it.
"""


class ActionExposureSource(SourceModel):
    rest: bool = False
    mcp: bool = False


class TransitionSource(SourceModel):
    """The one place an action's effect on a state field is declared.

    `from`/`to` name states of a `choice` field the workflow owns, and the
    compiler derives the action's state guard from `from`. `locks_record`
    says that arriving at `to` freezes the ordinarily writable fields, which
    is what four copies of `immutable_when: "status != 'draft'"` used to say
    by hand. `stamp` names the fields the handler records the transition in;
    the compiler checks them rather than writing them, so the handler stays
    the only thing that mutates a record.
    """

    field: str = Field(min_length=1)
    from_: tuple[str, ...] = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    locks_record: bool = False
    stamp: dict[str, Literal["now", "principal"]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @field_validator(
        "from_",
        mode="before",
        json_schema_input_type=Union[str, tuple[str, ...]],
    )
    @classmethod
    def one_or_more_states(cls, value: Any) -> Any:
        """`from: draft` and `from: [draft, held]` both name a set of states.

        A list matters because the generator's transition operation already
        carried several, and flattened them into `status in [...]` -- which the
        expression language does not accept, so that branch could only ever
        produce a model that refused to compile.

        `json_schema_input_type` is what tells `tide model schema` that the
        scalar form is accepted. Without it the export describes the validated
        type -- a list -- and an editor pointed at the schema rejects every
        `from: draft` in this repository, which is how both applications write
        it.
        """

        return (value,) if isinstance(value, str) else value


class ActionSource(SourceModel):
    label: str
    shortcut: str | None = None
    enabled_when: str | None = None
    visible_when: str | None = None
    permission: str | None = None
    unrestricted: bool = False
    execute: str
    expose: ActionExposureSource = Field(default_factory=ActionExposureSource)
    idempotent: bool = False
    audit: bool = True
    transition: TransitionSource | None = None


class FilterSource(SourceModel):
    label: str
    criteria: str


class PreviousTableSource(SourceModel):
    table: str = Field(min_length=1)
    schema_: str | None = Field(default=None, alias="schema", min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class EntityStorageSource(SourceModel):
    table: str | None = Field(default=None, min_length=1)
    schema_: str | None = Field(default=None, alias="schema", min_length=1)
    migration_id: str | None = Field(default=None, min_length=3)
    renamed_from: PreviousTableSource | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class EntitySource(SourceModel):
    entity: str
    label: str | None = None
    record_label: str | None = None
    display: str | None = None
    storage: EntityStorageSource | None = None
    search_fields: tuple[str, ...] = ()
    expose: EntityExposureSource = Field(default_factory=EntityExposureSource)
    permissions: EntityPermissionsSource = Field(default_factory=EntityPermissionsSource)
    presentation: dict[Literal["browse", "form", "lookup", "inline_edit"], dict[str, Any]] = Field(default_factory=dict)
    fields: dict[str, FieldSource]
    validations: tuple[ValidationSource, ...] = ()
    actions: dict[str, ActionSource] = Field(default_factory=dict)
    filters: dict[str, FilterSource] = Field(default_factory=dict)


class ViewSource(SourceModel):
    view: str
    entity: str | None = None
    kind: Literal["browse", "form", "lookup", "inline_edit"] | None = None
    base: str | None = None
    mode: Literal["overlay", "replace"] = "overlay"
    extends: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    fields: dict[str, dict[str, Any]] = Field(default_factory=dict)
    columns: tuple[str, ...] = ()
    search: tuple[str, ...] = ()
    filters: dict[str, FilterSource] = Field(default_factory=dict)
    layout: tuple[Any, ...] = ()
    actions: tuple[str, ...] = ()
    surfaces: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PresetSource(SourceModel):
    kind: Literal["browse", "form", "lookup", "inline_edit"]
    settings: dict[str, Any] = Field(default_factory=dict)


class PresetDocumentSource(SourceModel):
    presets: dict[str, PresetSource]


class ParameterSource(SourceModel):
    type: Literal["string", "integer", "decimal", "boolean", "date", "datetime"]
    required: bool = False
    default: Any = None


class QuerySource(SourceModel):
    criteria: str | None = None
    sort: tuple[str, ...] = ()


class ReportContentSource(SourceModel):
    text: str | None = None
    field: str | None = None
    expression: str | None = None
    label: str | None = None
    format: str | None = None
    style: str | None = None

    @model_validator(mode="after")
    def exactly_one_value_source(self) -> ReportContentSource:
        sources = (self.text, self.field, self.expression)
        if sum(value is not None for value in sources) != 1:
            raise ValueError("report content requires exactly one of text, field, or expression")
        return self


class ReportDetailSource(SourceModel):
    source: str = Field(min_length=1)
    columns: tuple[str, ...] = Field(min_length=1)


class ReportBandsSource(SourceModel):
    report_header: tuple[ReportContentSource, ...] = ()
    record_header: tuple[ReportContentSource, ...] = ()
    detail: ReportDetailSource
    report_footer: tuple[ReportContentSource, ...] = ()
    page_footer: tuple[ReportContentSource, ...] = ()


class ReportGroupSource(SourceModel):
    field: str = Field(min_length=1)
    label: str | None = None
    format: str | None = None


class ReportAggregateSource(SourceModel):
    name: str = Field(min_length=1)
    function: Literal["count", "sum"]
    field: str | None = None
    label: str | None = None
    format: str | None = None

    @model_validator(mode="after")
    def valid_aggregate_field(self) -> ReportAggregateSource:
        if self.function == "sum" and self.field is None:
            raise ValueError("sum aggregates require a field")
        if self.function == "count" and self.field is not None:
            raise ValueError("count aggregates do not accept a field")
        return self


class ReportExposureSource(SourceModel):
    rest: bool = False
    mcp: bool = False


class ReportSource(SourceModel):
    report: str
    title: str
    entity: str
    kind: Literal["record", "summary"] = "record"
    permission: str | None = None
    unrestricted: bool = False
    expose: ReportExposureSource = Field(default_factory=ReportExposureSource)
    parameters: dict[str, ParameterSource] = Field(default_factory=dict)
    query: QuerySource = Field(default_factory=QuerySource)
    bands: ReportBandsSource | None = None
    group_by: tuple[ReportGroupSource, ...] = ()
    aggregates: tuple[ReportAggregateSource, ...] = ()
    row_limit: int | None = Field(default=None, ge=1, le=500)

    @model_validator(mode="after")
    def valid_report_shape(self) -> ReportSource:
        if self.permission is not None and self.unrestricted:
            raise ValueError("report cannot declare both permission and unrestricted access")
        if self.kind == "record":
            if self.bands is None:
                raise ValueError("record reports require bands")
            if self.group_by or self.aggregates or self.row_limit is not None:
                raise ValueError("record reports do not accept summary fields")
        else:
            if self.bands is not None:
                raise ValueError("summary reports do not accept bands")
            if not self.aggregates:
                raise ValueError("summary reports require at least one aggregate")
        return self


class NavigationItemSource(SourceModel):
    view: str
    label: str | None = None


class NavigationGroupSource(SourceModel):
    label: str
    items: tuple[NavigationItemSource, ...]


class PresentationDefaultsSource(SourceModel):
    browse: dict[str, Any] = Field(default_factory=dict)
    form: dict[str, Any] = Field(default_factory=dict)
    lookup: dict[str, Any] = Field(default_factory=dict)
    inline_edit: dict[str, Any] = Field(default_factory=dict)
    navigation: tuple[NavigationGroupSource, ...] = ()


class FormatsSource(SourceModel):
    formats: dict[str, dict[str, Any]]


class RoleSource(SourceModel):
    grants: tuple[str, ...] = ()


class RowPolicySource(SourceModel):
    id: str
    entity: str
    operations: tuple[Literal["list", "read", "create", "update", "delete"], ...]
    criteria: str


class FieldPolicySource(SourceModel):
    entity: str
    field: str
    read: str | None = None
    write: str | None = None


class SecurityDocumentSource(SourceModel):
    permissions: tuple[str, ...] = ()
    roles: dict[str, RoleSource] = Field(default_factory=dict)
    row_policies: tuple[RowPolicySource, ...] = ()
    field_policies: tuple[FieldPolicySource, ...] = ()
