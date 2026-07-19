"""FastAPI transport adapter over TIDE application services."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import logging
import re
import secrets
from time import perf_counter
from typing import Any, Callable, Literal, Mapping, Protocol

from fastapi import (
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    Security,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from tide.api.contracts import (
    TIDE_WIRE_VERSION,
    TideAuditHistory,
    TideEntityCapabilities,
    TideQueryInput,
    TideReferenceSelectionInput,
    TideReferenceSelectionResult,
    TideReportDocument,
    TideSessionInfo,
)
from tide.api.config import (
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
)
from tide.api.inputs import build_writable_models, field_is_writable
from tide.api.openapi import (
    DEFAULT_BASE_PATH,
    REST_OPERATIONS,
    TideApiError,
    build_openapi_preview,
    rest_exposures,
)
from tide.api.wire import (
    coerce_identity as _coerce_identity,
    decode_filter_value as _decode_filter_value,
    decode_wire_value as _decode_wire_value,
    primary_key as _primary_key,
    wire_audit_event as _wire_audit_event,
    wire_record as _wire_record,
)
from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.data import FilterCondition, QuerySpec, SortField
from tide.observability import (
    CORRELATION_HEADER,
    bind_correlation_id,
    log_runtime_event,
    reset_correlation_id,
    resolve_correlation_id,
)
from tide.runtime import (
    AuthorizationError,
    ActionDisabled,
    Channel,
    ConcurrencyError,
    DeleteRestricted,
    IdempotencyConflict,
    ImmutableFieldError,
    InvalidQueryCursor,
    NotFoundError,
    Principal,
    RequestContext,
    TideRuntimeError,
    ValidationFailed,
    VersionPreconditionRequired,
)
from tide.reporting import ReportService
from tide.security import PROTECTED
from tide.services import ActionService, AuditHistoryReader, AuditHistoryService, RecordsService


SERVER_OPERATIONS = REST_OPERATIONS
_RUNTIME_LOGGER = logging.getLogger("tide.runtime")


class TideEmptyActionPayload(BaseModel):
    """Current action metadata declares no request payload fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TideReadiness(BaseModel):
    """Safe operational readiness result without dependency details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready", "not_ready"]
    application: str
    version: str


class BearerAuthenticator(Protocol):
    """Map a bearer credential to a server-controlled principal."""

    authentication_type: str
    production: bool

    def authenticate(self, credential: str) -> Principal | None: ...


@dataclass(frozen=True, slots=True)
class DevelopmentTokenAuthenticator:
    """Single-token identity adapter for local development only."""

    token: str
    principal: Principal

    authentication_type = "development-bearer"
    production = False

    def __post_init__(self) -> None:
        if len(self.token) < 32:
            raise ValueError("development API token must contain at least 32 characters")

    def authenticate(self, credential: str) -> Principal | None:
        matches = secrets.compare_digest(
            credential.encode("utf-8"),
            self.token.encode("utf-8"),
        )
        return self.principal if matches else None


@dataclass(frozen=True, slots=True)
class TideApiRuntime:
    model: ApplicationModel
    records: RecordsService
    actions: ActionService
    reports: ReportService
    audits: AuditHistoryReader
    authenticator: BearerAuthenticator
    base_path: str
    readiness_probes: tuple[Callable[[], None], ...]
    max_request_body_bytes: int
    request_body_timeout_seconds: int


def build_fastapi_app(
    model: ApplicationModel,
    records: RecordsService,
    authenticator: BearerAuthenticator,
    *,
    actions: ActionService | None = None,
    reports: ReportService | None = None,
    audits: AuditHistoryReader | None = None,
    base_path: str = DEFAULT_BASE_PATH,
    logger: logging.Logger | None = None,
    max_request_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES,
    request_body_timeout_seconds: int = DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
) -> FastAPI:
    """Build an HTTP adapter over services without granting client database access."""

    if (
        isinstance(max_request_body_bytes, bool)
        or not isinstance(max_request_body_bytes, int)
        or max_request_body_bytes <= 0
    ):
        raise ValueError("maximum request body size must be a positive integer")
    if (
        isinstance(request_body_timeout_seconds, bool)
        or not isinstance(request_body_timeout_seconds, int)
        or request_body_timeout_seconds <= 0
    ):
        raise ValueError("request body timeout must be a positive integer")
    preview = build_openapi_preview(model, base_path=base_path)
    exposures = rest_exposures(model, allowed_operations=SERVER_OPERATIONS)
    action_service = actions or ActionService(model, records)
    report_service = reports or ReportService(model, records)
    audit_service = audits or AuditHistoryService(
        model,
        action_service.execution_store,
        records.security,
    )
    runtime_logger = logger or _RUNTIME_LOGGER
    create_models, update_models = build_writable_models(
        model,
        {
            entity_name: exposure.operations
            for entity_name, exposure in exposures.items()
        },
    )
    app = FastAPI(
        title=f"{model.name} API",
        version=model.version,
        description=(
            "TIDE application server. Every request is authenticated and "
            "reauthorized through the application service layer."
        ),
    )
    app.state.tide = TideApiRuntime(
        model,
        records,
        action_service,
        report_service,
        audit_service,
        authenticator,
        base_path,
        _readiness_probes(records, action_service),
        max_request_body_bytes,
        request_body_timeout_seconds,
    )
    bearer = HTTPBearer(
        bearerFormat=("JWT" if authenticator.authentication_type == "oidc-jwt" else "opaque"),
        scheme_name="bearerAuth",
        description=(
            "Bearer credentials are mapped to a Principal by server configuration; "
            "clients cannot choose their roles or permissions."
        ),
        auto_error=False,
    )

    def request_context(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> RequestContext:
        if credentials is None or credentials.scheme.casefold() != "bearer":
            raise _unauthorized()
        principal = authenticator.authenticate(credentials.credentials)
        if principal is None:
            raise _unauthorized()
        return RequestContext(
            principal=principal,
            channel=Channel.REST,
            correlation_id=request.state.tide_correlation_id,
        )

    @app.middleware("http")
    async def runtime_request_boundary(request: Request, call_next: Any) -> Any:
        correlation_id = resolve_correlation_id(
            request.headers.get(CORRELATION_HEADER)
        )
        request.state.tide_correlation_id = correlation_id
        context_token = bind_correlation_id(correlation_id)
        started = perf_counter()
        try:
            try:
                async with asyncio.timeout(request_body_timeout_seconds):
                    body_error = await _buffer_bounded_request_body(
                        request,
                        max_request_body_bytes,
                    )
            except TimeoutError:
                request.state.tide_operation = "requestBodyTimeout"
                response = _request_body_timeout()
                body_error = None
            else:
                response = None
            if body_error is not None:
                request.state.tide_operation = "requestBodyLimit"
                response = body_error
            elif response is None:
                response = await call_next(request)
        except Exception as error:
            log_runtime_event(
                runtime_logger,
                logging.ERROR,
                "http.request.failed",
                channel=_request_channel(app, request),
                correlation_id=correlation_id,
                operation=_request_operation(request),
                method=request.method,
                duration_ms=_duration_ms(started),
                error_type=type(error).__name__,
            )
            raise
        else:
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers[CORRELATION_HEADER] = correlation_id
            log_runtime_event(
                runtime_logger,
                _response_log_level(response.status_code),
                "http.request.completed",
                channel=_request_channel(app, request),
                correlation_id=correlation_id,
                operation=_request_operation(request),
                method=request.method,
                status_code=response.status_code,
                duration_ms=_duration_ms(started),
            )
            return response
        finally:
            reset_correlation_id(context_token)

    @app.exception_handler(TideRuntimeError)
    async def tide_error_handler(
        _request: Request,
        error: TideRuntimeError,
    ) -> JSONResponse:
        status = _runtime_status(error)
        return JSONResponse(
            status_code=status,
            content=TideApiError(code=error.code, message=str(error)).model_dump(),
            headers=(
                {"WWW-Authenticate": "Bearer"}
                if status == 401
                else None
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(
        _request: Request,
        error: HTTPException,
    ) -> JSONResponse:
        if isinstance(error.detail, Mapping):
            code = str(error.detail.get("code", "http_error"))
            message = str(error.detail.get("message", "request failed"))
        else:
            code = "http_error"
            message = str(error.detail)
        return JSONResponse(
            status_code=error.status_code,
            content=TideApiError(code=code, message=message).model_dump(),
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=TideApiError(
                code="invalid_request",
                message="request validation failed",
            ).model_dump(),
        )

    @app.get(
        "/health/live",
        tags=["Health"],
        summary="Process liveness",
        include_in_schema=True,
    )
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/health/ready",
        tags=["Health"],
        summary="Application readiness",
        response_model=TideReadiness,
        responses={
            503: {
                "model": TideReadiness,
                "description": "A required runtime dependency is unavailable",
            }
        },
        include_in_schema=True,
    )
    def ready(request: Request, response: Response) -> TideReadiness:
        status: Literal["ready", "not_ready"] = "ready"
        try:
            for probe in app.state.tide.readiness_probes:
                probe()
        except Exception as error:
            status = "not_ready"
            response.status_code = 503
            log_runtime_event(
                runtime_logger,
                logging.ERROR,
                "readiness.failed",
                channel=Channel.SYSTEM.value,
                correlation_id=request.state.tide_correlation_id,
                operation=_probe_name(probe),
                error_type=type(error).__name__,
            )
        return TideReadiness(
            status=status,
            application=model.name,
            version=model.version,
        )

    @app.get(
        f"{base_path.rstrip('/')}/_tide/session",
        tags=["TIDE"],
        summary="Authenticated client capabilities",
        response_model=TideSessionInfo,
        responses=_documented_errors(401),
    )
    def session_info(
        context: RequestContext = Depends(request_context),
    ) -> TideSessionInfo:
        capabilities: dict[str, TideEntityCapabilities] = {}
        for entity_name, entity in model.entities.items():
            exposure = exposures.get(entity_name)
            operations = tuple(
                operation
                for operation in ("list", "get", "create", "update", "delete")
                if exposure is not None
                and operation in exposure.operations
                and _operation_allowed(records, entity, operation, context)
            )
            draft_operations = tuple(
                operation
                for operation in ("create", "update")
                if _nested_operation_allowed(
                    records,
                    exposures,
                    entity_name,
                    operation,
                    context,
                )
            )
            readable_fields = (
                tuple(
                    field_name
                    for field_name in entity.fields
                    if records.security.can_read_field(
                        entity_name,
                        field_name,
                        context,
                    )
                )
                if "get" in operations or draft_operations
                else ()
            )
            writable_fields = (
                tuple(
                    field_name
                    for field_name, field in entity.fields.items()
                    if field_is_writable(field, "update")
                    and records.security.can_write_field(
                        entity_name,
                        field_name,
                        context,
                    )
                )
                if {"create", "update"} & (set(operations) | set(draft_operations))
                else ()
            )
            allowed_actions = tuple(
                action_name
                for action_name, action in entity.actions.items()
                if entity_name in exposures
                and "get" in operations
                and action.get("expose", {}).get("rest") is True
                and records.security.can_execute_action(action, context)
            )
            capabilities[entity_name] = TideEntityCapabilities(
                operations=operations,
                draft_operations=draft_operations,
                readable_fields=readable_fields,
                writable_fields=writable_fields,
                actions=allowed_actions,
                audit=bool(
                    exposure is not None
                    and "get" in operations
                    and audit_service.can_view(entity_name, context)
                ),
            )
        return TideSessionInfo(
            application=model.name,
            application_version=model.version,
            schema_version=model.schema_version,
            authentication=authenticator.authentication_type,
            principal=context.principal.identifier,
            roles=tuple(sorted(context.principal.roles)),
            reports=tuple(
                report_name
                for report_name, report in model.reports.items()
                if report.get("expose", {}).get("rest") is True
                and report_service.can_generate(report_name, context)
            ),
            entities=capabilities,
        )

    @app.post(
        f"{base_path.rstrip('/')}/_tide/reference-selection",
        tags=["TIDE"],
        summary="Apply a secured reference selection to a draft",
        response_model=TideReferenceSelectionResult,
        responses=_documented_errors(400, 401, 403, 408, 409, 413, 422),
    )
    def reference_selection(
        payload: TideReferenceSelectionInput,
        context: RequestContext = Depends(request_context),
    ) -> TideReferenceSelectionResult:
        try:
            entity = model.entity(payload.entity)
            values = _decode_draft(model, entity, payload.values)
            field = entity.field(payload.field)
            identity = _coerce_reference_identity(model, field, payload.identity)
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request("reference-selection payload is invalid") from error
        updated = records.apply_reference_selection(
            entity.name,
            field.name,
            values,
            identity,
            context,
        )
        return TideReferenceSelectionResult(
            values=_wire_draft(model, entity, updated),
        )

    @app.get(
        f"{base_path.rstrip('/')}/_tide/reports/{{report_name}}/records/{{identity}}",
        tags=["TIDE"],
        summary="Build one secured record report",
        response_model=TideReportDocument,
        responses=_documented_errors(400, 401, 403, 404, 422),
    )
    def record_report(
        context: RequestContext = Depends(request_context),
        report_name: str = Path(min_length=1),
        identity: str = Path(min_length=1),
    ) -> TideReportDocument:
        report = model.reports.get(report_name)
        if report is None or report.get("expose", {}).get("rest") is not True:
            raise NotFoundError(f"report {report_name!r} was not found")
        entity = model.entity(str(report["entity"]))
        primary_key = _primary_key(entity)
        try:
            typed_identity = _coerce_identity(model, primary_key, identity)
        except (TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request("record identity has an invalid type") from error
        document = report_service.build_for_record(
            report_name,
            typed_identity,
            context,
        )
        return TideReportDocument.model_validate(asdict(document))

    for entity_name, exposure in exposures.items():
        entity = model.entity(entity_name)
        resource_path = f"{base_path.rstrip('/')}/{exposure.path}"
        tag = entity.label
        if "list" in exposure.operations:
            list_endpoint = _list_endpoint(
                records,
                entity,
                preview.page_models[entity_name],
                request_context,
            )
            app.add_api_route(
                resource_path,
                list_endpoint,
                methods=["GET"],
                response_model=preview.page_models[entity_name],
                response_model_by_alias=True,
                name=f"List {entity.label}",
                operation_id=f"list{preview.record_models[entity_name].__name__.removesuffix('Record')}",
                tags=[tag],
                responses=_documented_errors(400, 401, 403, 408, 413, 422),
            )
            query_endpoint = _query_endpoint(
                records,
                entity,
                preview.page_models[entity_name],
                request_context,
            )
            app.add_api_route(
                f"{resource_path}/_query",
                query_endpoint,
                methods=["POST"],
                response_model=preview.page_models[entity_name],
                response_model_by_alias=True,
                name=f"Query {entity.label}",
                operation_id=(
                    f"query{preview.record_models[entity_name].__name__.removesuffix('Record')}"
                ),
                tags=[tag],
                responses=_documented_errors(400, 401, 403, 422),
            )
        if "get" in exposure.operations:
            primary_key = _primary_key(entity)
            get_endpoint = _get_endpoint(
                records,
                entity,
                primary_key,
                preview.record_models[entity_name],
                request_context,
            )
            app.add_api_route(
                f"{resource_path}/{{{primary_key.name}}}",
                get_endpoint,
                methods=["GET"],
                response_model=preview.record_models[entity_name],
                response_model_by_alias=True,
                name=f"Get one {entity.label}",
                operation_id=f"get{preview.record_models[entity_name].__name__.removesuffix('Record')}",
                tags=[tag],
                responses=_documented_errors(400, 401, 403, 404, 422),
            )
            if entity.metadata.get("permissions", {}).get("audit") is not None:
                audit_endpoint = _audit_endpoint(
                    audit_service,
                    model,
                    entity,
                    primary_key,
                    request_context,
                )
                app.add_api_route(
                    f"{resource_path}/{{{primary_key.name}}}/_audit",
                    audit_endpoint,
                    methods=["GET"],
                    response_model=TideAuditHistory,
                    name=f"Audit history for {entity.label}",
                    operation_id=(
                        "audit"
                        f"{preview.record_models[entity_name].__name__.removesuffix('Record')}"
                    ),
                    tags=[tag],
                    responses=_documented_errors(400, 401, 403, 422),
                )
        if "create" in exposure.operations:
            create_endpoint = _create_endpoint(
                records,
                entity,
                create_models[entity_name],
                preview.record_models[entity_name],
                request_context,
                resource_path,
            )
            app.add_api_route(
                resource_path,
                create_endpoint,
                methods=["POST"],
                status_code=201,
                response_model=preview.record_models[entity_name],
                response_model_by_alias=True,
                name=f"Create {entity.label}",
                operation_id=f"create{preview.record_models[entity_name].__name__.removesuffix('Record')}",
                tags=[tag],
                responses=_documented_errors(400, 401, 403, 408, 409, 413, 422),
            )
        if "update" in exposure.operations:
            primary_key = _primary_key(entity)
            update_endpoint = _update_endpoint(
                records,
                entity,
                primary_key,
                update_models[entity_name],
                preview.record_models[entity_name],
                request_context,
            )
            app.add_api_route(
                f"{resource_path}/{{{primary_key.name}}}",
                update_endpoint,
                methods=["PATCH"],
                response_model=preview.record_models[entity_name],
                response_model_by_alias=True,
                name=f"Update {entity.label}",
                operation_id=f"update{preview.record_models[entity_name].__name__.removesuffix('Record')}",
                tags=[tag],
                responses=_documented_errors(
                    400, 401, 403, 404, 408, 409, 412, 413, 422, 428
                ),
            )
        if "delete" in exposure.operations:
            primary_key = _primary_key(entity)
            delete_endpoint = _delete_endpoint(
                records,
                entity,
                primary_key,
                request_context,
            )
            app.add_api_route(
                f"{resource_path}/{{{primary_key.name}}}",
                delete_endpoint,
                methods=["DELETE"],
                status_code=204,
                response_class=Response,
                name=f"Delete {entity.label}",
                operation_id=(
                    f"delete{preview.record_models[entity_name].__name__.removesuffix('Record')}"
                ),
                tags=[tag],
                responses=_documented_errors(
                    400, 401, 403, 404, 409, 412, 422, 428
                ),
            )

        primary_key = _primary_key(entity)
        for action_name, action in entity.actions.items():
            if action.get("expose", {}).get("rest") is not True:
                continue
            action_endpoint = _action_endpoint(
                action_service,
                entity,
                primary_key,
                action_name,
                action,
                preview.record_models[entity_name],
                request_context,
            )
            app.add_api_route(
                f"{resource_path}/{{{primary_key.name}}}/actions/{action_name}",
                action_endpoint,
                methods=["POST"],
                response_model=preview.record_models[entity_name],
                response_model_by_alias=True,
                name=str(action.get("label") or action_name),
                operation_id=(
                    f"{action_name}{preview.record_models[entity_name].__name__.removesuffix('Record')}"
                ),
                tags=[tag],
                responses=_documented_errors(
                    400, 401, 403, 404, 408, 409, 412, 413, 422, 428
                ),
            )

    generated_openapi = app.openapi

    def tide_openapi() -> dict[str, Any]:
        schema = generated_openapi()
        schema["x-tide"] = {
            "runtime": True,
            "read_only": False,
            "wire_version": TIDE_WIRE_VERSION,
            "schema_version": model.schema_version,
            "authentication": authenticator.authentication_type,
            "max_request_body_bytes": max_request_body_bytes,
            "request_body_timeout_seconds": request_body_timeout_seconds,
        }
        return schema

    app.openapi = tide_openapi  # type: ignore[method-assign]
    return app


def _readiness_probes(
    records: RecordsService,
    actions: ActionService,
) -> tuple[Callable[[], None], ...]:
    probes: list[Callable[[], None]] = []
    repository_probe = getattr(records.repository, "check_readiness", None)
    probes.append(
        repository_probe
        if callable(repository_probe)
        else _missing_repository_readiness_probe
    )
    for store in (records.cursor_store, actions.execution_store):
        validate_schema = getattr(store, "validate_schema", None)
        if callable(validate_schema):
            probes.append(validate_schema)
    return tuple(probes)


def _request_channel(app: FastAPI, request: Request) -> str:
    hosted_mcp = getattr(app.state, "tide_mcp", None)
    mcp_path = getattr(hosted_mcp, "path", None)
    if isinstance(mcp_path, str) and (
        request.url.path == mcp_path
        or request.url.path.startswith(f"{mcp_path}/")
    ):
        return Channel.MCP.value
    return Channel.REST.value


async def _buffer_bounded_request_body(
    request: Request,
    max_request_body_bytes: int,
) -> JSONResponse | None:
    content_length = request.headers.get("Content-Length")
    if content_length is not None and re.fullmatch(r"[0-9]+", content_length) is None:
        return JSONResponse(
            status_code=400,
            content=TideApiError(
                code="invalid_request",
                message="Content-Length must be a non-negative integer",
            ).model_dump(),
        )
    if content_length is not None and int(content_length) > max_request_body_bytes:
        return _request_too_large()
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_request_body_bytes:
            return _request_too_large()
        body.extend(chunk)
    request._body = bytes(body)
    return None


def _request_too_large() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content=TideApiError(
            code="request_too_large",
            message="request body exceeds the configured limit",
        ).model_dump(),
    )


def _request_body_timeout() -> JSONResponse:
    return JSONResponse(
        status_code=408,
        content=TideApiError(
            code="request_timeout",
            message="request body was not received within the configured timeout",
        ).model_dump(),
    )


def _request_operation(request: Request) -> str:
    boundary_operation = getattr(request.state, "tide_operation", None)
    if isinstance(boundary_operation, str) and boundary_operation:
        return boundary_operation[:128]
    route = request.scope.get("route")
    for attribute in ("operation_id", "name"):
        name = getattr(route, attribute, None)
        if isinstance(name, str) and name:
            return name[:128]
    return "unmatched"


def _response_log_level(status_code: int) -> int:
    if status_code >= 500:
        return logging.ERROR
    if status_code >= 400:
        return logging.WARNING
    return logging.INFO


def _duration_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


def _probe_name(probe: Callable[[], None]) -> str:
    name = getattr(probe, "__qualname__", None)
    if not isinstance(name, str) or not name:
        name = type(probe).__qualname__
    return name[:128]


def _missing_repository_readiness_probe() -> None:
    raise RuntimeError("repository does not implement the readiness contract")


def _audit_endpoint(
    audits: AuditHistoryReader,
    model: ApplicationModel,
    entity: NormalizedEntity,
    primary_key: NormalizedField,
    context_dependency: Any,
) -> Any:
    def record_audit(
        context: RequestContext = Depends(context_dependency),
        identity: str = Path(alias=primary_key.name, description="Record identity"),
        limit: int = Query(100, ge=1, le=500),
    ) -> TideAuditHistory:
        try:
            typed_identity = _coerce_identity(model, primary_key, identity)
        except (TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request("record identity has an invalid type") from error
        events = audits.for_record(
            entity.name,
            typed_identity,
            context,
            limit=limit,
        )
        return TideAuditHistory(
            entity=entity.name,
            identity=typed_identity,
            events=tuple(_wire_audit_event(event) for event in events),
        )

    record_audit.__name__ = f"audit_{entity.name.replace('.', '_')}"
    record_audit.__annotations__["identity"] = _identity_annotation(
        model,
        primary_key,
    )
    return record_audit


def _create_endpoint(
    records: RecordsService,
    entity: NormalizedEntity,
    input_model: type[BaseModel],
    record_model: type[BaseModel],
    context_dependency: Any,
    resource_path: str,
) -> Any:
    def create_record(
        response: Response,
        payload: BaseModel = Body(),
        context: RequestContext = Depends(context_dependency),
    ) -> BaseModel:
        values = payload.model_dump(by_alias=True, exclude_unset=True)
        session = records.create(entity.name, context, values)
        stored = records.commit(session, context)
        _set_etag(response, entity, stored)
        identity = stored[_primary_key(entity).name]
        response.headers["Location"] = f"{resource_path}/{identity}"
        return record_model.model_validate(_wire_record(records.model, entity, stored))

    create_record.__name__ = f"create_{entity.name.replace('.', '_')}"
    create_record.__annotations__["payload"] = input_model
    return create_record


def _update_endpoint(
    records: RecordsService,
    entity: NormalizedEntity,
    primary_key: NormalizedField,
    input_model: type[BaseModel],
    record_model: type[BaseModel],
    context_dependency: Any,
) -> Any:
    def update_record(
        response: Response,
        payload: BaseModel = Body(),
        context: RequestContext = Depends(context_dependency),
        identity: str = Path(alias=primary_key.name, description="Record identity"),
        if_match: str | None = Header(None, alias="If-Match"),
    ) -> BaseModel:
        try:
            typed_identity = _coerce_identity(records.model, primary_key, identity)
        except (TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request("record identity has an invalid type") from error
        session = records.begin_edit(entity.name, typed_identity, context)
        expected = _required_version(entity, if_match)
        _bind_expected_version(session, expected)
        values = payload.model_dump(by_alias=True, exclude_unset=True)
        if not values:
            raise _bad_request("update payload must contain at least one field")
        for field_name, value in values.items():
            session.set(field_name, value)
        stored = records.commit(session, context)
        _set_etag(response, entity, stored)
        return record_model.model_validate(_wire_record(records.model, entity, stored))

    update_record.__name__ = f"update_{entity.name.replace('.', '_')}"
    update_record.__annotations__["payload"] = input_model
    update_record.__annotations__["identity"] = _identity_annotation(
        records.model,
        primary_key,
    )
    return update_record


def _action_endpoint(
    actions: ActionService,
    entity: NormalizedEntity,
    primary_key: NormalizedField,
    action_name: str,
    action: Mapping[str, Any],
    record_model: type[BaseModel],
    context_dependency: Any,
) -> Any:
    def execute_action(
        response: Response,
        payload: TideEmptyActionPayload,
        context: RequestContext = Depends(context_dependency),
        identity: str = Path(alias=primary_key.name, description="Record identity"),
        if_match: str | None = Header(None, alias="If-Match"),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> BaseModel:
        try:
            typed_identity = _coerce_identity(actions.model, primary_key, identity)
        except (TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request("record identity has an invalid type") from error
        expected = _required_version(entity, if_match)
        if action.get("idempotent") and idempotency_key is None:
            raise _precondition_required("Idempotency-Key header is required")
        stored = actions.execute(
            entity.name,
            action_name,
            typed_identity,
            payload.model_dump(exclude_unset=True),
            context,
            idempotency_key=idempotency_key,
            expected_version=expected,
        )
        _set_etag(response, entity, stored)
        return record_model.model_validate(_wire_record(actions.model, entity, stored))

    execute_action.__name__ = (
        f"execute_{entity.name.replace('.', '_')}_{action_name}"
    )
    execute_action.__annotations__["identity"] = _identity_annotation(
        actions.model,
        primary_key,
    )
    return execute_action


def _delete_endpoint(
    records: RecordsService,
    entity: NormalizedEntity,
    primary_key: NormalizedField,
    context_dependency: Any,
) -> Any:
    def delete_record(
        context: RequestContext = Depends(context_dependency),
        identity: str = Path(alias=primary_key.name, description="Record identity"),
        if_match: str | None = Header(None, alias="If-Match"),
    ) -> Response:
        try:
            typed_identity = _coerce_identity(records.model, primary_key, identity)
        except (TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request("record identity has an invalid type") from error
        expected = _required_version(entity, if_match)
        records.delete(
            entity.name,
            typed_identity,
            context,
            expected_version=expected,
        )
        return Response(status_code=204)

    delete_record.__name__ = f"delete_{entity.name.replace('.', '_')}"
    delete_record.__annotations__["identity"] = _identity_annotation(
        records.model,
        primary_key,
    )
    return delete_record


def _list_endpoint(
    records: RecordsService,
    entity: NormalizedEntity,
    page_model: type[BaseModel],
    context_dependency: Any,
) -> Any:
    def list_records(
        context: RequestContext = Depends(context_dependency),
        limit: int = Query(100, ge=1, le=500),
        cursor: str | None = Query(None, min_length=1),
    ) -> BaseModel:
        try:
            page = records.query_page(
                entity.name,
                QuerySpec(limit=limit, cursor=cursor),
                context,
            )
        except ValueError as error:
            raise _bad_request(str(error)) from error
        return page_model.model_validate(
            {
                "records": [
                    _wire_record(records.model, entity, record)
                    for record in page.records
                ],
                "next_cursor": page.next_cursor,
            }
        )

    list_records.__name__ = f"list_{entity.name.replace('.', '_')}"
    return list_records


def _query_endpoint(
    records: RecordsService,
    entity: NormalizedEntity,
    page_model: type[BaseModel],
    context_dependency: Any,
) -> Any:
    def query_records(
        payload: TideQueryInput,
        context: RequestContext = Depends(context_dependency),
    ) -> BaseModel:
        try:
            filters = tuple(
                FilterCondition(
                    item.field,
                    item.operator,
                    _decode_filter_value(
                        records.model,
                        entity,
                        item.field,
                        item.value,
                    ),
                )
                for item in payload.filters
            )
            sort = tuple(
                SortField(item.field, descending=item.descending)
                for item in payload.sort
            )
            page = records.query_page(
                entity.name,
                QuerySpec(
                    filters=filters,
                    sort=sort,
                    limit=payload.limit,
                    cursor=payload.cursor,
                ),
                context,
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request(str(error) or "structured query is invalid") from error
        return page_model.model_validate(
            {
                "records": [
                    _wire_record(records.model, entity, record)
                    for record in page.records
                ],
                "next_cursor": page.next_cursor,
            }
        )

    query_records.__name__ = f"query_{entity.name.replace('.', '_')}"
    return query_records


def _get_endpoint(
    records: RecordsService,
    entity: NormalizedEntity,
    primary_key: NormalizedField,
    record_model: type[BaseModel],
    context_dependency: Any,
) -> Any:
    def get_record(
        response: Response,
        context: RequestContext = Depends(context_dependency),
        identity: str = Path(alias=primary_key.name, description="Record identity"),
    ) -> BaseModel:
        try:
            typed_identity = _coerce_identity(records.model, primary_key, identity)
            record = records.get(entity.name, typed_identity, context)
        except (TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request("record identity has an invalid type") from error
        _set_etag(response, entity, record)
        return record_model.model_validate(
            _wire_record(records.model, entity, record)
        )

    get_record.__name__ = f"get_{entity.name.replace('.', '_')}"
    get_record.__annotations__["identity"] = _identity_annotation(
        records.model,
        primary_key,
    )
    return get_record


def _decode_draft(
    model: ApplicationModel,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    unknown = set(values) - set(entity.fields)
    if unknown:
        raise ValueError(
            f"unknown draft field(s): {', '.join(sorted(unknown))}"
        )
    return {
        field_name: _decode_wire_value(model, entity.field(field_name), value)
        for field_name, value in values.items()
    }


def _coerce_reference_identity(
    model: ApplicationModel,
    field: NormalizedField,
    value: Any,
) -> Any:
    if field.metadata["type"] != "reference" or field.target_entity is None:
        raise ValueError(f"field {field.name!r} is not a reference")
    return _decode_wire_value(
        model,
        _primary_key(model.entity(field.target_entity)),
        value,
    )


def _wire_draft(
    model: ApplicationModel,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        field_name: _wire_value(model, entity.field(field_name), value)
        for field_name, value in values.items()
        if field_name in entity.fields and value is not PROTECTED
    }


def _wire_value(
    model: ApplicationModel,
    field: NormalizedField,
    value: Any,
) -> Any:
    if value is None:
        return None
    field_type = str(field.metadata["type"])
    if field_type == "collection":
        if field.target_entity is None:
            raise TypeError
        target = model.entity(field.target_entity)
        return [_wire_draft(model, target, item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _identity_annotation(
    model: ApplicationModel,
    field: NormalizedField,
) -> Any:
    field_type = str(field.metadata["type"])
    if field_type == "reference" and field.target_entity:
        return _identity_annotation(model, _primary_key(model.entity(field.target_entity)))
    return {"integer": int, "decimal": Decimal}.get(field_type, str)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "unauthorized", "message": "authentication required"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bad_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"code": "invalid_request", "message": message},
    )


def _precondition_required(message: str) -> HTTPException:
    return HTTPException(
        status_code=428,
        detail={"code": "precondition_required", "message": message},
    )


def _runtime_status(error: TideRuntimeError) -> int:
    if isinstance(error, AuthorizationError):
        return 403
    if isinstance(error, NotFoundError):
        return 404
    if isinstance(error, ConcurrencyError):
        return 412
    if isinstance(error, VersionPreconditionRequired):
        return 428
    if isinstance(error, ValidationFailed):
        return 422
    if isinstance(
        error,
        (ActionDisabled, DeleteRestricted, IdempotencyConflict, ImmutableFieldError),
    ):
        return 409
    if isinstance(error, InvalidQueryCursor):
        return 400
    return 400


def _documented_errors(*statuses: int) -> dict[int, dict[str, Any]]:
    descriptions = {
        400: "Invalid query, identity, or cursor",
        401: "Authentication required",
        403: "Operation not permitted",
        404: "Record not found",
        408: "Request body was not received within the configured timeout",
        409: "Mutation conflict or disabled action",
        412: "Observed version does not match",
        413: "Request body exceeds the configured limit",
        422: "Request validation failed",
        428: "Required mutation precondition is missing",
    }
    return {
        status: {
            "model": TideApiError,
            "description": descriptions[status],
        }
        for status in statuses
    }


def _required_version(
    entity: NormalizedEntity,
    if_match: str | None,
) -> int | None:
    version_field = _version_field(entity)
    if version_field is None:
        return None
    if if_match is None:
        raise _precondition_required("If-Match header is required")
    match = re.fullmatch(r'"(\d+)"', if_match.strip())
    if match is None:
        raise _bad_request('If-Match must be a strong integer ETag such as "3"')
    return int(match.group(1))


def _bind_expected_version(session: Any, expected: int | None) -> None:
    if expected is None:
        return
    if session.expected_version != expected:
        raise ConcurrencyError(expected, session.expected_version)
    session.expected_version = expected


def _set_etag(
    response: Response,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
) -> None:
    version_field = _version_field(entity)
    if version_field is not None and values.get(version_field.name) is not None:
        response.headers["ETag"] = f'"{int(values[version_field.name])}"'


def _version_field(entity: NormalizedEntity) -> NormalizedField | None:
    return next(
        (
            field
            for field in entity.fields.values()
            if field.metadata.get("concurrency_token")
        ),
        None,
    )


def _operation_allowed(
    records: RecordsService,
    entity: NormalizedEntity,
    operation: str,
    context: RequestContext,
) -> bool:
    security_operation = "read" if operation == "get" else operation
    if not records.security.can_access_entity(entity, security_operation, context):
        return False
    return not (
        operation == "update"
        and not records.security.can_access_entity(entity, "read", context)
    )


def _nested_operation_allowed(
    records: RecordsService,
    exposures: Mapping[str, Any],
    target_name: str,
    operation: str,
    context: RequestContext,
) -> bool:
    target = records.model.entity(target_name)
    if not _operation_allowed(records, target, operation, context):
        return False
    for parent_name, exposure in exposures.items():
        if operation not in exposure.operations:
            continue
        parent = records.model.entity(parent_name)
        if not _operation_allowed(records, parent, operation, context):
            continue
        for field_name, field in parent.fields.items():
            if (
                field.metadata["type"] == "collection"
                and field.target_entity == target_name
                and operation in field.metadata.get("cascade", ())
                and records.security.can_write_field(
                    parent_name,
                    field_name,
                    context,
                )
            ):
                return True
    return False
