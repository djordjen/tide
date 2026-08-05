"""Inspecting the generated machine-facing contracts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


from tide.api.openapi import DEFAULT_BASE_PATH, generate_openapi
from tide.compiler.compiler import compile_project
from tide.runtime import TideRuntimeError


def add_api_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Declare the `api` arguments."""

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
