"""Shared wire contracts for TIDE HTTP servers and clients."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TIDE_WIRE_VERSION = "0.1"
TideOperation = Literal["list", "get", "create", "update", "delete"]
TideAuditOutcome = Literal[
    "started",
    "succeeded",
    "replayed",
    "conflict",
    "failed",
]
TideAuditKind = Literal["action", "record"]
TideRecordAuditOperation = Literal["create", "update", "delete"]
TideAuditValueMode = Literal["recorded", "field_only", "redacted"]
TideFilterOperator = Literal[
    "eq",
    "ne",
    "lt",
    "lte",
    "gt",
    "gte",
    "contains",
    "icontains",
]
TideAlignment = Literal["left", "center", "right"]
TideCollectionAction = Literal["add", "apply", "remove"]
TideReportKind = Literal["record", "summary"]
TideReportExportFormat = Literal["csv", "html", "pdf"]
TideReportParameterType = Literal[
    "string", "integer", "decimal", "boolean", "date", "datetime"
]


class TideReportValue(BaseModel):
    """One formatted label/value pair in a report document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    text: str
    alignment: TideAlignment = "left"


class TideReportColumn(BaseModel):
    """One renderer-neutral report table column."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    label: str
    alignment: TideAlignment = "left"


class TideReportCell(BaseModel):
    """One preformatted report table cell."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    alignment: TideAlignment = "left"


class TideReportTable(BaseModel):
    """Renderer-neutral tabular report detail."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    columns: tuple[TideReportColumn, ...]
    rows: tuple[tuple[TideReportCell, ...], ...]


class TideReportGroup(BaseModel):
    """One named, subtotaled slice of the flat report detail rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    values: tuple[TideReportValue, ...]
    row_start: int = Field(ge=0)
    row_count: int = Field(ge=0)
    footer_values: tuple[TideReportValue, ...]


class TideReportDocument(BaseModel):
    """Versioned wire form of an authorized renderer-neutral report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    report: str
    title: str
    application: str
    generated_at: datetime
    header_text: tuple[str, ...]
    record_values: tuple[TideReportValue, ...]
    detail: TideReportTable
    footer_values: tuple[TideReportValue, ...]
    page_footer_template: str
    suggested_filename: str
    groups: tuple[TideReportGroup, ...] = ()


class TideFilterInput(BaseModel):
    """One typed field predicate in a remote secured query."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1)
    operator: TideFilterOperator
    value: Any


class TideSortInput(BaseModel):
    """One ordered field in a remote secured query."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1)
    descending: bool = False


class TideQueryInput(BaseModel):
    """Structured query body; values are normalized against entity metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    filters: tuple[TideFilterInput, ...] = ()
    sort: tuple[TideSortInput, ...] = ()
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = Field(default=None, min_length=1)


class TideReferenceSelectionInput(BaseModel):
    """A partial draft and selected reference identity for server-side assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: str = Field(min_length=1)
    field: str = Field(min_length=1)
    values: dict[str, Any]
    identity: Any


class TideReferenceSelectionResult(BaseModel):
    """The secured writable draft values after declarative assignments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    values: dict[str, Any]


class TideAuditFieldChange(BaseModel):
    """Safe wire projection of one changed record field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1)
    before_present: bool
    after_present: bool
    value_mode: TideAuditValueMode
    before: Any = None
    after: Any = None

    @model_validator(mode="after")
    def safe_values(self) -> TideAuditFieldChange:
        if self.value_mode != "recorded" and (
            self.before is not None or self.after is not None
        ):
            raise ValueError("non-recorded audit values must be omitted")
        if (not self.before_present and self.before is not None) or (
            not self.after_present and self.after is not None
        ):
            raise ValueError("absent audit sides cannot contain values")
        return self


class TideAuditEvent(BaseModel):
    """Safe wire projection of one action or CRUD audit event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    entity: str
    kind: TideAuditKind = "action"
    action: str | None = None
    operation: TideRecordAuditOperation | None = None
    identity: Any
    principal: str
    channel: str
    correlation_id: str
    started_at: datetime
    outcome: TideAuditOutcome | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    source: Literal["user", "action", "system"] | None = None
    changes: tuple[TideAuditFieldChange, ...] = ()

    @model_validator(mode="after")
    def valid_variant(self) -> TideAuditEvent:
        if self.kind == "action":
            if self.action is None or self.outcome is None:
                raise ValueError("action audit events require action and outcome")
            if self.operation is not None or self.source is not None or self.changes:
                raise ValueError("action audit events cannot contain CRUD details")
        else:
            if self.operation is None or self.source is None or not self.changes:
                raise ValueError("record audit events require operation, source, and changes")
            if self.action is not None or self.outcome is not None:
                raise ValueError("record audit events cannot contain action lifecycle fields")
            if self.finished_at is not None or self.error_code is not None:
                raise ValueError("record audit events cannot contain action completion fields")
        return self


