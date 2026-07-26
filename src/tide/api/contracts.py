"""Shared wire contracts for TIDE HTTP servers and clients."""

from __future__ import annotations

from datetime import datetime
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


class TidePresentationFormGroup(BaseModel):
    """One ordered renderer-neutral scalar-field section."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["group"] = "group"
    label: str = Field(min_length=1)
    rows: tuple[tuple[str, ...], ...] = Field(min_length=1)
    tab: str | None = None


class TidePresentationFormCollection(BaseModel):
    """One readable inline collection in an authenticated detail form."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["collection"] = "collection"
    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    columns: tuple[TidePresentationColumn, ...] = Field(min_length=1)
    tab: str | None = None


TidePresentationFormSection = (
    TidePresentationFormGroup | TidePresentationFormCollection
)


class TideFormPresentation(BaseModel):
    """One capability-filtered semantic detail-form contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    view: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    label: str = Field(min_length=1)
    display_template: str | None = None
    fields: dict[str, TidePresentationColumn]
    sections: tuple[TidePresentationFormSection, ...] = Field(min_length=1)

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
        referenced_forms = {
            view.detail_view
            for view in self.views.values()
            if view.detail_view is not None
        }
        if referenced_forms != set(self.forms):
            raise ValueError(
                "presentation browse detail views and forms must match"
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
