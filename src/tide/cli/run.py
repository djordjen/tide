"""Commands that open a local interface: terminal, Studio, desktop."""

from __future__ import annotations

import argparse
import os
import sys


from tide.api.openapi import DEFAULT_BASE_PATH
from tide.compiler.compiler import compile_project
from tide.compiler.normalized import ApplicationModel
from tide.development import (
    DesignerError,
    StudioService,
)
from tide.runtime import Channel, Principal, RequestContext, TideRuntimeError
from tide.services import (
    ActionService,
    RecordsService,
)

from .storage import RunStorage, open_run_storage


def add_run_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Declare the `run` arguments."""

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
    run.add_argument(
        "--page-size",
        type=int,
        help="override the incremental browse fetch batch size",
    )
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

    gui = commands.add_parser(
        "gui",
        help="run the remote PySide6 desktop prototype",
    )
    gui.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    gui.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="remote TIDE application server (default: http://127.0.0.1:8000)",
    )
    gui.add_argument("--view", help="browse view to open (default: application default)")
    gui.add_argument(
        "--page-size",
        type=int,
        help="override the incremental server fetch batch size",
    )
    gui.add_argument(
        "--api-base-path",
        default=DEFAULT_BASE_PATH,
        help=f"remote REST base path (default: {DEFAULT_BASE_PATH})",
    )
    gui.add_argument(
        "--api-token-env",
        default="TIDE_API_TOKEN",
        metavar="NAME",
        help="read the remote bearer token from environment variable NAME",
    )
    gui.add_argument(
        "--api-timeout",
        type=float,
        default=10.0,
        help="remote request timeout in seconds (default: 10)",
    )
    gui.set_defaults(handler=_run_qt_gui)

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
    storage = open_run_storage(arguments, model)
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


def _run_qt_gui(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    token = os.environ.get(arguments.api_token_env)
    if not token:
        print(
            "Qt GUI startup failed: bearer-token environment variable "
            f"{arguments.api_token_env!r} is not set",
            file=sys.stderr,
        )
        return 1
    if arguments.api_timeout <= 0:
        print(
            "Qt GUI startup failed: --api-timeout must be positive",
            file=sys.stderr,
        )
        return 1
    try:
        from tide.api.client import TideApiClient
        from tide.qt import run_qt_application
    except ModuleNotFoundError as error:
        if error.name == "httpx" or (error.name or "").startswith("httpx."):
            print(
                "The TIDE API client is not installed. Install the 'gui' extra "
                "(for example: uv sync --extra gui).",
                file=sys.stderr,
            )
            return 1
        if error.name == "PySide6" or (error.name or "").startswith("PySide6."):
            print(
                "The Qt adapter is not installed. Install the 'gui' extra "
                "(for example: uv sync --extra gui).",
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
            return run_qt_application(
                model,
                client,
                session,
                view_name=arguments.view,
                page_size=arguments.page_size,
                source_label=f"remote API {arguments.api_url}",
            )
    except (TideRuntimeError, ValueError) as error:
        print(f"Qt GUI startup failed: {error}", file=sys.stderr)
        return 1


def _launch_tui(
    arguments: argparse.Namespace,
    model: ApplicationModel,
    storage: RunStorage,
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
