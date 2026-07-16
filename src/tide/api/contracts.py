"""Shared wire contracts for TIDE HTTP servers and clients."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TIDE_WIRE_VERSION = "0.1"
TideOperation = Literal["list", "get", "create", "update"]
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


class TideEntityCapabilities(BaseModel):
    """Operations the authenticated principal may attempt through this server."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operations: tuple[TideOperation, ...] = ()
    draft_operations: tuple[Literal["create", "update"], ...] = ()
    readable_fields: tuple[str, ...] = ()
    writable_fields: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()


class TideSessionInfo(BaseModel):
    """Authenticated principal and application compatibility information."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    application: str
    application_version: str
    schema_version: str
    principal: str
    roles: tuple[str, ...] = ()
    reports: tuple[str, ...] = ()
    entities: dict[str, TideEntityCapabilities]
