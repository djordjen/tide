"""FastAPI transport adapter over TIDE application services."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
import secrets
from typing import Any, Mapping, Protocol

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
from pydantic import BaseModel, ConfigDict, Field, create_model

from tide.api.contracts import (
    TIDE_WIRE_VERSION,
    TideEntityCapabilities,
    TideQueryInput,
    TideReferenceSelectionInput,
    TideReferenceSelectionResult,
    TideReportDocument,
    TideSessionInfo,
)
from tide.api.openapi import (
    DEFAULT_BASE_PATH,
    REST_OPERATIONS,
    TideApiError,
    build_openapi_preview,
    rest_exposures,
    writable_scalar_annotation,
)
from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.data import FilterCondition, QuerySpec, SortField
from tide.runtime import (
    AuthorizationError,
    ActionDisabled,
    Channel,
    ConcurrencyError,
    IdempotencyConflict,
    ImmutableFieldError,
    InvalidQueryCursor,
    NotFoundError,
    Principal,
    RequestContext,
    TideRuntimeError,
    ValidationFailed,
)
from tide.reporting import ReportService
from tide.security import PROTECTED
from tide.services import ActionService, RecordsService


SERVER_OPERATIONS = REST_OPERATIONS - {"delete"}


class TideEmptyActionPayload(BaseModel):
    """Current action metadata declares no request payload fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class BearerAuthenticator(Protocol):
    """Map a bearer credential to a server-controlled principal."""

    def authenticate(self, credential: str) -> Principal | None: ...


@dataclass(frozen=True, slots=True)
class DevelopmentTokenAuthenticator:
    """Single-token identity adapter for local development only."""

    token: str
    principal: Principal

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
    authenticator: BearerAuthenticator
    base_path: str


def build_fastapi_app(
    model: ApplicationModel,
    records: RecordsService,
    authenticator: BearerAuthenticator,
    *,
    actions: ActionService | None = None,
    reports: ReportService | None = None,
    base_path: str = DEFAULT_BASE_PATH,
) -> FastAPI:
    """Build an HTTP adapter over services without granting client database access."""

    preview = build_openapi_preview(model, base_path=base_path)
    exposures = rest_exposures(model, allowed_operations=SERVER_OPERATIONS)
    action_service = actions or ActionService(model, records)
    report_service = reports or ReportService(model, records)
    create_models, update_models = _build_writable_models(model, exposures)
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
        authenticator,
        base_path,
    )
    bearer = HTTPBearer(
        bearerFormat="opaque",
        scheme_name="bearerAuth",
        description=(
            "Bearer credentials are mapped to a Principal by server configuration; "
            "clients cannot choose their roles or permissions."
        ),
        auto_error=False,
    )

    def request_context(
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> RequestContext:
        if credentials is None or credentials.scheme.casefold() != "bearer":
            raise _unauthorized()
        principal = authenticator.authenticate(credentials.credentials)
        if principal is None:
            raise _unauthorized()
        return RequestContext(principal=principal, channel=Channel.REST)

    @app.middleware("http")
    async def secured_response_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

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
        include_in_schema=True,
    )
    def ready() -> dict[str, str]:
        return {
            "status": "ready",
            "application": model.name,
            "version": model.version,
        }

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
                for operation in ("list", "get", "create", "update")
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
                    if _field_is_api_writable(field, "update")
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
            )
        return TideSessionInfo(
            application=model.name,
            application_version=model.version,
            schema_version=model.schema_version,
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
        responses=_documented_errors(400, 401, 403, 409, 422),
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
                responses=_documented_errors(400, 401, 403, 422),
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
                responses=_documented_errors(400, 401, 403, 409, 422),
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
                responses=_documented_errors(400, 401, 403, 404, 409, 412, 422, 428),
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
                    400, 401, 403, 404, 409, 412, 422, 428
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
            "authentication": "development-bearer",
        }
        return schema

    app.openapi = tide_openapi  # type: ignore[method-assign]
    return app


