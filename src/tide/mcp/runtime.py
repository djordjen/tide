"""Adapter-independent secured runtime MCP read services."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping

from tide.api.contracts import TideQueryInput
from tide.api.wire import coerce_identity, decode_filter_value, primary_key, wire_record
from tide.compiler.normalized import ApplicationModel, NormalizedField
from tide.data import FilterCondition, QuerySpec, SortField
from tide.mcp.contracts import (
    TideMcpEntitySchema,
    TideMcpFieldSchema,
    TideMcpPage,
    TideMcpRecord,
)
from tide.runtime import NotFoundError, RequestContext
from tide.services import RecordsService


@dataclass(frozen=True, slots=True)
class RuntimeMcpExposure:
    entity: str
    resources: tuple[str, ...]
    tools: tuple[str, ...]
    schema_uri: str
    record_uri_template: str
    search_tool: str


class RuntimeMcpService:
    """Translate opt-in MCP reads to the authoritative records service."""

    def __init__(self, model: ApplicationModel, records: RecordsService) -> None:
        if records.model is not model:
            raise ValueError("MCP model and records service model must match")
        self.model = model
        self.records = records
        self.exposures = runtime_mcp_exposures(model)

    def entity_schema(
        self,
        entity_name: str,
        context: RequestContext,
    ) -> TideMcpEntitySchema:
        exposure = self._require(entity_name, "resources", "schema")
        entity = self.model.entity(entity_name)
        self.records.security.authorize_entity(entity, "read", context)
        fields = tuple(
            _field_schema(field)
            for field in entity.fields.values()
            if self.records.security.can_read_field(
                entity_name,
                field.name,
                context,
            )
        )
        return TideMcpEntitySchema(
            application=self.model.name,
            application_version=self.model.version,
            schema_version=self.model.schema_version,
            entity=entity_name,
            label=entity.label,
            display=entity.display,
            resources=exposure.resources,
            tools=exposure.tools,
            fields=fields,
        )

    def record(
        self,
        entity_name: str,
        identity: str,
        context: RequestContext,
    ) -> TideMcpRecord:
        self._require(entity_name, "resources", "record")
        entity = self.model.entity(entity_name)
        typed_identity = coerce_identity(
            self.model,
            primary_key(entity),
            identity,
        )
        values = self.records.get(entity_name, typed_identity, context)
        return TideMcpRecord(
            application=self.model.name,
            entity=entity_name,
            record=wire_record(self.model, entity, values),
        )

    def search(
        self,
        entity_name: str,
        query: TideQueryInput,
        context: RequestContext,
    ) -> TideMcpPage:
        self._require(entity_name, "tools", "search")
        entity = self.model.entity(entity_name)
        filters = tuple(
            FilterCondition(
                item.field,
                item.operator,
                decode_filter_value(
                    self.model,
                    entity,
                    item.field,
                    item.value,
                ),
            )
            for item in query.filters
        )
        sort = tuple(
            SortField(item.field, descending=item.descending)
            for item in query.sort
        )
        page = self.records.query_page(
            entity_name,
            QuerySpec(
                filters=filters,
                sort=sort,
                limit=query.limit,
                cursor=query.cursor,
            ),
            context,
        )
        return TideMcpPage(
            application=self.model.name,
            entity=entity_name,
            records=tuple(
                wire_record(self.model, entity, record) for record in page.records
            ),
            next_cursor=page.next_cursor,
        )

    def _require(
        self,
        entity_name: str,
        category: str,
        capability: str,
    ) -> RuntimeMcpExposure:
        exposure = self.exposures.get(entity_name)
        enabled = getattr(exposure, category, ()) if exposure is not None else ()
        if capability not in enabled:
            raise NotFoundError("MCP capability was not found")
        return exposure


def runtime_mcp_exposures(
    model: ApplicationModel,
) -> Mapping[str, RuntimeMcpExposure]:
    application = _identifier(model.name)
    exposures: dict[str, RuntimeMcpExposure] = {}
    for entity_name, entity in model.entities.items():
        configured = entity.metadata.get("expose", {}).get("mcp", False)
        if configured is True:
            resources = ("schema", "record")
            tools = ("search",)
        elif isinstance(configured, Mapping):
            resources = tuple(str(item) for item in configured.get("resources", ()))
            tools = tuple(str(item) for item in configured.get("tools", ()))
        else:
            continue
        if not resources and not tools:
            continue
        stem = f"tide://runtime/{application}/entities/{entity_name}"
        exposures[entity_name] = RuntimeMcpExposure(
            entity=entity_name,
            resources=resources,
            tools=tools,
            schema_uri=f"{stem}/schema",
            record_uri_template=f"{stem}/records/{{identity}}",
            search_tool=f"search_{_identifier(entity_name)}",
        )
    return MappingProxyType(exposures)


def _field_schema(field: NormalizedField) -> TideMcpFieldSchema:
    metadata = field.metadata
    field_type = str(metadata["type"])
    queryable = not (
        field_type == "collection"
        or (
            metadata.get("computed")
            and metadata["computed"].get("materialization") == "virtual"
        )
    )
    operators: tuple[str, ...]
    if not queryable:
        operators = ()
    elif field_type == "boolean":
        operators = ("eq", "ne")
    elif field_type in {"string", "choice"}:
        operators = (
            "eq",
            "ne",
            "lt",
            "lte",
            "gt",
            "gte",
            "contains",
            "icontains",
        )
    else:
        operators = ("eq", "ne", "lt", "lte", "gt", "gte")
    return TideMcpFieldSchema(
        name=field.name,
        label=str(metadata.get("label") or _humanize(field.name)),
        type=field_type,
        required=bool(metadata.get("required")),
        read_only=bool(
            metadata.get("readonly")
            or metadata.get("write", "normal") != "normal"
            or metadata.get("computed")
        ),
        primary_key=bool(metadata.get("primary_key")),
        target=field.target_entity,
        choices=tuple(str(item) for item in metadata.get("choices", ())),
        query_operators=operators,
    )


def _identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    if not normalized:
        raise ValueError("MCP identifier source must contain a letter or number")
    return normalized


def _humanize(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ").title()
