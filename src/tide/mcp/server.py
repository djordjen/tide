"""Official MCP SDK hosting adapter for secured runtime capabilities."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from typing import Any, Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, BaseModel, Field

from tide.api.contracts import (
    TideFilterInput,
    TideQueryInput,
    TideSortInput,
)
from tide.api.inputs import build_writable_models
from tide.api.openapi import writable_scalar_annotation
from tide.api.wire import primary_key
from tide.mcp.contracts import TideMcpMutationResult, TideMcpPage
from tide.mcp.runtime import RuntimeMcpService
from tide.observability import current_correlation_id, resolve_correlation_id
from tide.runtime import AuthorizationError, Channel, Principal, RequestContext


class PrincipalAuthenticator(Protocol):
    authentication_type: str

    def authenticate(self, credential: str) -> Principal | None: ...


@dataclass(frozen=True, slots=True)
class HostedRuntimeMcp:
    fastmcp: FastMCP[Any]
    service: RuntimeMcpService
    issuer_url: str
    resource_url: str
    path: str


class TideMcpTokenVerifier(TokenVerifier):
    """Adapt TIDE's reviewed bearer validators to the MCP SDK boundary."""

    def __init__(
        self,
        authenticator: PrincipalAuthenticator,
        resource_url: str,
        issuer_url: str,
    ) -> None:
        self.authenticator = authenticator
        self.resource_url = resource_url
        self.issuer_url = issuer_url

    async def verify_token(self, token: str) -> AccessToken | None:
        principal = await asyncio.to_thread(self.authenticator.authenticate, token)
        if principal is None:
            return None
        return AccessToken(
            token=token,
            client_id=principal.identifier,
            scopes=[],
            resource=self.resource_url,
            subject=principal.identifier,
            claims={
                "iss": self.issuer_url,
                "tide_roles": sorted(principal.roles),
                "tide_permissions": sorted(principal.permissions),
            },
        )


def build_runtime_mcp_server(
    service: RuntimeMcpService,
    authenticator: PrincipalAuthenticator,
    *,
    issuer_url: str,
    resource_url: str,
    path: str = "/mcp",
) -> HostedRuntimeMcp:
    """Build a stateless authenticated Streamable HTTP MCP endpoint."""

    normalized_path = _normalize_path(path)
    parsed_resource = _validate_resource_url(resource_url, normalized_path)
    parsed_issuer = urlsplit(issuer_url)
    if parsed_issuer.scheme not in {"http", "https"} or not parsed_issuer.netloc:
        raise ValueError("MCP issuer must be an absolute HTTP or HTTPS URL")
    origin = f"{parsed_resource.scheme}://{parsed_resource.netloc}"
    fastmcp: FastMCP[Any] = FastMCP(
        name=f"{service.model.name} Runtime",
        instructions=(
            "Secured TIDE application access. Every read, mutation, and domain "
            "action is reauthorized and executed through application services."
        ),
        token_verifier=TideMcpTokenVerifier(
            authenticator,
            resource_url,
            issuer_url,
        ),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(issuer_url),
            resource_server_url=AnyHttpUrl(resource_url),
            required_scopes=[],
        ),
        streamable_http_path=normalized_path,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[parsed_resource.netloc],
            allowed_origins=[origin],
        ),
    )
    create_models, update_models = build_writable_models(
        service.model,
        {
            entity_name: exposure.tools
            for entity_name, exposure in service.exposures.items()
        },
    )
    for exposure in service.exposures.values():
        if "schema" in exposure.resources:
            fastmcp.resource(
                exposure.schema_uri,
                name=f"{exposure.entity} schema",
                description=(
                    "Principal-visible compiled fields and query operators for "
                    f"{exposure.entity}."
                ),
                mime_type="application/json",
            )(_schema_reader(service, exposure.entity))
        if "record" in exposure.resources:
            fastmcp.resource(
                exposure.record_uri_template,
                name=f"{exposure.entity} record",
                description=f"One authorized {exposure.entity} record by identity.",
                mime_type="application/json",
            )(_record_reader(service, exposure.entity))
        if "audit" in exposure.resources:
            fastmcp.resource(
                exposure.audit_uri_template,
                name=f"{exposure.entity} audit history",
                description=(
                    f"Authorized newest-first audit history for one "
                    f"{exposure.entity} record."
                ),
                mime_type="application/json",
            )(_audit_reader(service, exposure.entity))
        if "search" in exposure.tools:
            fastmcp.tool(
                name=exposure.search_tool,
                description=(
                    f"Query authorized {exposure.entity} records with typed filters, "
                    "sorting, a bounded page size, and an opaque continuation cursor."
                ),
                structured_output=True,
            )(_search_tool(service, exposure.entity, exposure.search_tool))
        if "create" in exposure.tools:
            fastmcp.tool(
                name=exposure.create_tool,
                description=(
                    f"Create an authorized {exposure.entity} record through "
                    "TIDE validation, defaults, security, and audit services."
                ),
                structured_output=True,
            )(
                _create_tool(
                    service,
                    exposure.entity,
                    exposure.create_tool,
                    create_models[exposure.entity],
                )
            )
        if "update" in exposure.tools:
            fastmcp.tool(
                name=exposure.update_tool,
                description=(
                    f"Update an authorized {exposure.entity} record. Versioned "
                    "records require the version previously observed by the caller."
                ),
                structured_output=True,
            )(
                _update_tool(
                    service,
                    exposure.entity,
                    exposure.update_tool,
                    update_models[exposure.entity],
                )
            )
        if "delete" in exposure.tools:
            fastmcp.tool(
                name=exposure.delete_tool,
                description=(
                    f"Delete an authorized {exposure.entity} record. Versioned "
                    "records require the version previously observed by the caller."
                ),
                structured_output=True,
            )(_delete_tool(service, exposure.entity, exposure.delete_tool))
        for action in exposure.actions:
            fastmcp.tool(
                name=action.tool,
                description=(
                    f"Execute the authorized {action.label} domain action for "
                    f"{exposure.entity}."
                ),
                structured_output=True,
            )(
                _action_tool(
                    service,
                    exposure.entity,
                    action.action,
                    action.tool,
                )
            )
    return HostedRuntimeMcp(
        fastmcp=fastmcp,
        service=service,
        issuer_url=issuer_url,
        resource_url=resource_url,
        path=normalized_path,
    )


