"""The initial TIDE command-line interface."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from tide.api.openapi import DEFAULT_BASE_PATH, generate_openapi
from tide.compiler.compiler import compile_project
from tide.compiler.normalized import ApplicationModel
from tide.diagnostics import CompilationFailed, Severity
from tide.data import (
    InMemoryRepository,
    Repository,
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemyRepository,
)
from tide.development import (
    ApplicationApplyApproval,
    ApplicationApplyError,
    ApplicationApplyPreparation,
    ApplicationApplyService,
    ApplicationGenerationPlan,
    DesignerCommandBatch,
    DesignerError,
    DesignerRecoveryApproval,
    DesignerRecoveryError,
    DesignerRecoveryPreparation,
    DesignerRecoveryService,
    DesignerSaveApproval,
    DesignerSaveError,
    DesignerSavePreparation,
    DesignerSaveService,
    DesignerService,
    StudioService,
)
from tide.model.source import (
    EntitySource,
    FormatsSource,
    PresentationDefaultsSource,
    PresetDocumentSource,
    ProjectSource,
    ReportSource,
    SecurityDocumentSource,
    ViewSource,
)
from tide.runtime import Channel, Principal, RequestContext, TideRuntimeError
from tide.services import (
    ActionExecutionStore,
    ActionService,
    CursorStore,
    RecordsService,
)

SCHEMA_TYPES: dict[str, type[BaseModel]] = {
    "project": ProjectSource,
    "entity": EntitySource,
    "view": ViewSource,
    "report": ReportSource,
    "presets": PresetDocumentSource,
    "defaults": PresentationDefaultsSource,
    "formats": FormatsSource,
    "security": SecurityDocumentSource,
}


def main(argv: list[str] | None = None) -> int:
    parser = _create_parser()
    arguments = parser.parse_args(argv)
    if not hasattr(arguments, "handler"):
        parser.print_help()
        return 1
    try:
        return int(arguments.handler(arguments))
    except CompilationFailed as error:
        project = Path(getattr(arguments, "project", ".")).resolve()
        root = project.parent if project.is_file() else project
        if getattr(arguments, "json", False):
            print(
                json.dumps(
                    {
                        "valid": False,
                        "diagnostics": [
                            diagnostic.as_dict(root=root) for diagnostic in error.diagnostics
                        ],
                    },
                    indent=2,
                )
            )
        else:
            for diagnostic in error.diagnostics:
                print(diagnostic.format(root=root), file=sys.stderr)
            errors = [
                diagnostic
                for diagnostic in error.diagnostics
                if diagnostic.severity is Severity.ERROR
            ]
            print(
                f"Model validation failed with {len(errors)} error(s).",
                file=sys.stderr,
            )
        return 2


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tide", description="Terminal Integrated Data Environment")
    parser.add_argument("--version", action="version", version="TIDE 0.1.0")
    commands = parser.add_subparsers(dest="command")

    run = commands.add_parser("run", help="run the Textual application adapter")
    run.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    data_source = run.add_mutually_exclusive_group()
    data_source.add_argument(
        "--demo",
        action="store_true",
        help="execute application-owned demo_data.py and seed an in-memory repository",
    )
    data_source.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        metavar="NAME",
        help=(
            "use a SQLAlchemy database URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    data_source.add_argument(
        "--api-url",
        help="use a remote TIDE application server instead of local persistence",
    )
    run.add_argument(
        "--create-schema",
        action="store_true",
        help="explicitly create missing managed application and TIDE system tables",
    )
    run.add_argument("--view", help="browse view to open (default: first browse view)")
    run.add_argument(
        "--role",
        action="append",
        default=[],
        help="principal role; repeat for multiple roles (demo default: most capable role)",
    )
    run.add_argument("--principal", default="local:user", help="principal identifier")
    run.add_argument("--page-size", type=int, help="override the view page size")
    run.add_argument(
        "--api-base-path",
        default=DEFAULT_BASE_PATH,
        help=f"remote REST base path (default: {DEFAULT_BASE_PATH})",
    )
    run.add_argument(
        "--api-token-env",
        default="TIDE_API_TOKEN",
        metavar="NAME",
        help="read the remote bearer token from environment variable NAME",
    )
    run.add_argument(
        "--api-timeout",
        type=float,
        default=10.0,
        help="remote request timeout in seconds (default: 10)",
    )
    run.set_defaults(handler=_run_tui)

    studio = commands.add_parser(
        "studio",
        help="inspect and edit an in-memory application-metadata candidate",
    )
    studio.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    studio.set_defaults(handler=_run_studio)

    serve = commands.add_parser(
        "serve",
        help="run the FastAPI application server",
    )
    serve.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    serve_source = serve.add_mutually_exclusive_group()
    serve_source.add_argument(
        "--demo",
        action="store_true",
        help="execute application-owned demo_data.py in an in-memory repository",
    )
    serve_source.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        metavar="NAME",
        help=(
            "use a SQLAlchemy database URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    serve.add_argument(
        "--create-schema",
        action="store_true",
        help="explicitly create missing managed application and TIDE system tables",
    )
    serve.add_argument(
        "--base-path",
        default=DEFAULT_BASE_PATH,
        help=f"REST base path (default: {DEFAULT_BASE_PATH})",
    )
    serve.add_argument(
        "--auth",
        choices=("development", "oidc"),
        default="development",
        help="bearer identity adapter (default: development)",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface to bind (non-loopback requires OIDC and direct TLS)",
    )
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--ssl-certfile",
        type=Path,
        help="PEM certificate chain used for direct HTTPS",
    )
    serve.add_argument(
        "--ssl-keyfile",
        type=Path,
        help="PEM private key used for direct HTTPS",
    )
    serve.add_argument(
        "--ssl-keyfile-password-env",
        metavar="NAME",
        help="read the encrypted private-key password from environment variable NAME",
    )
    serve.add_argument(
        "--dev-token-env",
        default="TIDE_API_TOKEN",
        metavar="NAME",
        help="read the local-development bearer token from environment variable NAME",
    )
    serve.add_argument(
        "--role",
        action="append",
        default=[],
        help="server-assigned principal role; repeat for multiple roles",
    )
    serve.add_argument(
        "--principal",
        default="development:api",
        help="server-assigned development principal identifier",
    )
    serve.add_argument(
        "--oidc-issuer",
        help="exact HTTPS issuer URL used for OIDC discovery and token validation",
    )
    serve.add_argument(
        "--oidc-audience",
        help="required access-token audience for this TIDE server",
    )
    serve.add_argument(
        "--oidc-role-claim",
        default="roles",
        help="dot-separated claim containing external roles (default: roles)",
    )
    serve.add_argument(
        "--oidc-role-map",
        action="append",
        default=[],
        metavar="EXTERNAL=TIDE_ROLE",
        help="map an external role to an application role; repeat as needed",
    )
    serve.add_argument(
        "--oidc-algorithm",
        action="append",
        default=[],
        metavar="NAME",
        help="accepted asymmetric JWT algorithm; repeat (default: RS256)",
    )
    serve.add_argument(
        "--oidc-token-type",
        action="append",
        default=[],
        metavar="TYPE",
        help="accepted JWT typ header; repeat (defaults: at+jwt and JWT)",
    )
    serve.add_argument(
        "--oidc-leeway",
        type=float,
        default=30.0,
        help="JWT clock-skew leeway in seconds (default: 30)",
    )
    serve.add_argument(
        "--oidc-timeout",
        type=float,
        default=5.0,
        help="OIDC discovery and JWKS timeout in seconds (default: 5)",
    )
    serve.add_argument(
        "--mcp",
        action="store_true",
        help="mount the opt-in read-only runtime MCP server",
    )
    serve.add_argument(
        "--mcp-path",
        default="/mcp",
        help="Streamable HTTP MCP path (default: /mcp)",
    )
    serve.add_argument(
        "--mcp-resource-url",
        help=(
            "canonical externally reachable MCP resource URL; required for "
            "non-loopback serving"
        ),
    )
    serve.set_defaults(handler=_serve_api)

    database = commands.add_parser("db", help="manage development database data")
    database_commands = database.add_subparsers(dest="database_command")
    database_check = database_commands.add_parser(
        "check",
        help="validate database connectivity, schema, durable state, and queries",
    )
    database_check.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    database_check.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        required=True,
        metavar="NAME",
        help=(
            "read the SQLAlchemy database URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    database_check.set_defaults(handler=_db_check, create_schema=False)
    seed = database_commands.add_parser(
        "seed",
        help="seed an empty managed database with application-owned fake data",
    )
    seed.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    seed.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        required=True,
        metavar="NAME",
        help=(
            "read the SQLAlchemy database URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    seed.add_argument("--customers", type=int, default=25)
    seed.add_argument("--products", type=int, default=20)
    seed.add_argument("--invoices", type=int, default=100)
    seed.add_argument("--random-seed", type=int, default=20260716)
    seed.add_argument("--locale", default="en_US")
    seed.add_argument("--role", default="sales_clerk")
    seed.set_defaults(handler=_db_seed, create_schema=False)

    application = commands.add_parser(
        "app",
        help="preview and explicitly apply generated applications",
    )
    application_commands = application.add_subparsers(dest="application_command")
    application_preview = application_commands.add_parser(
        "preview",
        help="validate an application plan and prepare an approval challenge",
    )
    application_preview.add_argument(
        "plan",
        type=Path,
        metavar="PLAN.json",
        help="structured application generation plan",
    )
    application_preview.add_argument(
        "--workspace",
        type=Path,
        default=Path("."),
        help="workspace containing applications/ (default: current directory)",
    )
    application_preview.add_argument(
        "--json",
        action="store_true",
        help="emit the approval preparation as structured JSON",
    )
    application_preview.set_defaults(handler=_app_preview)

    application_apply = application_commands.add_parser(
        "apply",
        help="interactively approve and atomically publish a new application",
    )
    application_apply.add_argument(
        "plan",
        type=Path,
        metavar="PLAN.json",
        help="structured application generation plan",
    )
    application_apply.add_argument(
        "--workspace",
        type=Path,
        default=Path("."),
        help="workspace containing applications/ (default: current directory)",
    )
    application_apply.set_defaults(handler=_app_apply)

    designer = commands.add_parser(
        "designer",
        help="preview and explicitly save structured application edits",
    )
    designer_commands = designer.add_subparsers(dest="designer_command")
    designer_preview = designer_commands.add_parser(
        "preview",
        help="apply commands in memory and prepare an exact save challenge",
    )
    designer_preview.add_argument(
        "project",
        metavar="APPLICATION",
        help="application root or tide.yaml",
    )
    designer_preview.add_argument(
        "changes",
        type=Path,
        metavar="CHANGES.json",
        help="structured Designer command batch",
    )
    designer_preview.add_argument(
        "--json",
        action="store_true",
        help="emit the save preparation as structured JSON",
    )
    designer_preview.set_defaults(handler=_designer_preview)

    designer_save = designer_commands.add_parser(
        "save",
        help="interactively approve and save an exact Designer candidate",
    )
    designer_save.add_argument(
        "project",
        metavar="APPLICATION",
        help="application root or tide.yaml",
    )
    designer_save.add_argument(
        "changes",
        type=Path,
        metavar="CHANGES.json",
        help="structured Designer command batch",
    )
    designer_save.set_defaults(handler=_designer_save)

    designer_recover = designer_commands.add_parser(
        "recover",
        help="inspect or explicitly recover an interrupted Designer save",
    )
    designer_recover.add_argument(
        "project",
        metavar="APPLICATION",
        help="application root or tide.yaml",
    )
    designer_recover.add_argument(
        "--preview",
        action="store_true",
        help="inspect recovery evidence without changing files",
    )
    designer_recover.add_argument(
        "--json",
        action="store_true",
        help="emit a read-only recovery preview as structured JSON",
    )
    designer_recover.set_defaults(handler=_designer_recover)

    model = commands.add_parser("model", help="validate and inspect the application model")
    model_commands = model.add_subparsers(dest="model_command")

    validate = model_commands.add_parser("validate", help="compile and validate an application")
    validate.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    validate.add_argument("--json", action="store_true", help="emit structured output")
    validate.set_defaults(handler=_model_validate)

    explain = model_commands.add_parser("explain", help="show a normalized entity or field")
    explain.add_argument("target")
    explain.add_argument(
        "--project",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    explain.set_defaults(handler=_model_explain)

    schema = model_commands.add_parser("schema", help="export a JSON Schema for a source file")
    schema.add_argument("kind", choices=sorted(SCHEMA_TYPES))
    schema.add_argument("--output", type=Path)
    schema.set_defaults(handler=_model_schema)

    view = commands.add_parser("view", help="inspect resolved views")
    view_commands = view.add_subparsers(dest="view_command")
    view_explain = view_commands.add_parser("explain", help="show a validated view")
    view_explain.add_argument("target")
    view_explain.add_argument(
        "--project",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    view_explain.set_defaults(handler=_view_explain)

    api = commands.add_parser("api", help="inspect generated machine-interface contracts")
    api_commands = api.add_subparsers(dest="api_command")
    export_openapi = api_commands.add_parser(
        "export-openapi",
        help="export the read-only OpenAPI preview",
    )
    export_openapi.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    export_openapi.add_argument(
        "--base-path",
        default=DEFAULT_BASE_PATH,
        help=f"REST base path (default: {DEFAULT_BASE_PATH})",
    )
    export_openapi.add_argument("--output", type=Path)
    export_openapi.set_defaults(handler=_api_export_openapi)

    check_server = api_commands.add_parser(
        "check-server",
        help="authenticate and verify a remote TIDE application contract",
    )
    check_server.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    check_server.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="TIDE server origin (default: http://127.0.0.1:8000)",
    )
    check_server.add_argument(
        "--base-path",
        default=DEFAULT_BASE_PATH,
        help=f"REST base path (default: {DEFAULT_BASE_PATH})",
    )
    check_server.add_argument(
        "--token-env",
        default="TIDE_API_TOKEN",
        metavar="NAME",
        help="read the bearer token from environment variable NAME",
    )
    check_server.set_defaults(handler=_api_check_server)

    mcp = commands.add_parser("mcp", help="run Model Context Protocol adapters")
    mcp_commands = mcp.add_subparsers(dest="mcp_command")
    mcp_dev = mcp_commands.add_parser(
        "dev",
        help="run the local read/propose-only developer MCP over stdio",
    )
    mcp_dev.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    mcp_dev.set_defaults(handler=_mcp_dev)

    return parser


def _run_tui(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    if arguments.api_url:
        if arguments.role or arguments.principal != "local:user":
            print(
                "TUI remote startup failed: --role and --principal are assigned "
                "by the API server",
                file=sys.stderr,
            )
            return 1
        if arguments.create_schema:
            print(
                "TUI remote startup failed: --create-schema cannot be used with "
                "--api-url",
                file=sys.stderr,
            )
            return 1
        return _launch_remote_tui(arguments, model)
    storage = _open_run_storage(arguments, model)
    if storage is None:
        return 1
    try:
        return _launch_tui(arguments, model, storage)
    finally:
        storage.dispose()


def _run_studio(arguments: argparse.Namespace) -> int:
    try:
        service = StudioService(arguments.project)
    except DesignerError as error:
        print(f"Studio startup failed: {error}", file=sys.stderr)
        return 1
    try:
        from tide.tui import StudioApp
    except ModuleNotFoundError as error:
        if error.name == "textual" or (error.name or "").startswith("textual."):
            print(
                "TIDE Studio requires the Textual adapter. Install the 'tui' extra "
                "or the syntax-enabled 'studio' extra "
                "(for example: uv sync --extra studio).",
                file=sys.stderr,
            )
            return 1
        raise
    StudioApp(service).run()
    return 0


def _serve_api(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    if not 1 <= arguments.port <= 65535:
        print("API startup failed: port must be between 1 and 65535", file=sys.stderr)
        return 1

    try:
        certfile, keyfile, keyfile_password = _server_tls_configuration(arguments)
        is_loopback = _is_loopback_host(arguments.host)
        if arguments.auth == "development" and not is_loopback:
            raise ValueError(
                "development authentication may listen only on a loopback interface"
            )
        if arguments.auth == "oidc" and not is_loopback and certfile is None:
            raise ValueError(
                "non-loopback serving requires --ssl-certfile and --ssl-keyfile"
            )
        mcp_resource_url, mcp_issuer_url = _server_mcp_configuration(
            arguments,
            is_loopback=is_loopback,
            direct_tls=certfile is not None,
        )
    except ValueError as error:
        print(f"API startup failed: {error}", file=sys.stderr)
        return 1

    try:
        from tide.api.server import (
            DevelopmentTokenAuthenticator,
            build_fastapi_app,
        )
        from tide.runtime.application import (
            ApplicationRuntimeError,
            configure_application_runtime,
        )
        import uvicorn
    except ModuleNotFoundError as error:
        if error.name in {"fastapi", "uvicorn"} or (error.name or "").startswith(
            ("fastapi.", "uvicorn.")
        ):
            print(
                "The FastAPI adapter is not installed. Install the 'api' extra "
                "(for example: uv sync --extra api).",
                file=sys.stderr,
            )
            return 1
        raise

    identity_summary: str
    if arguments.auth == "development":
        token = os.environ.get(arguments.dev_token_env)
        if not token:
            print(
                "API startup failed: development bearer-token environment variable "
                f"{arguments.dev_token_env!r} is not set",
                file=sys.stderr,
            )
            return 1
        if len(token) < 32:
            print(
                "API startup failed: development bearer token must contain at least "
                "32 characters",
                file=sys.stderr,
            )
            return 1
        roles = tuple(arguments.role)
        if not roles and arguments.demo and model.roles:
            roles = (max(model.roles, key=lambda role: len(model.roles[role])),)
        principal = Principal(arguments.principal, roles=frozenset(roles))
        authenticator: Any = DevelopmentTokenAuthenticator(token, principal)
        identity_summary = (
            f"identity: {principal.identifier}; development auth only"
        )
    else:
        if arguments.role or arguments.principal != "development:api":
            print(
                "API startup failed: --role and --principal apply only to "
                "development authentication",
                file=sys.stderr,
            )
            return 1
        if not arguments.oidc_issuer or not arguments.oidc_audience:
            print(
                "API startup failed: OIDC authentication requires --oidc-issuer "
                "and --oidc-audience",
                file=sys.stderr,
            )
            return 1
        try:
            from tide.api.auth import OidcJwtAuthenticator

            role_map = _parse_oidc_role_map(arguments.oidc_role_map, model)
            authenticator = OidcJwtAuthenticator.from_discovery(
                issuer=arguments.oidc_issuer,
                audience=arguments.oidc_audience,
                role_claim=arguments.oidc_role_claim,
                role_map=role_map,
                algorithms=tuple(arguments.oidc_algorithm) or ("RS256",),
                token_types=tuple(arguments.oidc_token_type) or ("at+jwt", "JWT"),
                leeway=arguments.oidc_leeway,
                timeout=arguments.oidc_timeout,
            )
        except ModuleNotFoundError as error:
            if error.name in {"httpx", "jwt", "cryptography"} or (
                error.name or ""
            ).startswith(("httpx.", "jwt.", "cryptography.")):
                print(
                    "The OIDC adapter is not installed. Install the 'auth' extra "
                    "(for example: uv sync --extra api --extra auth).",
                    file=sys.stderr,
                )
                return 1
            raise
        except ValueError as error:
            print(f"API startup failed: {error}", file=sys.stderr)
            return 1
        identity_summary = f"identity: OIDC issuer {arguments.oidc_issuer}"

    storage = _open_run_storage(arguments, model, purpose="API")
    if storage is None:
        return 1
    try:
        if arguments.demo:
            try:
                from tide.tui.demo import DemoDataError, seed_demo_data

                seed_demo_data(model, storage.repository)
            except DemoDataError as error:
                print(f"API demo startup failed: {error}", file=sys.stderr)
                return 1
        records = RecordsService(
            model,
            storage.repository,
            cursor_store=storage.cursor_store,
            audit_store=storage.execution_store,
        )
        actions = ActionService(
            model,
            records,
            execution_store=storage.execution_store,
        )
        try:
            configure_application_runtime(model, records, actions)
            app = build_fastapi_app(
                model,
                records,
                authenticator,
                actions=actions,
                base_path=arguments.base_path,
            )
            if arguments.mcp:
                try:
                    from tide.mcp.runtime import RuntimeMcpService
                    from tide.mcp.server import (
                        build_runtime_mcp_server,
                        mount_runtime_mcp,
                    )
                except ModuleNotFoundError as error:
                    if error.name == "mcp" or (error.name or "").startswith("mcp."):
                        print(
                            "The MCP adapter is not installed. Install the 'mcp' "
                            "extra (for example: uv sync --extra api --extra mcp).",
                            file=sys.stderr,
                        )
                        return 1
                    raise
                assert mcp_resource_url is not None
                assert mcp_issuer_url is not None
                hosted_mcp = build_runtime_mcp_server(
                    RuntimeMcpService(model, records),
                    authenticator,
                    issuer_url=mcp_issuer_url,
                    resource_url=mcp_resource_url,
                    path=arguments.mcp_path,
                )
                mount_runtime_mcp(app, hosted_mcp)
        except (ApplicationRuntimeError, ValueError) as error:
            print(f"API startup failed: {error}", file=sys.stderr)
            return 1
        scheme = "https" if certfile is not None else "http"
        mcp_summary = (
            f"; MCP: {mcp_resource_url}" if mcp_resource_url is not None else ""
        )
        print(
            f"Serving {model.name} at {scheme}://{arguments.host}:{arguments.port} "
            f"(docs: /docs; {identity_summary}{mcp_summary})."
        )
        configuration: dict[str, Any] = {
            "host": arguments.host,
            "port": arguments.port,
            "log_level": "info",
        }
        if certfile is not None and keyfile is not None:
            configuration.update(
                ssl_certfile=str(certfile),
                ssl_keyfile=str(keyfile),
            )
            if keyfile_password is not None:
                configuration["ssl_keyfile_password"] = keyfile_password
        uvicorn.run(app, **configuration)
        return 0
    finally:
        storage.dispose()


def _server_tls_configuration(
    arguments: argparse.Namespace,
) -> tuple[Path | None, Path | None, str | None]:
    certfile = arguments.ssl_certfile
    keyfile = arguments.ssl_keyfile
    if (certfile is None) != (keyfile is None):
        raise ValueError("--ssl-certfile and --ssl-keyfile must be supplied together")
    if arguments.ssl_keyfile_password_env and keyfile is None:
        raise ValueError(
            "--ssl-keyfile-password-env requires --ssl-certfile and --ssl-keyfile"
        )
    for label, path in (("certificate", certfile), ("private key", keyfile)):
        if path is not None and not path.is_file():
            raise ValueError(f"TLS {label} file does not exist: {path}")
    password = None
    if arguments.ssl_keyfile_password_env:
        password = os.environ.get(arguments.ssl_keyfile_password_env)
        if not password:
            raise ValueError(
                "TLS private-key password environment variable "
                f"{arguments.ssl_keyfile_password_env!r} is not set"
            )
    return certfile, keyfile, password


def _server_mcp_configuration(
    arguments: argparse.Namespace,
    *,
    is_loopback: bool,
    direct_tls: bool,
) -> tuple[str | None, str | None]:
    if not arguments.mcp:
        if arguments.mcp_resource_url is not None:
            raise ValueError("--mcp-resource-url requires --mcp")
        return None, None
    path = arguments.mcp_path.strip()
    if (
        not path.startswith("/")
        or path == "/"
        or path.endswith("/")
        or "?" in path
        or "#" in path
    ):
        raise ValueError(
            "--mcp-path must be an absolute non-root path without a trailing slash"
        )
    resource_url = arguments.mcp_resource_url
    if resource_url is None:
        if not is_loopback:
            raise ValueError(
                "non-loopback MCP serving requires --mcp-resource-url"
            )
        host = arguments.host.strip()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        scheme = "https" if direct_tls else "http"
        resource_url = f"{scheme}://{host}:{arguments.port}{path}"
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
        or parsed.path != path
    ):
        raise ValueError(
            "MCP resource URL must be an absolute HTTP or HTTPS URL whose path "
            "exactly matches --mcp-path"
        )
    resource_is_loopback = _is_loopback_host(parsed.hostname)
    if parsed.scheme != "https" and not resource_is_loopback:
        raise ValueError("non-loopback MCP resource URLs must use HTTPS")
    if arguments.auth == "development" and not resource_is_loopback:
        raise ValueError("development MCP resource URLs must use a loopback host")
    if arguments.auth == "oidc":
        issuer_url = arguments.oidc_issuer
    else:
        issuer_url = f"{parsed.scheme}://{parsed.netloc}"
    return resource_url, issuer_url


def _is_loopback_host(host: str) -> bool:
    value = host.strip()
    if value.lower() == "localhost":
        return True
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _parse_oidc_role_map(
    values: list[str],
    model: ApplicationModel,
) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for value in values:
        external, separator, tide_role = value.partition("=")
        external = external.strip()
        tide_role = tide_role.strip()
        if not separator or not external or not tide_role:
            raise ValueError(
                "OIDC role mappings must use EXTERNAL=TIDE_ROLE"
            )
        if external in mappings:
            raise ValueError(f"duplicate OIDC role mapping for {external!r}")
        if tide_role not in model.roles:
            raise ValueError(
                f"OIDC role mapping targets unknown application role {tide_role!r}"
            )
        mappings[external] = tide_role
    return mappings


def _launch_tui(
    arguments: argparse.Namespace,
    model: ApplicationModel,
    storage: _RunStorage,
) -> int:
    repository = storage.repository
    source_label = storage.source_label
    if arguments.demo:
        try:
            from tide.tui.demo import DemoDataError, seed_demo_data

            seeded = seed_demo_data(model, repository)
        except DemoDataError as error:
            print(f"TUI demo startup failed: {error}", file=sys.stderr)
            return 1
        source_label = f"demo data ({seeded} seeded records)"
    roles = tuple(arguments.role)
    if not roles and arguments.demo and model.roles:
        roles = (max(model.roles, key=lambda role: len(model.roles[role])),)
    context = RequestContext(
        principal=Principal(
            arguments.principal,
            roles=frozenset(roles),
        ),
        channel=Channel.TUI,
    )
    records = RecordsService(
        model,
        repository,
        cursor_store=storage.cursor_store,
        audit_store=storage.execution_store,
    )
    actions = ActionService(
        model,
        records,
        execution_store=storage.execution_store,
    )
    try:
        from tide.tui import (
            ApplicationRuntimeError,
            TideApp,
            configure_application_runtime,
        )
    except ModuleNotFoundError as error:
        if error.name == "textual" or (error.name or "").startswith("textual."):
            print(
                "The Textual adapter is not installed. Install the 'tui' extra "
                "(for example: uv sync --extra tui).",
                file=sys.stderr,
            )
            return 1
        raise
    try:
        configure_application_runtime(model, records, actions)
    except ApplicationRuntimeError as error:
        print(f"TUI startup failed: {error}", file=sys.stderr)
        return 1
    try:
        TideApp(
            model,
            records,
            context,
            actions=actions,
            view_name=arguments.view,
            page_size=arguments.page_size,
            source_label=source_label,
        ).run()
    except ValueError as error:
        print(f"TUI startup failed: {error}", file=sys.stderr)
        return 1
    return 0


def _launch_remote_tui(
    arguments: argparse.Namespace,
    model: ApplicationModel,
) -> int:
    token = os.environ.get(arguments.api_token_env)
    if not token:
        print(
            "TUI remote startup failed: bearer-token environment variable "
            f"{arguments.api_token_env!r} is not set",
            file=sys.stderr,
        )
        return 1
    if arguments.api_timeout <= 0:
        print(
            "TUI remote startup failed: --api-timeout must be positive",
            file=sys.stderr,
        )
        return 1
    try:
        from tide.api.client import TideApiClient
        from tide.api.remote import (
            RemoteActionService,
            RemoteAuditHistoryService,
            RemoteRecordsService,
            RemoteReportService,
        )
        from tide.tui import TideApp
    except ModuleNotFoundError as error:
        if error.name == "httpx" or (error.name or "").startswith("httpx."):
            print(
                "The TIDE API client is not installed. Install the 'client' extra "
                "(for example: uv sync --extra client).",
                file=sys.stderr,
            )
            return 1
        if error.name == "textual" or (error.name or "").startswith("textual."):
            print(
                "The Textual adapter is not installed. Install the 'tui' extra "
                "(for example: uv sync --extra tui).",
                file=sys.stderr,
            )
            return 1
        raise

    try:
        with TideApiClient(
            model,
            arguments.api_url,
            token,
            base_path=arguments.api_base_path,
            timeout=arguments.api_timeout,
        ) as client:
            session = client.connect()
            context = RequestContext(
                principal=Principal(
                    session.principal,
                    roles=frozenset(session.roles),
                ),
                channel=Channel.TUI,
            )
            records = RemoteRecordsService(model, client, session)
            actions = RemoteActionService(client)
            audits = RemoteAuditHistoryService(client, session)
            reports = RemoteReportService(client, session)
            TideApp(
                model,
                records,
                context,
                actions=actions,
                audit_history=audits,
                view_name=arguments.view,
                page_size=arguments.page_size,
                source_label=f"remote API {arguments.api_url}",
                report_service=reports,
            ).run()
    except (TideRuntimeError, ValueError) as error:
        print(f"TUI remote startup failed: {error}", file=sys.stderr)
        return 1
    return 0


@dataclass(slots=True)
class _RunStorage:
    repository: Repository
    source_label: str
    cursor_store: CursorStore | None = None
    execution_store: ActionExecutionStore | None = None

    def dispose(self) -> None:
        if isinstance(self.repository, SQLAlchemyRepository):
            self.repository.dispose()


def _open_run_storage(
    arguments: argparse.Namespace,
    model: ApplicationModel,
    *,
    purpose: str = "TUI",
) -> _RunStorage | None:
    environment_name = arguments.database_env
    if environment_name is None:
        if arguments.create_schema:
            print(
                f"{purpose} startup failed: --create-schema requires --database-env",
                file=sys.stderr,
            )
            return None
        return _RunStorage(InMemoryRepository(), "empty in-memory data")

    database_url = os.environ.get(environment_name)
    if not database_url:
        print(
            f"{purpose} database startup failed: environment variable "
            f"{environment_name!r} is not set",
            file=sys.stderr,
        )
        return None

    repository: SQLAlchemyRepository | None = None
    try:
        repository = SQLAlchemyRepository(model, database_url)
        mode = str(model.database["mode"])
        cursor_store: SQLAlchemyCursorStore | None = None
        execution_store: SQLAlchemyActionExecutionStore | None = None
        if mode == "managed":
            cursor_store = SQLAlchemyCursorStore(repository.engine, mode="managed")
            execution_store = SQLAlchemyActionExecutionStore(
                repository.engine,
                mode="managed",
            )

        if arguments.create_schema:
            repository.create_schema()
            if cursor_store is not None and execution_store is not None:
                cursor_store.create_schema()
                execution_store.create_schema()

        repository.validate_schema()
        repository.validate_query_support()
        if cursor_store is not None and execution_store is not None:
            cursor_store.validate_schema()
            execution_store.validate_schema()

        state_label = "durable state" if mode == "managed" else "process-local state"
        return _RunStorage(
            repository,
            f"database via {environment_name} ({state_label})",
            cursor_store=cursor_store,
            execution_store=execution_store,
        )
    except (SQLAlchemyError, TideRuntimeError, ValueError) as error:
        if repository is not None:
            repository.dispose()
        detail = str(error) if isinstance(error, TideRuntimeError) else type(error).__name__
        print(
            f"{purpose} database startup failed via {environment_name!r}: {detail}",
            file=sys.stderr,
        )
        return None


def _db_check(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    storage = _open_run_storage(arguments, model, purpose="Read-only check")
    if storage is None:
        return 1
    try:
        repository = storage.repository
        dialect = (
            repository.engine.dialect.name
            if isinstance(repository, SQLAlchemyRepository)
            else "memory"
        )
        mode = str(model.database["mode"])
        state = "durable" if mode == "managed" else "process-local"
        print(
            f"Database check passed: {model.name} {model.version}; "
            f"dialect={dialect}; mode={mode}; framework_state={state}."
        )
        return 0
    finally:
        storage.dispose()


def _db_seed(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    if str(model.database["mode"]) != "managed":
        print(
            "Fake-data seeding is available only for managed databases; "
            "legacy schemas are never seeded automatically.",
            file=sys.stderr,
        )
        return 1
    counts = {
        "customers": arguments.customers,
        "products": arguments.products,
        "invoices": arguments.invoices,
    }
    if any(count < 0 for count in counts.values()):
        print("Fake-data counts must not be negative.", file=sys.stderr)
        return 1

    storage = _open_run_storage(arguments, model, purpose="Fake-data")
    if storage is None:
        return 1
    try:
        existing = [
            entity_name
            for entity_name in model.entities
            if storage.repository.all(entity_name)
        ]
        if existing:
            print(
                "Fake-data seeding refused because the database is not empty; "
                "entities with records: " + ", ".join(existing),
                file=sys.stderr,
            )
            return 1

        records = RecordsService(
            model,
            storage.repository,
            cursor_store=storage.cursor_store,
            audit_store=storage.execution_store,
        )
        actions = ActionService(
            model,
            records,
            execution_store=storage.execution_store,
        )
        from tide.tui.application_runtime import (
            ApplicationRuntimeError,
            configure_application_runtime,
        )

        configure_application_runtime(model, records, actions)
        from tide.development import FakeDataError, seed_fake_data

        context = RequestContext(
            principal=Principal(
                "development:seed",
                roles=frozenset({arguments.role}),
            ),
            channel=Channel.SYSTEM,
        )
        seeded = seed_fake_data(
            model,
            records,
            actions,
            context,
            counts=counts,
            random_seed=arguments.random_seed,
            locale=arguments.locale,
        )
    except (ApplicationRuntimeError, FakeDataError, TideRuntimeError, ValueError) as error:
        print(f"Fake-data seeding failed: {error}", file=sys.stderr)
        return 1
    finally:
        storage.dispose()

    summary = ", ".join(f"{name}={count}" for name, count in seeded.items())
    print(
        f"Fake-data seeding complete ({summary}; seed={arguments.random_seed})."
    )
    return 0


def _app_preview(arguments: argparse.Namespace) -> int:
    try:
        plan = _load_application_plan(arguments.plan)
    except ApplicationApplyError as error:
        if arguments.json:
            _print_json(
                {
                    "ready": False,
                    "writes_performed": False,
                    "blockers": [{"code": error.code, "message": error.message}],
                }
            )
        else:
            print(f"Application preview failed: {error}", file=sys.stderr)
        return 1

    preparation = ApplicationApplyService(arguments.workspace).prepare(plan)
    if arguments.json:
        _print_json(preparation.model_dump(mode="json"))
    else:
        _print_application_preparation(preparation)
    return 0 if preparation.ready else 1


def _app_apply(arguments: argparse.Namespace) -> int:
    try:
        plan = _load_application_plan(arguments.plan)
        service = ApplicationApplyService(arguments.workspace)
        preparation = service.prepare(plan)
    except ApplicationApplyError as error:
        print(f"Application apply failed: {error}", file=sys.stderr)
        return 1

    _print_application_preparation(preparation)
    if not preparation.ready or preparation.approval_prompt is None:
        print("Application apply refused; no files were written.", file=sys.stderr)
        return 1

    print(
        "\nThis command creates a new application source tree. It never overwrites "
        "an existing application."
    )
    try:
        response = input(
            f"Type exactly {preparation.approval_prompt!r} to publish: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nApplication apply cancelled; no files were written.", file=sys.stderr)
        return 1
    if response != preparation.approval_prompt:
        print("Application apply cancelled; no files were written.", file=sys.stderr)
        return 1

    try:
        approval = ApplicationApplyApproval.from_preparation(preparation)
        result = service.apply(plan, approval)
    except (ApplicationApplyError, ValueError) as error:
        print(f"Application apply failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Applied {result.artifact_count} generated artifacts to {result.target_path}."
    )
    print(f"Approval receipt: {result.receipt_path}")
    print(f"Candidate fingerprint: {result.candidate_fingerprint}")
    return 0


def _load_application_plan(path: Path) -> ApplicationGenerationPlan:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ApplicationApplyError(
            "TIDEAPPLY008",
            f"the application plan could not be read: {error}",
        ) from error
    except json.JSONDecodeError as error:
        raise ApplicationApplyError(
            "TIDEAPPLY008",
            f"the application plan is not valid JSON at line {error.lineno}",
        ) from error
    try:
        return ApplicationGenerationPlan.model_validate(document)
    except ValidationError as error:
        raise ApplicationApplyError(
            "TIDEAPPLY008",
            f"the application plan does not match the structured schema: {error}",
        ) from error


def _designer_preview(arguments: argparse.Namespace) -> int:
    try:
        batch = _load_designer_batch(arguments.changes)
        session = DesignerService(arguments.project).open_session()
        session.execute_batch(batch)
        preparation = DesignerSaveService().prepare(session)
    except (DesignerError, DesignerSaveError) as error:
        if arguments.json:
            _print_json(
                {
                    "ready": False,
                    "writes_performed": False,
                    "blockers": [{"code": error.code, "message": error.message}],
                }
            )
        else:
            print(f"Designer preview failed: {error}", file=sys.stderr)
        return 1

    if arguments.json:
        _print_json(preparation.model_dump(mode="json"))
    else:
        _print_designer_preparation(preparation)
    return 0 if preparation.ready else 1


def _designer_save(arguments: argparse.Namespace) -> int:
    try:
        batch = _load_designer_batch(arguments.changes)
        session = DesignerService(arguments.project).open_session()
        session.execute_batch(batch)
        service = DesignerSaveService()
        preparation = service.prepare(session)
    except (DesignerError, DesignerSaveError) as error:
        print(f"Designer save failed: {error}", file=sys.stderr)
        return 1

    _print_designer_preparation(preparation)
    if not preparation.ready or preparation.approval_prompt is None:
        print("Designer save refused; no source files were written.", file=sys.stderr)
        return 1

    print(
        "\nThis command replaces only the listed YAML source files. "
        "It rechecks the live application before writing."
    )
    try:
        response = input(
            f"Type exactly {preparation.approval_prompt!r} to save: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nDesigner save cancelled; no files were written.", file=sys.stderr)
        return 1
    if response != preparation.approval_prompt:
        print("Designer save cancelled; no files were written.", file=sys.stderr)
        return 1

    try:
        approval = DesignerSaveApproval.from_preparation(preparation)
        result = service.save(session, approval)
    except (DesignerSaveError, ValueError) as error:
        print(f"Designer save failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Saved {len(result.changed_files)} YAML source file(s) in "
        f"{result.project_path}."
    )
    print(f"Approval receipt: {result.receipt_path}")
    print(f"Candidate fingerprint: {result.candidate_fingerprint}")
    return 0


def _load_designer_batch(path: Path) -> DesignerCommandBatch:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise DesignerSaveError(
            "TIDEDSAVE008",
            f"the Designer command batch could not be read: {error}",
        ) from error
    except json.JSONDecodeError as error:
        raise DesignerSaveError(
            "TIDEDSAVE008",
            f"the Designer command batch is not valid JSON at line {error.lineno}",
        ) from error
    try:
        return DesignerCommandBatch.model_validate(document)
    except ValidationError as error:
        raise DesignerSaveError(
            "TIDEDSAVE008",
            f"the Designer command batch does not match its schema: {error}",
        ) from error


def _designer_recover(arguments: argparse.Namespace) -> int:
    try:
        service = DesignerRecoveryService(arguments.project)
        preparation = service.prepare()
    except DesignerRecoveryError as error:
        if arguments.json:
            _print_json(
                {
                    "ready": False,
                    "recovery_required": True,
                    "writes_performed": False,
                    "blockers": [{"code": error.code, "message": error.message}],
                }
            )
        else:
            print(f"Designer recovery failed: {error}", file=sys.stderr)
        return 1

    if arguments.json:
        _print_json(preparation.model_dump(mode="json"))
        if not preparation.recovery_required:
            return 0
        return 0 if preparation.ready else 1

    _print_designer_recovery_preparation(preparation)
    if arguments.preview or not preparation.recovery_required:
        return 0 if preparation.ready or not preparation.recovery_required else 1
    if not preparation.ready or preparation.approval_prompt is None:
        print("Designer recovery refused; no files were changed.", file=sys.stderr)
        return 1

    print(
        "\nRecovery uses the displayed file hashes to either restore the original "
        "YAML set or finalize an already receipted save."
    )
    try:
        response = input(
            f"Type exactly {preparation.approval_prompt!r} to recover: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nDesigner recovery cancelled; no files were changed.", file=sys.stderr)
        return 1
    if response != preparation.approval_prompt:
        print("Designer recovery cancelled; no files were changed.", file=sys.stderr)
        return 1

    try:
        approval = DesignerRecoveryApproval.from_preparation(preparation)
        result = service.recover(approval)
    except (DesignerRecoveryError, ValueError) as error:
        print(f"Designer recovery failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Designer transaction {result.transaction_id} recovered by "
        f"{result.recovery_action}."
    )
    print(f"Restored YAML files: {result.restored_files}")
    return 0


def _print_designer_recovery_preparation(
    preparation: DesignerRecoveryPreparation,
) -> None:
    print(preparation.summary)
    print(f"Project path: {preparation.project_path}")
    if preparation.transaction_id is not None:
        print(f"Transaction: {preparation.transaction_id}")
    if preparation.recovery_action is not None:
        print(f"Recovery action: {preparation.recovery_action}")
    if preparation.journal_phase is not None:
        print(f"Last durable phase: {preparation.journal_phase}")
    for artifact in preparation.artifacts:
        print(
            f"{artifact.path}: target={artifact.target_state}, "
            f"backup={artifact.backup_state}, "
            f"candidate={artifact.candidate_state}"
        )
    for blocker in preparation.blockers:
        print(f"{blocker.code}: {blocker.message}", file=sys.stderr)


def _print_designer_preparation(
    preparation: DesignerSavePreparation,
) -> None:
    print(preparation.summary)
    print(f"Application: {preparation.project}")
    print(f"Project path: {preparation.project_path}")
    print(f"Base: {preparation.base_state} ({preparation.base_fingerprint})")
    print(f"Candidate: {preparation.candidate_id}")
    print(f"Candidate fingerprint: {preparation.candidate_fingerprint}")
    if preparation.changed_files:
        print("Changed YAML: " + ", ".join(preparation.changed_files))
    for blocker in preparation.blockers:
        print(f"{blocker.code}: {blocker.message}", file=sys.stderr)
    if preparation.diff:
        print("\nExact candidate diff:\n")
        print(preparation.diff.rstrip())


def _print_application_preparation(
    preparation: ApplicationApplyPreparation,
) -> None:
    print(preparation.summary)
    if preparation.application_id is not None:
        print(f"Application: {preparation.application_id}")
    if preparation.target_path is not None:
        print(
            f"Destination: {preparation.target_path} ({preparation.destination_state})"
        )
    print(f"Proposal: {preparation.proposal_id}")
    if preparation.candidate_id is not None:
        print(f"Candidate: {preparation.candidate_id}")
    if preparation.candidate_fingerprint is not None:
        print(f"Candidate fingerprint: {preparation.candidate_fingerprint}")
    if preparation.base_fingerprint is not None:
        print(f"Base fingerprint: {preparation.base_fingerprint}")
    passed = sum(check.status == "passed" for check in preparation.checks)
    failed = sum(check.status == "failed" for check in preparation.checks)
    skipped = sum(check.status == "skipped" for check in preparation.checks)
    print(f"Checks: {passed} passed, {failed} failed, {skipped} skipped")
    for blocker in preparation.blockers:
        print(f"{blocker.code}: {blocker.message}", file=sys.stderr)
    if preparation.diff:
        print("\nExact candidate diff:\n")
        print(preparation.diff.rstrip())


def _model_validate(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    project = Path(arguments.project).resolve()
    root = project.parent if project.is_file() else project
    result = {
        "valid": True,
        "application": model.name,
        "version": model.version,
        "schema_version": model.schema_version,
        "entities": len(model.entities),
        "views": len(model.views),
        "reports": len(model.reports),
        "warnings": [diagnostic.as_dict(root=root) for diagnostic in model.diagnostics],
    }
    if arguments.json:
        print(json.dumps(result, indent=2))
    else:
        for diagnostic in model.diagnostics:
            print(diagnostic.format(root=root), file=sys.stderr)
        print(
            f"Model is valid: {model.name} {model.version} "
            f"({len(model.entities)} entities, {len(model.views)} views, "
            f"{len(model.reports)} reports, {len(model.diagnostics)} warning(s))."
        )
    return 0


def _model_explain(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    if arguments.target in model.entities:
        _print_json(model.entity(arguments.target).as_dict())
        return 0
    matches = [
        entity_name
        for entity_name in model.entities
        if arguments.target.startswith(entity_name + ".")
    ]
    if matches:
        entity_name = max(matches, key=len)
        field_name = arguments.target[len(entity_name) + 1 :]
        entity = model.entity(entity_name)
        if field_name in entity.fields:
            _print_json(entity.field(field_name).as_dict())
            return 0
    print(f"Unknown model target: {arguments.target}", file=sys.stderr)
    return 1


def _view_explain(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    view = model.views.get(arguments.target)
    if view is None:
        print(f"Unknown view: {arguments.target}", file=sys.stderr)
        return 1
    _print_json(view.as_dict())
    return 0


def _model_schema(arguments: argparse.Namespace) -> int:
    schema = SCHEMA_TYPES[arguments.kind].model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    text = json.dumps(schema, indent=2) + "\n"
    if arguments.output:
        arguments.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


def _api_export_openapi(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    try:
        document = generate_openapi(model, base_path=arguments.base_path)
    except ValueError as error:
        print(f"OpenAPI preview failed: {error}", file=sys.stderr)
        return 1
    text = json.dumps(document, indent=2) + "\n"
    if arguments.output:
        arguments.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


def _api_check_server(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    token = os.environ.get(arguments.token_env)
    if not token:
        print(
            "API check failed: bearer-token environment variable "
            f"{arguments.token_env!r} is not set",
            file=sys.stderr,
        )
        return 1
    try:
        from tide.api.client import TideApiClient
    except ModuleNotFoundError as error:
        if error.name == "httpx" or (error.name or "").startswith("httpx."):
            print(
                "The TIDE API client is not installed. Install the 'client' extra "
                "(for example: uv sync --extra client).",
                file=sys.stderr,
            )
            return 1
        raise

    try:
        with TideApiClient(
            model,
            arguments.url,
            token,
            base_path=arguments.base_path,
        ) as client:
            session = client.connect()
    except (TideRuntimeError, ValueError) as error:
        print(f"API check failed: {error}", file=sys.stderr)
        return 1

    operations = sum(
        len(capabilities.operations) for capabilities in session.entities.values()
    )
    actions = sum(
        len(capabilities.actions) for capabilities in session.entities.values()
    )
    print(
        f"Connected to {session.application} {session.application_version} as "
        f"{session.principal} ({operations} operation(s), {actions} action(s))."
    )
    return 0


def _mcp_dev(arguments: argparse.Namespace) -> int:
    try:
        from tide.mcp.developer import build_developer_mcp_server
    except ModuleNotFoundError as error:
        if error.name == "mcp" or (error.name or "").startswith("mcp."):
            print(
                "The MCP adapter is not installed. Install the 'mcp' extra "
                "(for example: uv sync --extra mcp).",
                file=sys.stderr,
            )
            return 1
        raise

    server = build_developer_mcp_server(arguments.project)
    # STDIO protocol messages own stdout. Developer status and diagnostics are
    # available through MCP resources/tools, so this command prints no banner.
    server.run(transport="stdio")
    return 0


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))
