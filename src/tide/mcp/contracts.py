"""Versioned structured-output contracts for secured runtime MCP access."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tide.api.contracts import TIDE_WIRE_VERSION, TideFilterOperator


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


class TideMcpPage(BaseModel):
    """One authorized bounded result page returned by an MCP query tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    wire_version: Literal["0.1"] = TIDE_WIRE_VERSION
    application: str
    entity: str
    records: tuple[dict[str, Any], ...]
    next_cursor: str | None = None


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