def mount_runtime_mcp(app: FastAPI, hosted: HostedRuntimeMcp) -> None:
    """Mount MCP last while composing its required session-manager lifespan."""

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with original_lifespan(application):
            async with hosted.fastmcp.session_manager.run():
                yield

    app.router.lifespan_context = lifespan
    app.mount("/", hosted.fastmcp.streamable_http_app(), name="tide-runtime-mcp")
    app.state.tide_mcp = hosted


def _schema_reader(service: RuntimeMcpService, entity_name: str) -> Any:
    async def read_schema() -> str:
        result = await asyncio.to_thread(
            service.entity_schema,
            entity_name,
            _request_context(),
        )
        return json.dumps(result.model_dump(mode="json"), separators=(",", ":"))

    read_schema.__name__ = f"read_{entity_name.replace('.', '_')}_schema"
    return read_schema


def _record_reader(service: RuntimeMcpService, entity_name: str) -> Any:
    async def read_record(identity: str) -> str:
        result = await asyncio.to_thread(
            service.record,
            entity_name,
            identity,
            _request_context(),
        )
        return json.dumps(result.model_dump(mode="json"), separators=(",", ":"))

    read_record.__name__ = f"read_{entity_name.replace('.', '_')}_record"
    return read_record


def _audit_reader(service: RuntimeMcpService, entity_name: str) -> Any:
    async def read_audit(identity: str) -> str:
        result = await asyncio.to_thread(
            service.audit,
            entity_name,
            identity,
            _request_context(),
        )
        return json.dumps(result.model_dump(mode="json"), separators=(",", ":"))

    read_audit.__name__ = f"read_{entity_name.replace('.', '_')}_audit"
    return read_audit


def _search_tool(
    service: RuntimeMcpService,
    entity_name: str,
    tool_name: str,
) -> Any:
    async def search_records(
        filters: list[TideFilterInput] | None = None,
        sort: list[TideSortInput] | None = None,
        limit: int = Field(default=20, ge=1, le=500),
        cursor: str | None = Field(default=None, min_length=1),
    ) -> TideMcpPage:
        query = TideQueryInput(
            filters=tuple(filters or ()),
            sort=tuple(sort or ()),
            limit=limit,
            cursor=cursor,
        )
        return await asyncio.to_thread(
            service.search,
            entity_name,
            query,
            _request_context(),
        )

    search_records.__name__ = tool_name
    return search_records