class TideAuditHistory(BaseModel):
    """Bounded newest-first history for one authorized record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    entity: str
    identity: Any
    events: tuple[TideAuditEvent, ...] = ()


class TideEntityCapabilities(BaseModel):
    """Operations the authenticated principal may attempt through this server."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operations: tuple[TideOperation, ...] = ()
    draft_operations: tuple[Literal["create", "update"], ...] = ()
    readable_fields: tuple[str, ...] = ()
    writable_fields: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    audit: bool = False


class TideSessionInfo(BaseModel):
    """Authenticated principal and application compatibility information."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    application: str
    application_version: str
    schema_version: str
    authentication: str
    principal: str
    roles: tuple[str, ...] = ()
    reports: tuple[str, ...] = ()
    entities: dict[str, TideEntityCapabilities]


class TideBrowserAuthenticationInfo(BaseModel):
    """Public discovery for the optional same-origin browser login flow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    mode: Literal["oidc", "password", "development"] | None = None
    login_path: str | None = Field(default=None, pattern=r"^/")
    session_path: str | None = Field(default=None, pattern=r"^/")
    logout_path: str | None = Field(default=None, pattern=r"^/")


class TideBrowserSessionInfo(BaseModel):
    """Browser-only session material that never includes provider tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    csrf_token: str = Field(min_length=32, max_length=256)


class TidePasswordLoginInput(BaseModel):
    """Bounded credentials accepted only by the local password adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class TidePresentationFormat(BaseModel):
    """Safe display-only options resolved from one named semantic format."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decimal_places: int | None = Field(default=None, ge=0, le=30)
    thousands_separator: bool = False
    display: str | None = Field(default=None, max_length=64)


class TidePresentationReference(BaseModel):
    """Authorized record-display contract for one browse reference column."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: str = Field(min_length=1)
    resource_path: str = Field(pattern=r"^/")
    identity_field: str = Field(min_length=1)
    display_template: str = Field(min_length=1, max_length=512)


class TideValueLabel(BaseModel):
    """One stored code and the text it stands for."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: bool | int | str
    label: str = Field(min_length=1)


class TidePresentationColumn(BaseModel):
    """One safe, renderer-neutral browse column in the remote manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    field_type: str = Field(min_length=1)
    alignment: TideAlignment = "left"
    format: str | None = None
    format_options: TidePresentationFormat | None = None
    target_entity: str | None = None
    reference: TidePresentationReference | None = None
    # On the column rather than only on the form field: a browse grid shows a
    # code just as a form does, and a reader who has to translate it in one
    # place and not the other is worse off than one who translates everywhere.
    values: tuple[TideValueLabel, ...] = ()


