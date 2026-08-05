"""Model Context Protocol adapters."""

from __future__ import annotations

import argparse
import sys




def add_mcp_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Declare the `mcp` arguments."""

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
