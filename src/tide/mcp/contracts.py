"""Versioned structured-output contracts for runtime MCP reads."""

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
    resources: tuple[Literal["schema", "record"], ...] = ()
    tools: tuple[Literal["search"], ...] = ()
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
