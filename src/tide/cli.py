"""The initial TIDE command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tide.api.openapi import DEFAULT_BASE_PATH, generate_openapi
from tide.compiler.compiler import compile_project
from tide.diagnostics import CompilationFailed, Severity
from tide.data import InMemoryRepository
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
from tide.runtime import Channel, Principal, RequestContext
from tide.services import RecordsService

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
    run.add_argument(
        "--demo",
        action="store_true",
        help="execute application-owned demo_data.py and seed an in-memory repository",
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
    repository = InMemoryRepository()
    source_label = "empty in-memory data"
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
    records = RecordsService(model, repository)
    try:
        from tide.tui import TideApp
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
        TideApp(
            model,
            records,
            context,
            view_name=arguments.view,
            page_size=arguments.page_size,
            source_label=source_label,
        ).run()
    except ValueError as error:
        print(f"TUI startup failed: {error}", file=sys.stderr)
        return 1
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
