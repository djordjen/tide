"""The `tide` command line: parse arguments, then hand off to a family."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


from tide.diagnostics import CompilationFailed, Severity


from .api import add_api_commands
from .application import add_application_commands, add_designer_commands
from .auth import add_authentication_commands
from .database import add_database_commands
from .mcp import add_mcp_commands
from .model import add_model_commands, add_view_commands
from .run import add_run_commands
from .serve import add_serve_command


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

    # Each family declares its own arguments beside the handlers that read
    # them. They used to be a thousand lines away from each other.
    add_run_commands(commands)
    add_serve_command(commands)
    add_database_commands(commands)
    add_application_commands(commands)
    add_designer_commands(commands)
    add_model_commands(commands)
    add_view_commands(commands)
    add_api_commands(commands)
    add_authentication_commands(commands)
    add_mcp_commands(commands)

    return parser
