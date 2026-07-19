"""Adapter-independent secured runtime MCP application services."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Mapping

from tide.api.contracts import TideAuditHistory, TideQueryInput
from tide.api.wire import (
    coerce_identity,
    decode_filter_value,
    primary_key,
    wire_audit_event,
    wire_record,
)
from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.data import FilterCondition, QuerySpec, SortField
from tide.mcp.contracts import (
    TideMcpActionSchema,
    TideMcpEntitySchema,
    TideMcpFieldSchema,
    TideMcpMutationResult,
    TideMcpPage,
    TideMcpRecord,
)
from tide.runtime import (
    ConcurrencyError,
    NotFoundError,
    RequestContext,
    VersionPreconditionRequired,
)
from tide.services import ActionService, AuditHistoryReader, RecordsService


@dataclass(frozen=True, slots=True)
class RuntimeMcpActionExposure:
    entity: str
    action: str
    label: str
    tool: str
    idempotent: bool


@dataclass(frozen=True, slots=True)
class RuntimeMcpExposure:
    entity: str
    resources: tuple[str, ...]
    tools: tuple[str, ...]
    schema_uri: str
    record_uri_template: str
    audit_uri_template: str
    search_tool: str
    create_tool: str
    update_tool: str
    delete_tool: str
    actions: tuple[RuntimeMcpActionExposure, ...]


class RuntimeMcpService:
    """Translate opt-in MCP calls to authoritative application services."""

    def __init__(
        self,
        model: ApplicationModel,
        records: RecordsService,
        *,
        actions: ActionService | None = None,
        audits: AuditHistoryReader | None = None,
    ) -> None:
        if records.model is not model:
            raise ValueError("MCP model and records service model must match")
        self.model = model
        self.records = records
        self.exposures = runtime_mcp_exposures(model)
        if any(exposure.actions for exposure in self.exposures.values()):
            if actions is None:
                raise ValueError("MCP action exposure requires an action service")
            if actions.model is not model or actions.records is not records:
                raise ValueError("MCP action and records services must share a runtime")
        if any(
            "audit" in exposure.resources for exposure in self.exposures.values()
        ) and audits is None:
            raise ValueError("MCP audit exposure requires an audit history service")
        audit_model = getattr(audits, "model", model)
        if audit_model is not model:
            raise ValueError("MCP audit and records services must share a model")
        self.actions = actions
        self.audits = audits

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
            actions=tuple(
                TideMcpActionSchema(
                    name=action.action,
                    label=action.label,
                    tool=action.tool,
                    idempotent=action.idempotent,
                )
                for action in exposure.actions
                if self.records.security.can_execute_action(
                    entity.actions[action.action],
                    context,
                )
            ),
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

    def audit(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
        *,
        limit: int = 100,
    ) -> TideAuditHistory:
        self._require(entity_name, "resources", "audit")
        if self.audits is None:
            raise RuntimeError("MCP audit history service is not configured")
        entity = self.model.entity(entity_name)
        typed_identity = coerce_identity(
            self.model,
            primary_key(entity),
            identity,
        )
        events = self.audits.for_record(
            entity_name,
            typed_identity,
            context,
            limit=limit,
        )
        return TideAuditHistory(
            entity=entity_name,
            identity=typed_identity,
            events=tuple(wire_audit_event(event) for event in events),
        )

    def create(
        self,
        entity_name: str,
        values: Mapping[str, Any],
        context: RequestContext,
    ) -> TideMcpMutationResult:
        self._require(entity_name, "tools", "create")
        session = self.records.create(entity_name, context, values)
        stored = self.records.commit(session, context)
        return self._mutation_result(
            entity_name,
            "create",
            session.identity,
            stored,
            context,
        )

    def update(
        self,
        entity_name: str,
        identity: Any,
        values: Mapping[str, Any],
        context: RequestContext,
        *,
        expected_version: int | None = None,
    ) -> TideMcpMutationResult:
        self._require(entity_name, "tools", "update")
        if not values:
            raise ValueError("update values must contain at least one field")
        entity = self.model.entity(entity_name)
        typed_identity = coerce_identity(
            self.model,
            primary_key(entity),
            identity,
        )
        session = self.records.begin_edit(entity_name, typed_identity, context)
        _bind_expected_version(entity, session.expected_version, expected_version)
        if expected_version is not None:
            session.expected_version = expected_version
        for field_name, value in values.items():
            session.set(field_name, value)
        stored = self.records.commit(session, context)
        return self._mutation_result(
            entity_name,
            "update",
            typed_identity,
            stored,
            context,
        )

    def delete(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
        *,
        expected_version: int | None = None,
    ) -> TideMcpMutationResult:
        self._require(entity_name, "tools", "delete")
        entity = self.model.entity(entity_name)
        typed_identity = coerce_identity(
            self.model,
            primary_key(entity),
            identity,
        )
        _require_expected_version(entity, expected_version)
        self.records.delete(
            entity_name,
            typed_identity,
            context,
            expected_version=expected_version,
        )
        return self._mutation_result(
            entity_name,
            "delete",
            typed_identity,
            None,
            context,
        )

    def execute_action(
        self,
        entity_name: str,
        action_name: str,
        identity: Any,
        payload: Mapping[str, Any],
        context: RequestContext,
        *,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
    ) -> TideMcpMutationResult:
        exposure = self._require_action(entity_name, action_name)
        if self.actions is None:
            raise RuntimeError("MCP action service is not configured")
        if payload:
            raise ValueError(
                "action payload must be empty for the current metadata contract"
            )
        if exposure.idempotent and idempotency_key is None:
            raise ValueError("idempotency_key is required for this action")
        entity = self.model.entity(entity_name)
        typed_identity = coerce_identity(
            self.model,
            primary_key(entity),
            identity,
        )
        _require_expected_version(entity, expected_version)
        stored = self.actions.execute(
            entity_name,
            action_name,
            typed_identity,
            payload,
            context,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
        )
        return self._mutation_result(
            entity_name,
            "action",
            typed_identity,
            stored,
            context,
            action=action_name,
        )

    def _mutation_result(
        self,
        entity_name: str,
        operation: str,
        identity: Any,
        values: Mapping[str, Any] | None,
        context: RequestContext,
        *,
        action: str | None = None,
    ) -> TideMcpMutationResult:
        entity = self.model.entity(entity_name)
        return TideMcpMutationResult(
            application=self.model.name,
            entity=entity_name,
            operation=operation,
            action=action,
            identity=identity,
            record=(
                wire_record(self.model, entity, values)
                if values is not None
                else None
            ),
            correlation_id=context.correlation_id,
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

    def _require_action(
        self,
        entity_name: str,
        action_name: str,
    ) -> RuntimeMcpActionExposure:
        exposure = self.exposures.get(entity_name)
        if exposure is not None:
            for action in exposure.actions:
                if action.action == action_name:
                    return action
        raise NotFoundError("MCP capability was not found")


def runtime_mcp_exposures(
    model: ApplicationModel,
) -> Mapping[str, RuntimeMcpExposure]:
    application = _identifier(model.name)
    exposures: dict[str, RuntimeMcpExposure] = {}
    tool_owners: dict[str, str] = {}
    for entity_name, entity in model.entities.items():
        configured = entity.metadata.get("expose", {}).get("mcp", False)
        if configured is True:
            resources = ("schema", "record")
            tools = ("search",)
        elif isinstance(configured, Mapping):
            resources = tuple(str(item) for item in configured.get("resources", ()))
            tools = tuple(str(item) for item in configured.get("tools", ()))
        else:
            resources = ()
            tools = ()
        action_exposures = tuple(
            RuntimeMcpActionExposure(
                entity=entity_name,
                action=action_name,
                label=str(action.get("label") or _humanize(action_name)),
                tool=(
                    f"{_identifier(action_name)}_{_identifier(entity_name)}"
                ),
                idempotent=bool(action.get("idempotent")),
            )
            for action_name, action in entity.actions.items()
            if action.get("expose", {}).get("mcp") is True
        )
        if not resources and not tools and not action_exposures:
            continue
        stem = f"tide://runtime/{application}/entities/{entity_name}"
        exposure = RuntimeMcpExposure(
            entity=entity_name,
            resources=resources,
            tools=tools,
            schema_uri=f"{stem}/schema",
            record_uri_template=f"{stem}/records/{{identity}}",
            audit_uri_template=f"{stem}/records/{{identity}}/audit",
            search_tool=f"search_{_identifier(entity_name)}",
            create_tool=f"create_{_identifier(entity_name)}",
            update_tool=f"update_{_identifier(entity_name)}",
            delete_tool=f"delete_{_identifier(entity_name)}",
            actions=action_exposures,
        )
        named_tools = {
            capability: getattr(exposure, f"{capability}_tool")
            for capability in tools
        }
        named_tools.update(
            {f"action {action.action}": action.tool for action in action_exposures}
        )
        for capability, tool_name in named_tools.items():
            owner = f"{entity_name} {capability}"
            previous = tool_owners.setdefault(tool_name, owner)
            if previous != owner:
                raise ValueError(
                    f"MCP tool name {tool_name!r} collides between "
                    f"{previous} and {owner}"
                )
        exposures[entity_name] = exposure
    return MappingProxyType(exposures)


def _require_expected_version(
    entity: NormalizedEntity,
    expected_version: int | None,
) -> None:
    if _version_field(entity) is not None and expected_version is None:
        raise VersionPreconditionRequired(entity.name)


def _bind_expected_version(
    entity: NormalizedEntity,
    actual_version: int | None,
    expected_version: int | None,
) -> None:
    _require_expected_version(entity, expected_version)
    if expected_version is not None and actual_version != expected_version:
        raise ConcurrencyError(expected_version, actual_version)


def _version_field(entity: NormalizedEntity) -> NormalizedField | None:
    return next(
        (
            field
            for field in entity.fields.values()
            if field.metadata.get("concurrency_token")
        ),
        None,
    )


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
