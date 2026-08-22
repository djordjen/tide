"""FastAPI transport adapter over TIDE application services."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
from decimal import Decimal, InvalidOperation
import logging
from pathlib import Path as FileSystemPath
import re
import secrets
from time import perf_counter
from typing import Any, Callable, Literal, Mapping, Protocol, cast
from urllib.parse import quote

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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict
from starlette.staticfiles import StaticFiles

from tide.api.contracts import (
    TIDE_WIRE_VERSION,
    TideAuditHistory,
    TideBrowserAuthenticationInfo,
    TideBrowserSessionInfo,
    TideCreateLocalUserInput,
    TideDistinctInput,
    TideDistinctResult,
    TideDistinctValue,
    TideEntityCapabilities,
    TideLocalPasswordInput,
    TideLocalUser,
    TideLocalUserList,
    TidePresentationManifest,
    TidePasswordLoginInput,
    TideQueryInput,
    TideReferenceSelectionInput,
    TideReferenceSelectionResult,
    TideReportDocument,
    TideRoleCatalogue,
    TideRoleGrants,
    TideSessionInfo,
    TideUpdateLocalUserInput,
)
from tide.api.config import (
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
)
from tide.api.development_auth import is_loopback_host_header
from tide.api.inputs import build_writable_models, field_is_writable
from tide.api.administration import (
    AdministrationConflict,
    AdministrationDenied,
    AdministrationError,
    UnknownLocalUser,
    UserAdministration,
)
from tide.api.local_auth import (
    LocalAuthenticationBusy,
    LocalPasswordAuth,
    LocalUserSummary,
)
from tide.api.openapi import (
    DEFAULT_BASE_PATH,
    REST_OPERATIONS,
    TideApiError,
    TideApiValidationIssue,
    build_openapi_preview,
    rest_exposures,
)
from tide.api.presentation import build_presentation_manifest
from tide.reporting.browse import EXPORT as EXPORT_PERMISSION
from tide.reporting.xlsx import SPREADSHEET_AVAILABLE
from tide.api.wire import (
    coerce_identity as _coerce_identity,
    decode_filter_value as _decode_filter_value,
    decode_wire_value as _decode_wire_value,
    primary_key as _primary_key,
    wire_audit_event as _wire_audit_event,
    wire_record as _wire_record,
)
from tide.compiler.normalized import ApplicationModel, NormalizedEntity, NormalizedField
from tide.data import FilterCondition, QuerySpec, SortField, SummaryRequest
from tide.observability import (
    CORRELATION_HEADER,
    bind_correlation_id,
    log_runtime_event,
    reset_correlation_id,
    resolve_correlation_id,
)
from tide.presentation import (
    action_state,
    field_is_immutable,
    record_appearance,
    RecordAppearance,
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
    QueryFieldError,
    RequestContext,
    TideRuntimeError,
    ValidationFailed,
    VersionPreconditionRequired,
)
from tide.reporting import (
    PdfDependencyMissing,
    ReportDocument,
    ReportService,
    render_csv,
    render_html,
    render_pdf,
)
from tide.security import PROTECTED
from tide.services import ActionService, AuditHistoryReader, AuditHistoryService, RecordsService


SERVER_OPERATIONS = REST_OPERATIONS
_RUNTIME_LOGGER = logging.getLogger("tide.runtime")
_BROWSER_CSRF_HEADER = "X-TIDE-CSRF"
_BROWSER_LOGIN_HEADER = "X-TIDE-LOGIN"

SESSION_ORIGIN_SEPARATOR = "~"
"""Separates the issuing process from the session identifier in the cookie.

Not a character `secrets.token_urlsafe` can produce, so the split is exact and
a session identifier never has to be guessed at.
"""


def encode_session_cookie(origin: str | None, session_id: str) -> str:
    """Stamp a session with its issuing process, where there is one to stamp."""

    if origin is None:
        return session_id
    return f"{origin}{SESSION_ORIGIN_SEPARATOR}{session_id}"


def split_session_cookie(value: str | None) -> tuple[str | None, str | None]:
    """Recover the issuing process and the session identifier.

    An unstamped cookie is a session identifier and nothing else: that is a
    shared deployment, and also anything issued before this existed.
    """

    if not isinstance(value, str) or not value:
        return None, None
    origin, separator, session_id = value.partition(SESSION_ORIGIN_SEPARATOR)
    if not separator:
        return None, value
    return origin, session_id



SWAGGER_UI_DIRECTORY = FileSystemPath(__file__).parent / "swagger_ui"
SWAGGER_UI_ASSETS = {
    "swagger-ui-bundle.js": "text/javascript",
    "swagger-ui.css": "text/css",
}
SWAGGER_INITIALIZER = "swagger-initializer.js"
"""Swagger UI, vendored, and the file that starts it.

