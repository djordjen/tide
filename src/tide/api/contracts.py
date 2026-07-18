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
