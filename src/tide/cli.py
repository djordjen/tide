"""The initial TIDE command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tide.compiler.compiler import compile_project
from tide.diagnostics import CompilationFailed, Severity
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

    return parser


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


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))
