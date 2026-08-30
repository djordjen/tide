"""Versioned structured-output contracts for secured runtime MCP access."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tide.api.contracts import TIDE_WIRE_VERSION, TideFilterOperator, TideParameter
from tide.model.source import TideSummaryFunction


class TideMcpFieldSchema(BaseModel):
    """One principal-visible field in an exposed entity schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    label: str
    type: str
    required: bool = False
    read_only: bool = False
    primary_key: bool = False
    target: str | None = None
    choices: tuple[str, ...] = ()
    query_operators: tuple[TideFilterOperator, ...] = ()


class TideMcpActionSchema(BaseModel):
    """One explicitly exposed domain action and its stable MCP tool name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    label: str
    tool: str
    idempotent: bool = False
    # The same descriptors every renderer reads; the tool's own arguments
    # model is generated from the same declaration.
    parameters: tuple[TideParameter, ...] = ()


class TideMcpEntitySchema(BaseModel):
    """Secured renderer-neutral schema resource for one MCP entity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    application: str
    application_version: str
    schema_version: str
    entity: str
    label: str
    display: str | None = None
    resources: tuple[Literal["schema", "record", "audit"], ...] = ()
    tools: tuple[Literal["search", "create", "update", "delete"], ...] = ()
    actions: tuple[TideMcpActionSchema, ...] = ()
    fields: tuple[TideMcpFieldSchema, ...]


class TideMcpRecord(BaseModel):
    """One authorized record returned through an MCP resource."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    application: str
    entity: str
    record: dict[str, Any]


class TideMcpSummaryValue(BaseModel):
    """One answered aggregate on an MCP search page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    function: TideSummaryFunction
    value: Any = None


class TideMcpPage(BaseModel):
    """One authorized bounded result page returned by an MCP query tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    application: str
    entity: str
    records: tuple[dict[str, Any], ...]
    next_cursor: str | None = None
    summaries: tuple[TideMcpSummaryValue, ...] | None = None
    """Whole-filtered-set answers, present only when the query asked."""


class TideMcpMutationResult(BaseModel):
    """Safe exact-value result for one MCP mutation or domain action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    application: str
    entity: str
    operation: Literal["create", "update", "delete", "action"]
    action: str | None = None
    identity: Any
    record: dict[str, Any] | None = None
    correlation_id: str

    def model_post_init(self, __context: Any) -> None:
        if self.operation == "action" and self.action is None:
            raise ValueError("action mutation results require an action name")
        if self.operation != "action" and self.action is not None:
            raise ValueError("CRUD mutation results cannot contain an action name")
        if self.operation == "delete" and self.record is not None:
            raise ValueError("delete mutation results cannot contain a record")
        if self.operation != "delete" and self.record is None:
            raise ValueError("non-delete mutation results require a record")


TideMcpReportColumnType = Literal[
    "text", "integer", "decimal", "date", "datetime", "boolean"
]


class TideMcpReportValue(BaseModel):
    """One labeled report value: a header field, group name, or total."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    text: str


class TideMcpReportCell(BaseModel):
    """Display text beside the exact value, where one exists.

    `value` is None where the text is already the whole truth: a reference
    names a record, a choice is captioned, a string is a string. A decimal
    travels as its exact string and the column's type says so, which is the
    same contract records use.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    value: Any = None


class TideMcpReportColumn(BaseModel):
    """One detail column, typed by the values this document carries.

    Read back off the typed table rather than re-derived from the model: the
    service already decided per column what is a value and what is text. A
    column with no typed values reads `text`, because it offers nothing to
    compute with.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    label: str
    type: TideMcpReportColumnType = "text"


class TideMcpReportGroup(BaseModel):
    """One contiguous, named, subtotaled slice of the detail rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    values: tuple[TideMcpReportValue, ...]
    row_start: int = Field(ge=0)
    row_count: int = Field(ge=0)
    footer_values: tuple[TideMcpReportValue, ...]


class TideMcpReportDocument(BaseModel):
    """One authorized report as a program reads it.

    Deliberately not the REST document: no page footer, no suggested
    filename, no alignments -- presentation belongs to renderers. What a
    machine needs is what this carries: identity, typed columns, exact
    values beside the text every renderer shows.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    application: str
    report: str
    kind: Literal["record", "summary"]
    entity: str
    title: str
    generated_at: datetime
    header_text: tuple[str, ...]
    record_values: tuple[TideMcpReportValue, ...]
    columns: tuple[TideMcpReportColumn, ...]
    rows: tuple[tuple[TideMcpReportCell, ...], ...]
    groups: tuple[TideMcpReportGroup, ...]
    footer_values: tuple[TideMcpReportValue, ...]
