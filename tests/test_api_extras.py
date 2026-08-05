"""What each extra has to be enough for.

The documentation tells people to install `--extra api --extra report` and then
run `tide serve ... --auth local`. That has to be true, and it was not:
`server` imports `local_auth`, `local_auth` imported `browser_auth` for a
two-field dataclass, and `browser_auth` imports `httpx` -- which lives in the
`client`, `auth` and `gui` extras. Every server start needed an HTTP client it
never called, and the failure arrived as an unhandled `ModuleNotFoundError`
from three imports deep.

These run in a subprocess with the extras made unimportable, because they are
already loaded in this one and unloading them would not prove anything about a
machine that never had them.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


EVERY_EXTRA = (
    "alembic",
    "faker",
    "fastapi",
    "httpx",
    "jwt",
    "mcp",
    "pyodbc",
    "reportlab",
    "textual",
    "uvicorn",
)


def _blocking(*names: str) -> str:
    return textwrap.dedent(
        f"""
        import sys

        BLOCKED = {set(names)!r}

        class Blocked:
            def find_spec(self, name, target=None, path=None):
                root = name.split(".")[0]
                if root in BLOCKED:
                    raise ModuleNotFoundError(
                        f"No module named {{root!r}}", name=root
                    )
                return None

        sys.meta_path.insert(0, Blocked())
        """
    )


def _run(blocked: tuple[str, ...], body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _blocking(*blocked) + body],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_block_actually_blocks() -> None:
    """Without this the rest would pass on a machine that has the extras."""

    result = _run(EVERY_EXTRA, "import httpx")
    assert result.returncode != 0
    assert "No module named 'httpx'" in result.stderr


@pytest.mark.parametrize(
    "target",
    [
        "from tide.api.local_auth import LocalUserStore, validate_password",
        "from tide.api.browser_session import BrowserSessionAccess",
    ],
)
def test_local_identities_need_no_extra_at_all(target: str) -> None:
    """`tide auth create-user` manages a SQLite file and hashes a password."""

    result = _run(EVERY_EXTRA, f"{target}\nprint('ok')")
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


@pytest.mark.parametrize(
    "target",
    [
        "from tide.api.server import build_fastapi_app",
        "from tide.api import build_fastapi_app",
    ],
)
def test_serving_locally_needs_no_http_client(target: str) -> None:
    """Serving `--auth local` should not want an outbound HTTP client."""

    result = _run(("httpx",), f"{target}\nprint('ok')")
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_the_oidc_adapter_still_says_what_it_needs() -> None:
    """Only the provider adapter itself should ask for an HTTP client."""

    result = _run(("httpx",), "import tide.api.browser_auth")
    assert result.returncode != 0
    assert "No module named 'httpx'" in result.stderr