def _build_writable_models(
    model: ApplicationModel,
    exposures: Mapping[str, Any],
) -> tuple[dict[str, type[BaseModel]], dict[str, type[BaseModel]]]:
    create_models: dict[str, type[BaseModel]] = {}
    update_models: dict[str, type[BaseModel]] = {}
    nested_cache: dict[tuple[str, str], type[BaseModel]] = {}

    def nested_model(
        entity: NormalizedEntity,
        *,
        excluded_field: str | None,
        stack: frozenset[str],
    ) -> type[BaseModel]:
        cache_key = entity.name, excluded_field or ""
        cached = nested_cache.get(cache_key)
        if cached is not None:
            return cached
        if entity.name in stack:
            raise ValueError(
                f"writable collection cascade contains a cycle through {entity.name}"
            )
        fields = _writable_fields(
            model,
            entity,
            mode="nested",
            excluded_field=excluded_field,
            nested_factory=lambda target, inverse: nested_model(
                target,
                excluded_field=inverse,
                stack=stack | {entity.name},
            ),
        )
        generated = create_model(
            _component_name(entity.name, "NestedInput"),
            __config__=_input_model_config(),
            __module__=__name__,
            **fields,
        )
        nested_cache[cache_key] = generated
        return generated

    for entity_name, exposure in exposures.items():
        entity = model.entity(entity_name)
        if "create" in exposure.operations:
            create_models[entity_name] = create_model(
                _component_name(entity_name, "CreateInput"),
                __config__=_input_model_config(),
                __module__=__name__,
                **_writable_fields(
                    model,
                    entity,
                    mode="create",
                    nested_factory=lambda target, inverse: nested_model(
                        target,
                        excluded_field=inverse,
                        stack=frozenset({entity.name}),
                    ),
                ),
            )
        if "update" in exposure.operations:
            update_models[entity_name] = create_model(
                _component_name(entity_name, "UpdateInput"),
                __config__=_input_model_config(),
                __module__=__name__,
                **_writable_fields(
                    model,
                    entity,
                    mode="update",
                    nested_factory=lambda target, inverse: nested_model(
                        target,
                        excluded_field=inverse,
                        stack=frozenset({entity.name}),
                    ),
                ),
            )
    return create_models, update_models


def _writable_fields(
    model: ApplicationModel,
    entity: NormalizedEntity,
    *,
    mode: str,
    nested_factory: Any,
    excluded_field: str | None = None,
) -> dict[str, tuple[Any, Any]]:
    result: dict[str, tuple[Any, Any]] = {}
    used_names: set[str] = set()
    for index, (field_name, field) in enumerate(entity.fields.items()):
        if field_name == excluded_field or not _field_is_api_writable(field, mode):
            continue
        internal_name = _model_field_name(field_name, index, used_names)
        used_names.add(internal_name)
        metadata = field.metadata
        if metadata["type"] == "collection":
            if not field.target_entity:
                raise ValueError(f"collection field {entity.name}.{field_name} has no target")
            inverse = metadata.get("inverse")
            item_model = nested_factory(model.entity(field.target_entity), inverse)
            annotation: Any = list[item_model]
        else:
            annotation = writable_scalar_annotation(model, field)

        required = (
            mode in {"create", "nested"}
            and bool(metadata.get("required"))
            and "default" not in metadata
            and "default_factory" not in metadata
            and not metadata.get("primary_key")
        )
        default: Any = ... if required else None
        if not required:
            annotation = annotation | None
        result[internal_name] = (
            annotation,
            Field(
                default,
                alias=field_name,
                title=str(metadata.get("label") or _humanize(field_name)),
                description=_input_field_description(field, mode),
            ),
        )
    return result


def _field_is_api_writable(field: NormalizedField, mode: str) -> bool:
    metadata = field.metadata
    if metadata.get("computed") or metadata.get("readonly"):
        return False
    if metadata.get("write", "normal") != "normal":
        return False
    if metadata.get("primary_key"):
        return mode == "nested"
    if metadata["type"] == "collection":
        required_cascade = "create" if mode in {"create", "nested"} else "update"
        return required_cascade in metadata.get("cascade", ())
    return True


def _input_model_config() -> ConfigDict:
    return ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        regex_engine="python-re",
    )


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


