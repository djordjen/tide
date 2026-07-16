"""The initial TIDE command-line interface."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel
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
    run.set_defaults(handler=_run_tui)

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
        "--host",
        default="127.0.0.1",
        choices=("127.0.0.1", "localhost", "::1"),
        help="loopback interface for the development identity adapter",
    )
    serve.add_argument("--port", type=int, default=8000)
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
    serve.set_defaults(handler=_serve_api)

    database = commands.add_parser("db", help="manage development database data")
    database_commands = database.add_subparsers(dest="database_command")
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

    return parser


def _run_tui(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    storage = _open_run_storage(arguments, model)
    if storage is None:
        return 1
    try:
        return _launch_tui(arguments, model, storage)
    finally:
        storage.dispose()


def _serve_api(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
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
    if not 1 <= arguments.port <= 65535:
        print("API startup failed: port must be between 1 and 65535", file=sys.stderr)
        return 1

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
        roles = tuple(arguments.role)
        if not roles and arguments.demo and model.roles:
            roles = (max(model.roles, key=lambda role: len(model.roles[role])),)
        principal = Principal(
            arguments.principal,
            roles=frozenset(roles),
        )
        records = RecordsService(
            model,
            storage.repository,
            cursor_store=storage.cursor_store,
        )
        actions = ActionService(
            model,
            records,
            execution_store=storage.execution_store,
        )
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
        try:
            configure_application_runtime(model, records, actions)
            app = build_fastapi_app(
                model,
                records,
                DevelopmentTokenAuthenticator(token, principal),
                actions=actions,
                base_path=arguments.base_path,
            )
        except (ApplicationRuntimeError, ValueError) as error:
            print(f"API startup failed: {error}", file=sys.stderr)
            return 1

        print(
            f"Serving {model.name} at http://{arguments.host}:{arguments.port} "
            f"(docs: /docs; identity: {principal.identifier}; development auth only)."
        )
        uvicorn.run(
            app,
            host=arguments.host,
            port=arguments.port,
            log_level="info",
        )
        return 0
    finally:
        storage.dispose()


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
    records = RecordsService(model, repository, cursor_store=storage.cursor_store)
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


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))