class TidePresentationLookup(BaseModel):
    """One authorized compiler-approved reference lookup for a form field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    view: str = Field(min_length=1)
    title: str = Field(min_length=1)
    owner_entity: str = Field(min_length=1)
    field: str = Field(min_length=1)
    target_entity: str = Field(min_length=1)
    resource_path: str = Field(pattern=r"^/")
    query_path: str = Field(pattern=r"^/")
    selection_path: str = Field(pattern=r"^/")
    identity_field: str = Field(min_length=1)
    columns: tuple[TidePresentationColumn, ...] = Field(min_length=1)
    search_fields: tuple[str, ...] = Field(min_length=1)
    page_size: int = Field(ge=1, le=500)
    operations: tuple[TideOperation, ...] = ()
    create_view: str | None = None

    @model_validator(mode="after")
    def lookup_configuration_is_consistent(
        self,
    ) -> TidePresentationLookup:
        if len(self.search_fields) != len(set(self.search_fields)):
            raise ValueError("lookup search fields must not be repeated")
        if self.create_view is not None and "create" not in self.operations:
            raise ValueError("lookup create view requires create capability")
        return self


class TidePresentationFormField(TidePresentationColumn):
    """Safe editor metadata for one readable scalar form field."""

    writable: bool = False
    lookup: TidePresentationLookup | None = None
    required: bool = False
    help: str | None = None
    max_length: int | None = Field(default=None, ge=1)
    choices: tuple[str, ...] = ()
    regex: str | None = Field(default=None, max_length=1024)
    numeric_mask: str | None = Field(default=None, max_length=64)
    precision: int | None = Field(default=None, ge=1)
    scale: int | None = Field(default=None, ge=0)
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    has_default: bool = False
    default_value: str | int | Decimal | bool | date | datetime | None = None

    @model_validator(mode="after")
    def editor_constraints_are_consistent(
        self,
    ) -> TidePresentationFormField:
        if (
            self.precision is not None
            and self.scale is not None
            and self.scale > self.precision
        ):
            raise ValueError("form field scale cannot exceed precision")
        if not self.has_default and self.default_value is not None:
            raise ValueError("form field default value requires has_default")
        if self.lookup is not None and (
            self.field_type != "reference"
            or self.target_entity != self.lookup.target_entity
            or self.name != self.lookup.field
        ):
            raise ValueError("form lookup must match its reference field")
        return self


class TidePresentationFormGroup(BaseModel):
    """One ordered renderer-neutral scalar-field section."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["group"] = "group"
    label: str = Field(min_length=1)
    rows: tuple[tuple[str, ...], ...] = Field(min_length=1)
    tab: str | None = None


class TidePresentationFormCollection(BaseModel):
    """One capability-filtered inline collection in a detail form."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["collection"] = "collection"
    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    record_label: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    view: str = Field(min_length=1)
    identity_field: str | None = Field(default=None, min_length=1)
    sequence_field: str | None = Field(default=None, min_length=1)
    columns: tuple[TidePresentationColumn, ...] = Field(min_length=1)
    fields: dict[str, TidePresentationFormField] = Field(default_factory=dict)
    groups: tuple[TidePresentationFormGroup, ...] = ()
    actions: tuple[TideCollectionAction, ...] = ()
    draft_operations: tuple[Literal["create", "update"], ...] = ()
    writable: bool = False
    tab: str | None = None

    @model_validator(mode="after")
    def editor_configuration_is_consistent(
        self,
    ) -> TidePresentationFormCollection:
        editor_names = {
            field_name
            for group in self.groups
            for row in group.rows
            for field_name in row
        }
        if editor_names != set(self.fields):
            raise ValueError(
                "collection editor groups and declared fields must match"
            )
        if len(self.actions) != len(set(self.actions)):
            raise ValueError("collection actions must not be repeated")
        if self.writable and (
            self.identity_field is None
            or not self.fields
            or not self.groups
            or not self.actions
            or not self.draft_operations
        ):
            raise ValueError(
                "writable collection requires fields, groups, actions, "
                "and draft operations"
            )
        if any(
            field.lookup is not None
            and field.lookup.owner_entity != self.entity
            for field in self.fields.values()
        ):
            raise ValueError("collection lookup owner must match its entity")
        return self


TidePresentationFormSection = (
    TidePresentationFormGroup | TidePresentationFormCollection
)


class TidePresentationFormAction(BaseModel):
    """One capability-gated domain action without source expressions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    idempotent: bool = False


class TideFormPresentation(BaseModel):
    """One capability-filtered semantic detail-form contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    view: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    label: str = Field(min_length=1)
    display_template: str | None = None
    fields: dict[str, TidePresentationFormField]
    sections: tuple[TidePresentationFormSection, ...] = Field(min_length=1)
    actions: tuple[TidePresentationFormAction, ...] = ()

    @model_validator(mode="after")
    def sections_reference_declared_fields(self) -> TideFormPresentation:
        scalar_names = {
            field_name
            for section in self.sections
            if isinstance(section, TidePresentationFormGroup)
            for row in section.rows
            for field_name in row
        }
        if scalar_names != set(self.fields):
            raise ValueError(
                "form sections and declared scalar fields must match"
            )
        collection_names = [
            section.name
            for section in self.sections
            if isinstance(section, TidePresentationFormCollection)
        ]
        if len(collection_names) != len(set(collection_names)):
            raise ValueError("form repeats an inline collection")
        action_names = [action.name for action in self.actions]
        if len(action_names) != len(set(action_names)):
            raise ValueError("form repeats a domain action")
        return self


class TidePresentationNamedFilter(BaseModel):
    """One named browse filter translated to structured query conditions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    conditions: tuple[TideFilterInput, ...] = Field(min_length=1)