def _create_tool(
    service: RuntimeMcpService,
    entity_name: str,
    tool_name: str,
    input_model: type[BaseModel],
) -> Any:
    async def create_record(values: BaseModel) -> TideMcpMutationResult:
        return await asyncio.to_thread(
            service.create,
            entity_name,
            values.model_dump(by_alias=True, exclude_unset=True),
            _request_context(),
        )

    create_record.__name__ = tool_name
    create_record.__annotations__["values"] = input_model
    return create_record


def _update_tool(
    service: RuntimeMcpService,
    entity_name: str,
    tool_name: str,
    input_model: type[BaseModel],
) -> Any:
    async def update_record(
        identity: Any,
        values: BaseModel,
        expected_version: int | None = Field(default=None, ge=1),
    ) -> TideMcpMutationResult:
        return await asyncio.to_thread(
            service.update,
            entity_name,
            identity,
            values.model_dump(by_alias=True, exclude_unset=True),
            _request_context(),
            expected_version=expected_version,
        )

    update_record.__name__ = tool_name
    update_record.__annotations__["identity"] = _identity_annotation(
        service,
        entity_name,
    )
    update_record.__annotations__["values"] = input_model
    return update_record


def _delete_tool(
    service: RuntimeMcpService,
    entity_name: str,
    tool_name: str,
) -> Any:
    async def delete_record(
        identity: Any,
        expected_version: int | None = Field(default=None, ge=1),
    ) -> TideMcpMutationResult:
        return await asyncio.to_thread(
            service.delete,
            entity_name,
            identity,
            _request_context(),
            expected_version=expected_version,
        )

    delete_record.__name__ = tool_name
    delete_record.__annotations__["identity"] = _identity_annotation(
        service,
        entity_name,
    )
    return delete_record


def _action_tool(
    service: RuntimeMcpService,
    entity_name: str,
    action_name: str,
    tool_name: str,
) -> Any:
    async def execute_action(
        identity: Any,
        expected_version: int | None = Field(default=None, ge=1),
        idempotency_key: str | None = Field(default=None, min_length=1),
    ) -> TideMcpMutationResult:
        return await asyncio.to_thread(
            service.execute_action,
            entity_name,
            action_name,
            identity,
            {},
            _request_context(),
            expected_version=expected_version,
            idempotency_key=idempotency_key,
        )

    execute_action.__name__ = tool_name
    execute_action.__annotations__["identity"] = _identity_annotation(
        service,
        entity_name,
    )
    return execute_action


def _identity_annotation(service: RuntimeMcpService, entity_name: str) -> Any:
    entity = service.model.entity(entity_name)
    return writable_scalar_annotation(service.model, primary_key(entity))


def _request_context() -> RequestContext:
    token = get_access_token()
    if token is None or not token.subject:
        raise AuthorizationError("MCP authentication context is missing")
    claims = token.claims or {}
    roles = claims.get("tide_roles", ())
    permissions = claims.get("tide_permissions", ())
    if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
        raise AuthorizationError("MCP authentication roles are invalid")
    if not isinstance(permissions, list) or any(
        not isinstance(permission, str) for permission in permissions
    ):
        raise AuthorizationError("MCP authentication permissions are invalid")
    return RequestContext(
        principal=Principal(
            token.subject,
            roles=frozenset(roles),
            permissions=frozenset(permissions),
        ),
        channel=Channel.MCP,
        correlation_id=current_correlation_id() or resolve_correlation_id(None),
    )


def _normalize_path(path: str) -> str:
    normalized = path.strip()
    if (
        not normalized.startswith("/")
        or normalized == "/"
        or normalized.endswith("/")
        or "?" in normalized
        or "#" in normalized
    ):
        raise ValueError("MCP path must be an absolute non-root path without a trailing slash")
    return normalized


def _validate_resource_url(resource_url: str, path: str) -> Any:
    try:
        parsed = urlsplit(resource_url)
        _port = parsed.port
    except ValueError as error:
        raise ValueError("MCP resource URL is invalid") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "MCP resource URL must be an absolute HTTP or HTTPS URL without "
            "credentials, query, or fragment"
        )
    if parsed.path != path:
        raise ValueError("MCP resource URL path must exactly match --mcp-path")
    return parsed