`swagger_ui/PROVENANCE.md` records the version and its checksums. It is here
rather than on a CDN because TIDE sends `script-src 'self'` whenever it owns
identities, and the alternative to hosting the assets is trusting a third-party
origin on a page that carries a session cookie. Hosting them also means `/docs`
works with no network at all.
"""


def _swagger_initializer(openapi_url: str) -> str:
    return (
        "window.ui = SwaggerUIBundle({\n"
        f"  url: {json.dumps(openapi_url)},\n"
        '  dom_id: "#swagger-ui",\n'
        "  deepLinking: true,\n"
        "  showExtensions: true,\n"
        "  showCommonExtensions: true,\n"
        "  presets: [SwaggerUIBundle.presets.apis],\n"
        '  layout: "BaseLayout",\n'
        "})\n"
    )


def _swagger_ui_html(*, title: str, assets_path: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="utf-8" />\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"    <title>{title}</title>\n"
        f'    <link rel="stylesheet" href="{assets_path}/swagger-ui.css" />\n'
        "  </head>\n"
        "  <body>\n"
        '    <div id="swagger-ui"></div>\n'
        f'    <script src="{assets_path}/swagger-ui-bundle.js"></script>\n'
        f'    <script src="{assets_path}/{SWAGGER_INITIALIZER}"></script>\n'
        "  </body>\n"
        "</html>\n"
    )


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


class BrowserAuthenticator(Protocol):
    """Keep a server-owned identity behind an opaque browser session."""

    authentication_mode: Literal["oidc", "password", "development"]
    secure_cookie: bool
    session_cookie_name: str
    session_lifetime_seconds: int

    def authenticate_session(self, session_id: str | None) -> Any: ...

    def end_session(self, session_id: str | None) -> None: ...


class OidcBrowserAuthenticator(BrowserAuthenticator, Protocol):
    transaction_cookie_name: str
    transaction_lifetime_seconds: int

    def begin_login(self, *, return_to: str = "/") -> Any: ...

    def complete_login(
        self,
        *,
        state: str,
        transaction_binding: str,
        code: str,
    ) -> Any: ...


class PasswordBrowserAuthenticator(BrowserAuthenticator, Protocol):
    def login(self, *, username: str, password: str) -> Any: ...


class DevelopmentBrowserAuthenticator(BrowserAuthenticator, Protocol):
    """Starts a session for nobody in particular, on a machine-local request."""

    def begin_session(self) -> Any: ...


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
    browser_auth: BrowserAuthenticator | None
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
    web_root: str | FileSystemPath | None = None,
    browser_auth: BrowserAuthenticator | None = None,
    session_origin: str | None = None,
    docs: bool = False,
) -> FastAPI:
    """Build an HTTP adapter over services without granting client database access.

    ``docs`` serves `/docs`, `/redoc` and `/openapi.json`. It is off unless
    asked for, because that document is not a courtesy -- it is every exposed
    entity, field and action plus the `x-tide` runtime configuration, which is
    a map of the application handed out before anyone authenticates. Defaulting
    it off means a caller that never considers the question is not publishing
    one, and `tide serve` turns it on only where nothing beyond the machine
    can reach the server -- a loopback bind with no declared proxy in front.

    Withheld rather than gated: a bearer token is not what makes publishing the
    model surface acceptable, and Swagger UI is a browser page that would have
    to carry credentials of its own to fetch a protected schema.
    """

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
    web_directory: FileSystemPath | None = None
    if web_root is not None:
        web_directory = FileSystemPath(web_root).resolve()
        if not web_directory.is_dir():
            raise ValueError(
                f"Web build directory does not exist: {web_directory}"
            )
        if not (web_directory / "index.html").is_file():
            raise ValueError(
                f"Web build directory has no index.html: {web_directory}"
            )
    if browser_auth is not None and browser_auth.authentication_mode not in {
        "oidc",
        "password",
        "development",
    }:
        raise ValueError("browser authentication mode is unsupported")
    if (
        browser_auth is not None
        and browser_auth.authentication_mode == "development"
        and getattr(authenticator, "production", False)
    ):
        # A session nobody has to prove anything for cannot be attached to an
        # adapter that was chosen because it is production-grade. The mode is
        # already refused off loopback by `tide serve`; this is the fence for
        # everything that builds an app without going through the CLI, and it
        # is structural because a "development only" flag reaches production
        # exactly by being possible.
        raise ValueError(
            "a development browser session may not be combined with a "
            "production identity adapter"
        )
    development_browser_session = (
        browser_auth is not None
        and browser_auth.authentication_mode == "development"
    )
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
        # `/docs` is served below rather than by FastAPI, which points Swagger
        # UI at a CDN and initialises it from an inline script -- neither of
        # which survives the `script-src 'self'` this sends whenever it owns
        # identities. `/redoc` is left as FastAPI builds it and is therefore
        # still CDN-dependent; see the decision log.
        docs_url=None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.tide = TideApiRuntime(
        model,
        records,
        action_service,
        report_service,
        audit_service,
        authenticator,
        browser_auth,
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
        if credentials is not None:
            if credentials.scheme.casefold() != "bearer":
                raise _unauthorized()
            principal = authenticator.authenticate(credentials.credentials)
            if principal is None:
                raise _unauthorized()
        else:
            access = _browser_session_access(request, browser_auth, session_origin)
            principal = access.principal
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                supplied_csrf = request.headers.get(_BROWSER_CSRF_HEADER)
                if (
                    not isinstance(supplied_csrf, str)
                    or not secrets.compare_digest(supplied_csrf, access.csrf_token)
                ):
                    raise _csrf_failed()
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
                if development_browser_session and not is_loopback_host_header(
                    request.headers.get("host")
                ):
                    # DNS rebinding is the one attacker the rest of this
                    # arrangement cannot see. Binding to 127.0.0.1 does not stop
                    # a browser whose attacker-controlled domain now resolves
                    # there, and no CORS header helps either, because to that
                    # browser the page is same-origin. What the request still
                    # carries is the name it asked for, and that name is not
                    # this machine's.
                    request.state.tide_operation = "nonLoopbackHost"
                    response = _non_loopback_host()
                else:
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
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
                if web_directory is not None
                and request.url.path.startswith("/assets/")
                else "no-store"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            if browser_auth is not None:
                response.headers["Referrer-Policy"] = "no-referrer"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; "
                    "script-src 'self'; "
                    # React writes element style attributes, which style-src
                    # governs; the built stylesheet itself is same-origin.
                    "style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data:; "
                    "font-src 'self'; "
                    "connect-src 'self'; "
                    "object-src 'none'; "
                    "form-action 'self'; "
                    "frame-ancestors 'none'; "
                    "base-uri 'self'"
                )
                if request.url.scheme == "https":
                    # Only over TLS. Sent on a plain-HTTP loopback response it
                    # is ignored by browsers at best, and at worst pins a
                    # developer's machine to a scheme it is not serving.
                    response.headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains"
                    )
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
        issues = (
            tuple(
                TideApiValidationIssue(
                    rule=issue.rule,
                    message=issue.message,
                    fields=issue.fields,
                    severity=issue.severity,
                )
                for issue in error.issues
            )
            if isinstance(error, ValidationFailed)
            else ()
        )
        return JSONResponse(
            status_code=status,
            content=TideApiError(
                code=error.code,
                message=str(error),
                issues=issues,
            ).model_dump(),
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
        error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=TideApiError(
                code="invalid_request",
                message="request validation failed",
                issues=tuple(
                    TideApiValidationIssue(
                        rule=str(item.get("type", "invalid")),
                        message=str(item.get("msg", "invalid value")),
                        fields=(
                            tuple(
                                str(part)
                                for part in tuple(item.get("loc", ()))[1:]
                                if not isinstance(part, int)
                            )
                            if tuple(item.get("loc", ()))[:1] == ("body",)
                            else ()
                        ),
                    )
                    for item in error.errors()
                ),
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

    if docs:
        assets_path = f"{base_path.rstrip('/')}/_tide/docs-assets"

        @app.get(
            f"{assets_path}/{{asset}}",
            include_in_schema=False,
        )
        def swagger_ui_asset(asset: str) -> Response:
            """Serve Swagger UI from TIDE rather than from a CDN.

            Registered only when the description is, so the assets are
            withheld with the document they draw. Same-origin is the whole
            point: it is what lets the page work under the security headers
            the renderer needs, instead of asking for an exception to them.
            """

            if asset == SWAGGER_INITIALIZER:
                # Generated rather than shipped, so it cannot name a schema URL
                # this application does not serve. A file and not an inline
                # block, because `'self'` covers a file and covering a block
                # would need `unsafe-inline` or a hash of a string FastAPI owns.
                return Response(
                    content=_swagger_initializer(app.openapi_url or ""),
                    media_type="text/javascript",
                )
            if asset not in SWAGGER_UI_ASSETS:
                raise HTTPException(status_code=404, detail={"error": "not_found"})
            return FileResponse(
                SWAGGER_UI_DIRECTORY / asset,
                media_type=SWAGGER_UI_ASSETS[asset],
            )

        @app.get("/docs", include_in_schema=False)
        def swagger_ui_page() -> Response:
            return Response(
                content=_swagger_ui_html(
                    title=f"{model.name} API",
                    assets_path=assets_path,
                ),
                media_type="text/html",
            )

    browser_auth_path = f"{base_path.rstrip('/')}/_tide/browser-auth"
    browser_login_path = f"{browser_auth_path}/login"
    browser_callback_path = f"{browser_auth_path}/callback"
    browser_session_path = f"{browser_auth_path}/session"
    browser_logout_path = f"{browser_auth_path}/logout"

    @app.get(
        browser_auth_path,
        tags=["TIDE"],
        summary="Browser authentication discovery",
        response_model=TideBrowserAuthenticationInfo,
    )
    def browser_authentication_info() -> TideBrowserAuthenticationInfo:
        return TideBrowserAuthenticationInfo(
            enabled=browser_auth is not None,
            mode=(
                browser_auth.authentication_mode
                if browser_auth is not None
                else None
            ),
            login_path=browser_login_path if browser_auth is not None else None,
            session_path=(
                browser_session_path if browser_auth is not None else None
            ),
            logout_path=browser_logout_path if browser_auth is not None else None,
        )

    if browser_auth is not None and browser_auth.authentication_mode == "oidc":
        oidc_browser_auth = cast(OidcBrowserAuthenticator, browser_auth)

        @app.get(
            browser_login_path,
            tags=["TIDE"],
            summary="Start browser sign-in",
            include_in_schema=False,
        )
        def browser_login(return_to: str = Query(default="/")) -> RedirectResponse:
            try:
                login = oidc_browser_auth.begin_login(return_to=return_to)
            except ValueError as error:
                raise _bad_request("browser login request is invalid") from error
            response = RedirectResponse(login.authorization_url, status_code=302)
            response.set_cookie(
                oidc_browser_auth.transaction_cookie_name,
                login.transaction_binding,
                max_age=oidc_browser_auth.transaction_lifetime_seconds,
                httponly=True,
                secure=oidc_browser_auth.secure_cookie,
                samesite="lax",
                path="/",
            )
            return response

        @app.get(
            browser_callback_path,
            tags=["TIDE"],
            summary="Complete browser sign-in",
            include_in_schema=False,
        )
        def browser_callback(
            request: Request,
            code: str | None = Query(default=None),
            state: str | None = Query(default=None),
            error: str | None = Query(default=None),
        ) -> RedirectResponse:
            transaction_binding = request.cookies.get(
                oidc_browser_auth.transaction_cookie_name
            )
            if error is not None or code is None or state is None:
                return _browser_login_error_redirect(oidc_browser_auth)
            try:
                result = oidc_browser_auth.complete_login(
                    state=state,
                    transaction_binding=transaction_binding or "",
                    code=code,
                )
            except ValueError:
                return _browser_login_error_redirect(oidc_browser_auth)
            response = RedirectResponse(result.return_to, status_code=303)
            response.delete_cookie(
                oidc_browser_auth.transaction_cookie_name,
                path="/",
                secure=oidc_browser_auth.secure_cookie,
                httponly=True,
                samesite="lax",
            )
            response.set_cookie(
                oidc_browser_auth.session_cookie_name,
                encode_session_cookie(session_origin, result.session_id),
                max_age=oidc_browser_auth.session_lifetime_seconds,
                httponly=True,
                secure=oidc_browser_auth.secure_cookie,
                samesite="strict",
                path="/",
            )
            return response

    if browser_auth is not None and browser_auth.authentication_mode == "password":
        password_browser_auth = cast(PasswordBrowserAuthenticator, browser_auth)

        @app.post(
            browser_login_path,
            tags=["TIDE"],
            summary="Sign in with a local username and password",
            response_model=TideBrowserSessionInfo,
            responses=_documented_errors(401, 503),
        )
        def browser_password_login(
            request: Request,
            credentials: TidePasswordLoginInput,
        ) -> Response:
            if request.headers.get(_BROWSER_LOGIN_HEADER) != "password":
                raise _bad_request("local login request is invalid")
            try:
                result = password_browser_auth.login(
                    username=credentials.username,
                    password=credentials.password,
                )
            except LocalAuthenticationBusy as error:
                # Capacity, not credentials. Telling a legitimate user their
                # password is wrong would send them to reset it.
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "login_capacity_exhausted",
                        "message": "too many sign-in attempts in progress",
                    },
                    headers={"Retry-After": "1"},
                ) from error
            except ValueError as error:
                raise _unauthorized() from error
            response = JSONResponse(
                TideBrowserSessionInfo(
                    csrf_token=result.csrf_token
                ).model_dump()
            )
            response.set_cookie(
                password_browser_auth.session_cookie_name,
                encode_session_cookie(session_origin, result.session_id),
                max_age=password_browser_auth.session_lifetime_seconds,
                httponly=True,
                secure=password_browser_auth.secure_cookie,
                samesite="strict",
                path="/",
            )
            return response

    if development_browser_session:
        development_browser_auth = cast(
            DevelopmentBrowserAuthenticator, browser_auth
        )

        @app.post(
            browser_login_path,
            tags=["TIDE"],
            summary="Start a local development session without a credential",
            response_model=TideBrowserSessionInfo,
            responses=_documented_errors(400, 403),
        )
        def browser_development_login(request: Request) -> Response:
            # The same custom-header requirement the password flow uses. It
            # proves nothing about who is asking -- nothing here does -- but a
            # header a form post cannot set is what keeps a cross-site page
            # from starting a session in a developer's browser without any
            # script running at all.
            if request.headers.get(_BROWSER_LOGIN_HEADER) != "development":
                raise _bad_request("development login request is invalid")
            result = development_browser_auth.begin_session()
            response = JSONResponse(
                TideBrowserSessionInfo(csrf_token=result.csrf_token).model_dump()
            )
            response.set_cookie(
                development_browser_auth.session_cookie_name,
                encode_session_cookie(session_origin, result.session_id),
                max_age=development_browser_auth.session_lifetime_seconds,
                httponly=True,
                secure=development_browser_auth.secure_cookie,
                samesite="strict",
                path="/",
            )
            return response

    if browser_auth is not None:

        @app.get(
            browser_session_path,
            tags=["TIDE"],
            summary="Current browser session",
            response_model=TideBrowserSessionInfo,
            responses={
                204: {"description": "This browser has no session"},
                **_documented_errors(401),
            },
        )
        def browser_session(request: Request) -> Any:
            # No cookie is an answer, not a refusal. Every cold load of the Web
            # UI asks this endpoint whether the browser is already signed in,
            # and answering 401 put a red line in the console of a page where
            # nothing had gone wrong and nobody had tried anything. 401 is kept
            # for a cookie that was presented and rejected, which is a real
            # failure and worth seeing -- and which was indistinguishable from
            # this one until now, to the console and to the client alike.
            if request.cookies.get(browser_auth.session_cookie_name) is None:
                return Response(status_code=204)
            access = _browser_session_access(request, browser_auth, session_origin)
            return TideBrowserSessionInfo(csrf_token=access.csrf_token)

        @app.post(
            browser_logout_path,
            tags=["TIDE"],
            summary="End current browser session",
            status_code=204,
            responses=_documented_errors(401, 403),
        )
        def browser_logout(request: Request) -> Response:
            access = _browser_session_access(request, browser_auth, session_origin)
            supplied_csrf = request.headers.get(_BROWSER_CSRF_HEADER)
            if (
                not isinstance(supplied_csrf, str)
                or not secrets.compare_digest(supplied_csrf, access.csrf_token)
            ):
                raise _csrf_failed()
            _, session_id = split_session_cookie(
                request.cookies.get(browser_auth.session_cookie_name)
            )
            browser_auth.end_session(session_id)
            response = Response(status_code=204)
            response.delete_cookie(
                browser_auth.session_cookie_name,
                path="/",
                secure=browser_auth.secure_cookie,
                httponly=True,
                samesite="strict",
            )
            return response

    # Administering identities exists exactly where TIDE owns them. The store
    # is the password authenticator's, rather than a second thing to configure:
    # one identity file, named once, and no way for the two to disagree about
    # which accounts exist.
    administration = (
        UserAdministration(browser_auth.store, model, records.security)
        if isinstance(browser_auth, LocalPasswordAuth)
        else None
    )
    administration_path = f"{base_path.rstrip('/')}/_tide/administration"

    if administration is not None:

        def _log_administration(
            event: str,
            context: RequestContext,
            *,
            subject: str,
            operation: str,
        ) -> None:
            log_runtime_event(
                runtime_logger,
                logging.INFO,
                event,
                channel=Channel.REST.value,
                correlation_id=context.correlation_id,
                operation=operation,
                principal=context.principal.identifier,
                subject=subject,
            )

        @app.get(
            f"{administration_path}/roles",
            tags=["TIDE"],
            summary="Compiled roles and what they grant",
            response_model=TideRoleCatalogue,
            responses=_documented_errors(401, 403),
        )
        def administration_roles(
            context: RequestContext = Depends(request_context),
        ) -> TideRoleCatalogue:
            try:
                roles = administration.roles(context)
            except AdministrationError as error:
                raise _administration_refused(error) from error
            return TideRoleCatalogue(
                roles=tuple(
                    TideRoleGrants(name=role.name, grants=role.grants)
                    for role in roles
                )
            )

        @app.get(
            f"{administration_path}/users",
            tags=["TIDE"],
            summary="Accounts this application owns",
            response_model=TideLocalUserList,
            responses=_documented_errors(401, 403),
        )
        def administration_users(
            context: RequestContext = Depends(request_context),
        ) -> TideLocalUserList:
            try:
                listing = administration.list_users(context)
            except AdministrationError as error:
                raise _administration_refused(error) from error
            return TideLocalUserList(
                users=tuple(_local_user(user) for user in listing.users),
                truncated=listing.truncated,
            )

        @app.post(
            f"{administration_path}/users",
            tags=["TIDE"],
            summary="Create an account",
            response_model=TideLocalUser,
            status_code=201,
            responses=_documented_errors(400, 401, 403, 409),
        )
        def administration_create_user(
            payload: TideCreateLocalUserInput,
            context: RequestContext = Depends(request_context),
        ) -> TideLocalUser:
            try:
                created = administration.create_user(
                    context,
                    username=payload.username,
                    password=payload.password,
                    roles=payload.roles,
                    display_name=payload.display_name,
                )
            except AdministrationError as error:
                raise _administration_refused(error) from error
            _log_administration(
                "administration.user_created",
                context,
                subject=created.username,
                operation="create_user",
            )
            return _local_user(created)

        @app.patch(
            f"{administration_path}/users/{{username}}",
            tags=["TIDE"],
            summary="Change an account's roles or whether it may sign in",
            response_model=TideLocalUser,
            responses=_documented_errors(400, 401, 403, 404, 409),
        )
        def administration_update_user(
            username: str,
            payload: TideUpdateLocalUserInput,
            context: RequestContext = Depends(request_context),
        ) -> TideLocalUser:
            if payload.roles is None and payload.enabled is None:
                raise _bad_request(
                    "an account update must change roles or enabled"
                )
            try:
                changed = administration.user(context, username)
                if payload.roles is not None:
                    changed = administration.set_roles(
                        context, username, payload.roles
                    )
                if payload.enabled is not None:
                    changed = administration.set_enabled(
                        context, username, payload.enabled
                    )
            except AdministrationError as error:
                raise _administration_refused(error) from error
            _log_administration(
                "administration.user_updated",
                context,
                subject=changed.username,
                operation="update_user",
            )
            return _local_user(changed)

        @app.post(
            f"{administration_path}/users/{{username}}/password",
            tags=["TIDE"],
            summary="Replace an account's password",
            status_code=204,
            responses=_documented_errors(400, 401, 403, 404),
        )
        def administration_reset_password(
            username: str,
            payload: TideLocalPasswordInput,
            context: RequestContext = Depends(request_context),
        ) -> Response:
            try:
                subject = administration.user(context, username)
                administration.set_password(context, username, payload.password)
            except AdministrationError as error:
                raise _administration_refused(error) from error
            _log_administration(
                "administration.password_reset",
                context,
                subject=subject.username,
                operation="reset_password",
            )
            return Response(status_code=204)

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
            administration=(
                administration is not None
                and administration.can_administer(context)
            ),
        )

    @app.get(
        f"{base_path.rstrip('/')}/_tide/presentation",
        tags=["TIDE"],
        summary="Secured application presentation manifest",
        response_model=TidePresentationManifest,
        responses=_documented_errors(401),
    )
    def presentation_manifest(
        context: RequestContext = Depends(request_context),
    ) -> TidePresentationManifest:
        # Filtered twice: by what this principal may do, and by what this
        # process can actually write. Offering a format the server has no
        # writer for would be offering a 503.
        export_formats: tuple[str, ...] = ()
        if EXPORT_PERMISSION in records.security.effective_permissions(
            context.principal
        ):
            export_formats = ("csv", "xlsx") if SPREADSHEET_AVAILABLE else ("csv",)
        return build_presentation_manifest(
            model,
            session_info(context),
            exposures,
            base_path=base_path,
            export_formats=export_formats,
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

    def build_summary_document(
        report_name: str,
        parameters: Mapping[str, Any],
        context: RequestContext,
    ) -> ReportDocument:
        report = model.reports.get(report_name)
        if (
            report is None
            or report.get("kind", "record") != "summary"
            or report.get("expose", {}).get("rest") is not True
        ):
            raise NotFoundError(f"report {report_name!r} was not found")
        return report_service.build(report_name, parameters, context)

    def build_record_document(
        report_name: str,
        identity: str,
        context: RequestContext,
    ) -> ReportDocument:
        report = model.reports.get(report_name)
        if (
            report is None
            or report.get("kind", "record") != "record"
            or report.get("expose", {}).get("rest") is not True
        ):
            raise NotFoundError(f"report {report_name!r} was not found")
        entity = model.entity(str(report["entity"]))
        primary_key = _primary_key(entity)
        try:
            typed_identity = _coerce_identity(model, primary_key, identity)
        except (TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request("record identity has an invalid type") from error
        return report_service.build_for_record(
            report_name,
            typed_identity,
            context,
        )

    @app.post(
        f"{base_path.rstrip('/')}/_tide/reports/{{report_name}}",
        tags=["TIDE"],
        summary="Build one secured summary report",
        response_model=TideReportDocument,
        responses=_documented_errors(400, 401, 403, 404, 422),
    )
    def summary_report(
        context: RequestContext = Depends(request_context),
        report_name: str = Path(min_length=1),
        parameters: dict[str, Any] = Body(default_factory=dict),
    ) -> TideReportDocument:
        document = build_summary_document(report_name, parameters, context)
        return TideReportDocument.model_validate(asdict(document))

    @app.post(
        (
            f"{base_path.rstrip('/')}/_tide/reports/"
            "{report_name}/exports/{export_format}"
        ),
        tags=["TIDE"],
        summary="Export one secured summary report",
        response_class=Response,
        responses=_documented_errors(400, 401, 403, 404, 422, 503),
    )
    def summary_report_export(
        context: RequestContext = Depends(request_context),
        report_name: str = Path(min_length=1),
        export_format: Literal["csv", "html", "pdf"] = Path(),
        parameters: dict[str, Any] = Body(default_factory=dict),
    ) -> Response:
        document = build_summary_document(report_name, parameters, context)
        return _report_export_response(document, export_format)

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
        document = build_record_document(report_name, identity, context)
        return TideReportDocument.model_validate(asdict(document))

    @app.get(
        (
            f"{base_path.rstrip('/')}/_tide/reports/"
            "{report_name}/records/{identity}/exports/{export_format}"
        ),
        tags=["TIDE"],
        summary="Export one secured record report",
        response_class=Response,
        responses=_documented_errors(400, 401, 403, 404, 422, 503),
    )
    def record_report_export(
        context: RequestContext = Depends(request_context),
        report_name: str = Path(min_length=1),
        identity: str = Path(min_length=1),
        export_format: Literal["csv", "html", "pdf"] = Path(),
    ) -> Response:
        document = build_record_document(report_name, identity, context)
        return _report_export_response(document, export_format)

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
            distinct_endpoint = _distinct_endpoint(
                records,
                entity,
                request_context,
            )
            app.add_api_route(
                f"{resource_path}/_distinct",
                distinct_endpoint,
                methods=["POST"],
                response_model=TideDistinctResult,
                name=f"Distinct {entity.label}",
                operation_id=(
                    f"distinct{preview.record_models[entity_name].__name__.removesuffix('Record')}"
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
            "browser_authentication": browser_auth is not None,
            "max_request_body_bytes": max_request_body_bytes,
            "request_body_timeout_seconds": request_body_timeout_seconds,
        }
        return schema

    app.openapi = tide_openapi  # type: ignore[method-assign]
    if web_directory is not None:
        app.mount(
            "/",
            StaticFiles(directory=web_directory, html=True),
            name="tide-web",
        )
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
        return record_model.model_validate(
            _wire_record_with_state(records, entity, stored, context)
        )

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
        return record_model.model_validate(
            _wire_record_with_state(records, entity, stored, context)
        )

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
        return record_model.model_validate(
            _wire_record_with_state(
                actions.records,
                entity,
                stored,
                context,
            )
        )

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
            # The message is the service's, not the caller's: it can name
            # internal limits and fields. The correlation identifier is how
            # an operator finds the detail this deliberately withholds.
            raise _bad_request("list query parameters are invalid") from error
        return page_model.model_validate(
            {
                "records": [
                    # A page carries the appearance verdict too. A rule that
                    # only shows its colour once the record is open is not a
                    # warning; it is a reward for having already looked.
                    _apply_appearance(
                        _wire_record(
                            records.model,
                            entity,
                            record,
                            page.references,
                        ),
                        entity,
                        record,
                    )
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
                        item.operator,
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
                    summaries=tuple(
                        SummaryRequest(item.field, item.function)
                        for item in payload.summaries
                    ),
                ),
                context,
            )
        except QueryFieldError as error:
            # Composed here, so it is safe to repeat: it names a field the
            # caller sent, not an internal limit or a library's exception type.
            raise _bad_request(str(error)) from error
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request("structured query is invalid") from error
        envelope: dict[str, Any] = {
            "records": [
                # A page carries the appearance verdict too. A rule that
                # only shows its colour once the record is open is not a
                # warning; it is a reward for having already looked.
                _apply_appearance(
                    _wire_record(
                        records.model,
                        entity,
                        record,
                        page.references,
                    ),
                    entity,
                    record,
                )
                for record in page.records
            ],
            "next_cursor": page.next_cursor,
        }
        if page.summaries:
            # `_wire_value` already speaks every summary's type: a count is
            # an integer, a decimal sum a string, a date bound ISO text and
            # an empty set's answer null.
            envelope["summaries"] = [
                {
                    "field": request.field,
                    "function": request.function,
                    "value": _wire_value(
                        records.model,
                        entity.fields[request.field],
                        value,
                    ),
                }
                for request, value in page.summaries
            ]
        return page_model.model_validate(envelope)

    query_records.__name__ = f"query_{entity.name.replace('.', '_')}"
    return query_records


def _distinct_endpoint(
    records: RecordsService,
    entity: NormalizedEntity,
    context_dependency: Any,
) -> Any:
    def distinct_values(
        payload: TideDistinctInput,
        context: RequestContext = Depends(context_dependency),
    ) -> TideDistinctResult:
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
                        item.operator,
                    ),
                )
                for item in payload.filters
            )
            answer = records.distinct_values(
                entity.name,
                payload.field,
                filters,
                context,
            )
        except QueryFieldError as error:
            raise _bad_request(str(error)) from error
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise _bad_request("distinct query is invalid") from error
        field = entity.fields[payload.field]
        return TideDistinctResult(
            field=payload.field,
            values=tuple(
                TideDistinctValue(
                    value=_wire_value(records.model, field, value),
                    display=display,
                )
                for value, display in answer.values
            ),
            truncated=answer.truncated,
        )

    distinct_values.__name__ = f"distinct_{entity.name.replace('.', '_')}"
    return distinct_values


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
            _wire_record_with_state(records, entity, record, context)
        )

    get_record.__name__ = f"get_{entity.name.replace('.', '_')}"
    get_record.__annotations__["identity"] = _identity_annotation(
        records.model,
        primary_key,
    )
    return get_record


def _wire_record_with_state(
    records: RecordsService,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
    context: RequestContext,
) -> dict[str, Any]:
    projected = _wire_record(
        records.model,
        entity,
        values,
        records.reference_displays(entity.name, (values,), context),
    )
    appearance = _record_appearance(
        entity.metadata.get("appearance") or (),
        values,
    )
    writable_fields = _record_writable_fields(
        records,
        entity,
        values,
        context,
        appearance,
    )
    if writable_fields:
        projected.setdefault("_tide", {})["writable_fields"] = list(
            writable_fields
        )
    action_states = _record_action_states(
        records,
        entity,
        values,
        context,
    )
    if action_states:
        projected.setdefault("_tide", {})["actions"] = action_states
    _apply_appearance(projected, entity, values, appearance)
    return projected


def _apply_appearance(
    projected: dict[str, Any],
    entity: NormalizedEntity,
    values: Mapping[str, Any],
    resolved: RecordAppearance | None = None,
) -> dict[str, Any]:
    """Attach the entity's appearance verdict, when it has anything to say.

    Absent when no rule matched, so an application declaring none pays no
    bytes for the feature and a renderer needs no default to compare against.
    """

    appearance = resolved or _record_appearance(
        entity.metadata.get("appearance") or (),
        values,
    )
    verdict: dict[str, Any] = {}
    if appearance.record is not None:
        verdict["record"] = appearance.record
    if appearance.fields:
        verdict["fields"] = dict(appearance.fields)
    if appearance.hidden:
        verdict["hidden"] = sorted(appearance.hidden)
    # A lock is not carried here: it has already left the record through
    # `writable_fields`, which every renderer reads. Two lists saying one
    # thing is how one of them starts saying something else.
    if not verdict:
        return projected
    projected.setdefault("_tide", {})["appearance"] = verdict
    return projected


def _record_writable_fields(
    records: RecordsService,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
    context: RequestContext,
    appearance: RecordAppearance | None = None,
) -> tuple[str, ...]:
    """Return advisory field state after server-side workflow evaluation."""

    if not _operation_allowed(records, entity, "update", context):
        return ()
    if not records.security.row_allowed(
        entity.name,
        "update",
        values,
        context,
    ):
        return ()
    result: list[str] = []
    for name, field in entity.fields.items():
        if (
            not field_is_writable(field, "update")
            or not records.security.can_read_field(entity.name, name, context)
            or not records.security.can_write_field(entity.name, name, context)
        ):
            continue
        if _field_is_immutable(field, values, appearance):
            continue
        result.append(name)
    return tuple(result)



_action_state = action_state
_field_is_immutable = field_is_immutable
_record_appearance = record_appearance

def _record_action_states(
    records: RecordsService,
    entity: NormalizedEntity,
    values: Mapping[str, Any],
    context: RequestContext,
) -> dict[str, dict[str, bool]]:
    """Return safe record-specific action hints without exposing expressions."""

    result: dict[str, dict[str, bool]] = {}
    for action_name, action in entity.actions.items():
        if (
            action.get("expose", {}).get("rest") is not True
            or not records.security.can_execute_action(action, context)
        ):
            continue
        state = _action_state(action, values)
        result[action_name] = {
            "visible": state.visible,
            "enabled": state.enabled,
        }
    return result


def _report_export_response(
    document: ReportDocument,
    export_format: Literal["csv", "html", "pdf"],
) -> Response:
    """Render one already-authorized report with a safe attachment name."""

    filename = f"{document.suggested_filename}.{export_format}"
    fallback = re.sub(
        r"[^A-Za-z0-9._-]+",
        "-",
        filename.encode("ascii", "ignore").decode("ascii"),
    ).strip("-.") or f"report.{export_format}"
    disposition = (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    if export_format == "csv":
        content = render_csv(document).encode("utf-8-sig")
        media_type = "text/csv; charset=utf-8"
    elif export_format == "html":
        content = render_html(document).encode("utf-8")
        media_type = "text/html; charset=utf-8"
    else:
        try:
            content = render_pdf(document)
        except PdfDependencyMissing as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "report_format_unavailable",
                    "message": str(error),
                },
            ) from error
        media_type = "application/pdf"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )


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


def _browser_session_access(
    request: Request,
    browser_auth: BrowserAuthenticator | None,
    session_origin: str | None = None,
) -> Any:
    if browser_auth is None:
        raise _unauthorized()
    origin, session_id = split_session_cookie(
        request.cookies.get(browser_auth.session_cookie_name)
    )
    access = browser_auth.authenticate_session(session_id)
    if access is not None:
        return access
    # A cookie that names a different process, in a deployment that stamps
    # one, is not an expired session -- it is a request that reached the
    # wrong server. Saying so turns a misconfiguration that looks like a
    # random sign-out into one an operator can act on. Only reachable where
    # sessions are process-local, because that is the only case that stamps.
    if (
        session_origin is not None
        and origin is not None
        and origin != session_origin
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "session_from_another_server",
                "message": (
                    "this session was issued by another server process, which "
                    "keeps its sessions to itself"
                ),
            },
        )
    raise _unauthorized()


def _browser_login_error_redirect(
    browser_auth: OidcBrowserAuthenticator,
) -> RedirectResponse:
    response = RedirectResponse("/?tide_auth_error=login_failed", status_code=303)
    response.delete_cookie(
        browser_auth.transaction_cookie_name,
        path="/",
        secure=browser_auth.secure_cookie,
        httponly=True,
        samesite="lax",
    )
    return response


def _csrf_failed() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={
            "code": "csrf_failed",
            "message": "browser request verification failed",
        },
    )


def _non_loopback_host() -> JSONResponse:
    """Refuse a request that asked for a name other than this machine's.

    A response rather than a raise: this runs in the middleware, before routing,
    so there is no handler to carry an `HTTPException` out of. 403 rather than
    404 because the resource exists and the caller is not allowed to reach it by
    that name, and the message says which name is wrong so a developer who has
    put a proxy in front of a development server is not left guessing.
    """

    return JSONResponse(
        status_code=403,
        content={
            "detail": {
                "code": "non_loopback_host",
                "message": (
                    "a development browser session serves only loopback host "
                    "names"
                ),
            }
        },
    )


def _local_user(user: LocalUserSummary) -> TideLocalUser:
    """Project one account for the wire. There is no hash to withhold."""

    return TideLocalUser(
        username=user.username,
        display_name=user.display_name,
        enabled=user.enabled,
        roles=tuple(sorted(user.roles)),
        created_at=user.created_at,
        password_changed_at=user.password_changed_at,
    )


def _administration_refused(error: AdministrationError) -> HTTPException:
    """Answer with what the caller can act on.

    A conflict is the store's current state refusing -- an account that is
    already there, the last way back in -- and is worth telling apart from a
    request that was simply wrong. The message never repeats a value that was
    refused, because one of them is a password.
    """

    if isinstance(error, AdministrationDenied):
        return HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": str(error)},
        )
    if isinstance(error, UnknownLocalUser):
        return HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": str(error)},
        )
    if isinstance(error, AdministrationConflict):
        return HTTPException(
            status_code=409,
            detail={"code": "conflict", "message": str(error)},
        )
    return HTTPException(
        status_code=400,
        detail={"code": "invalid_request", "message": str(error)},
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
        503: "Requested optional report format is unavailable",
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
    return entity.version_field


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