class TideBrowsePresentation(BaseModel):
    """One capability-filtered browse contract for remote renderers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    view: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    label: str = Field(min_length=1)
    resource_path: str = Field(pattern=r"^/")
    query_path: str = Field(pattern=r"^/")
    identity_field: str = Field(min_length=1)
    columns: tuple[TidePresentationColumn, ...] = Field(min_length=1)
    search_field: str | None = None
    search_label: str | None = None
    named_filters: tuple[TidePresentationNamedFilter, ...] = ()
    sortable_fields: tuple[str, ...] = ()
    page_size: int = Field(ge=1, le=500)
    operations: tuple[TideOperation, ...] = ()
    detail_view: str | None = None


class TidePresentationNavigationItem(BaseModel):
    """One browse destination in secured application navigation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    view: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    label: str = Field(min_length=1)


class TidePresentationNavigationGroup(BaseModel):
    """One non-empty capability-filtered application-navigation group."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1)
    items: tuple[TidePresentationNavigationItem, ...] = Field(min_length=1)


class TideReportParameter(BaseModel):
    """One value a renderer collects as text before building a summary.

    `required` means the caller must supply the value. A parameter whose
    definition carries a default is offered as optional here, because the
    report service fills the default on its own; typing and range checks
    also stay with the service, so the renderer sends strings and nothing
    else.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: TideReportParameterType
    required: bool = False


class TidePresentationReport(BaseModel):
    """One authorized renderer-neutral report entry point."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: TideReportKind
    entity: str = Field(min_length=1)
    resource_path: str = Field(pattern=r"^/")
    export_formats: tuple[TideReportExportFormat, ...] = (
        "csv",
        "html",
        "pdf",
    )
    # Empty for record reports: their identity parameter is bound from the
    # URL, so a renderer has nothing to collect.
    parameters: tuple[TideReportParameter, ...] = ()


class TidePresentationManifest(BaseModel):
    """Versioned safe presentation projection for remote UI renderers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    application: str
    application_version: str
    schema_version: str
    principal: str
    navigation: tuple[TidePresentationNavigationGroup, ...] = ()
    views: dict[str, TideBrowsePresentation]
    forms: dict[str, TideFormPresentation] = Field(default_factory=dict)
    reports: dict[str, TidePresentationReport] = Field(default_factory=dict)

    @model_validator(mode="after")
    def navigation_matches_views(self) -> TidePresentationManifest:
        items = tuple(
            item
            for group in self.navigation
            for item in group.items
        )
        names = tuple(item.view for item in items)
        if len(names) != len(set(names)):
            raise ValueError("presentation navigation repeats a view")
        if set(names) != set(self.views):
            raise ValueError(
                "presentation navigation and browse views must match"
            )
        if any(
            self.views[item.view].entity != item.entity
            for item in items
        ):
            raise ValueError(
                "presentation navigation entity does not match its browse view"
            )
        detail_forms = {
            view.detail_view
            for view in self.views.values()
            if view.detail_view is not None
        }
        lookup_forms = {
            field.lookup.create_view
            for form in self.forms.values()
            for field in (
                *form.fields.values(),
                *(
                    field
                    for section in form.sections
                    if isinstance(
                        section,
                        TidePresentationFormCollection,
                    )
                    for field in section.fields.values()
                ),
            )
            if field.lookup is not None
            and field.lookup.create_view is not None
        }
        referenced_forms = detail_forms | lookup_forms
        if referenced_forms != set(self.forms):
            raise ValueError(
                "presentation detail/lookup views and forms must match"
            )
        if any(
            self.forms[view.detail_view].entity != view.entity
            for view in self.views.values()
            if view.detail_view is not None
        ):
            raise ValueError(
                "presentation form entity does not match its browse view"
            )
        return self