def _wire_record(
    model: ApplicationModel,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    protected: list[str] = []
    for field_name, field in entity.fields.items():
        value = values.get(field_name)
        if value is PROTECTED:
            result[field_name] = None
            protected.append(field_name)
        elif field.metadata["type"] == "collection" and field.target_entity:
            target = model.entity(field.target_entity)
            result[field_name] = [
                _wire_record(model, target, child)
                for child in (value or ())
            ]
        else:
            result[field_name] = value
    if protected:
        result["_tide"] = {"protected_fields": protected}
    return result


def _coerce_identity(
    model: ApplicationModel,
    field: NormalizedField,
    value: Any,
) -> Any:
    field_type = str(field.metadata["type"])
    if field_type == "reference" and field.target_entity:
        return _coerce_identity(model, _primary_key(model.entity(field.target_entity)), value)
    if field_type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if not isinstance(value, str):
            raise ValueError
        if not value or value.strip() != value:
            raise ValueError
        return int(value)
    if field_type in {"string", "choice"}:
        return str(value)
    if field_type == "decimal":
        return value if isinstance(value, Decimal) else Decimal(str(value))
    raise TypeError


def _decode_filter_value(
    model: ApplicationModel,
    entity: NormalizedEntity,
    field_name: str,
    value: Any,
) -> Any:
    if field_name not in entity.fields:
        raise ValueError(f"unknown query field {field_name!r}")
    return _decode_wire_value(model, entity.field(field_name), value)


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


def _decode_wire_value(
    model: ApplicationModel,
    field: NormalizedField,
    value: Any,
) -> Any:
    if value is None:
        return None
    field_type = str(field.metadata["type"])
    if field_type == "reference":
        if field.target_entity is None:
            raise TypeError
        return _decode_wire_value(
            model,
            _primary_key(model.entity(field.target_entity)),
            value,
        )
    if field_type == "collection":
        if field.target_entity is None or not isinstance(value, list):
            raise TypeError
        target = model.entity(field.target_entity)
        if not all(isinstance(item, Mapping) for item in value):
            raise TypeError
        return [_decode_draft(model, target, item) for item in value]
    if field_type == "decimal":
        if not isinstance(value, str):
            raise TypeError
        return Decimal(value)
    if field_type == "date":
        if not isinstance(value, str):
            raise TypeError
        from datetime import date

        return date.fromisoformat(value)
    if field_type == "datetime":
        if not isinstance(value, str):
            raise TypeError
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if field_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError
        return value
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise TypeError
        return value
    if field_type in {"string", "choice"}:
        if not isinstance(value, str):
            raise TypeError
        return value
    raise TypeError


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


def _primary_key(entity: NormalizedEntity) -> NormalizedField:
    return next(
        field for field in entity.fields.values() if field.metadata.get("primary_key")
    )


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
    if isinstance(error, ValidationFailed):
        return 422
    if isinstance(
        error,
        (ActionDisabled, IdempotencyConflict, ImmutableFieldError),
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
        409: "Mutation conflict or disabled action",
        412: "Observed version does not match",
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


def _input_field_description(field: NormalizedField, mode: str) -> str | None:
    help_text = field.metadata.get("help")
    if help_text:
        return str(help_text)
    if field.metadata.get("primary_key") and mode == "nested":
        return "Existing child identity; omit for a new child."
    if field.metadata["type"] == "collection":
        return "Complete replacement for this writable child collection."
    return None


def _component_name(entity_name: str, suffix: str) -> str:
    return "".join(_pascal_case(part) for part in entity_name.split(".")) + suffix


def _model_field_name(
    source_name: str,
    index: int,
    used_names: set[str],
) -> str:
    candidate = source_name
    if (
        not candidate.isidentifier()
        or candidate.startswith("_")
        or hasattr(BaseModel, candidate)
        or candidate in used_names
    ):
        candidate = f"tide_field_{index}"
    while candidate in used_names:
        index += 1
        candidate = f"tide_field_{index}"
    return candidate


def _pascal_case(value: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", value) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _humanize(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ").title()
