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

from pathlib import Path
import subprocess
import sys
import textwrap
import tomllib

from packaging.requirements import Requirement
import pytest


ROOT = Path(__file__).parents[1]

# Installed with `--extra dev` for the test run and so never blocked here.
TOOLING = frozenset({"build", "mypy", "pytest", "pytest-cov", "pytest-xdist", "ruff", "types-pyyaml"})
# Extras deliberately outside `dev`: PySide6 is installed only on the Windows
# CI job, and pyodbc needs a system driver no runner is guaranteed to have.
OUTSIDE_DEV = frozenset({"pyside6-essentials", "pyodbc"})
# Where a distribution and its import name differ. Anything not listed is
# imported under its own lowercased name, and `test_every_extra_has_a_module`
# fails on a package this does not know how to name.
IMPORT_NAMES = {"pyjwt": "jwt", "pyside6-essentials": "PySide6"}


def extras() -> dict[str, list[Requirement]]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        name: [Requirement(entry) for entry in entries]
        for name, entries in data["project"]["optional-dependencies"].items()
    }


def module_name(requirement: Requirement) -> str:
    package = requirement.name.lower()
    return IMPORT_NAMES.get(package, package)


# Derived rather than listed. The hand-written version had ten entries and the
# extras had eleven packages: PySide6 was never blocked, so a subprocess that
# imported it would have passed on the one CI job that has it installed.
EVERY_EXTRA = tuple(
    sorted(
        {
            module_name(requirement)
            for name, requirements in extras().items()
            if name != "dev"
            for requirement in requirements
        }
    )
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


def test_the_dev_extra_still_covers_every_extra_it_stands_in_for() -> None:
    """`uv sync --extra dev` is the whole test environment, assembled by hand.

    It is a union of nine other extras kept in step by copying, so a new
    dependency or a moved version bound goes quietly missing and the suite
    stops exercising a path everyone believes it covers. Checked in both
    directions: nothing an extra declares may be absent from `dev`, and
    nothing in `dev` may be there without being either a tool or some extra's.
    """

    declared = extras()
    dev = {requirement.name.lower(): requirement for requirement in declared["dev"]}
    elsewhere = {
        requirement.name.lower()
        for name, requirements in declared.items()
        if name != "dev"
        for requirement in requirements
    }

    missing = []
    for name, requirements in sorted(declared.items()):
        if name == "dev":
            continue
        for requirement in requirements:
            package = requirement.name.lower()
            if package in OUTSIDE_DEV:
                continue
            held = dev.get(package)
            if held is None:
                missing.append(f"{name}: {requirement} is absent from dev")
            elif held.specifier != requirement.specifier:
                missing.append(f"{name}: {requirement}, but dev pins {held}")
            elif not requirement.extras <= held.extras:
                missing.append(f"{name}: {requirement}, but dev has {held}")

    assert missing == []

    unexplained = sorted(
        requirement.name
        for requirement in declared["dev"]
        if requirement.name.lower() not in TOOLING
        and requirement.name.lower() not in elsewhere
    )
    assert unexplained == []

    for package in sorted(OUTSIDE_DEV):
        assert package not in dev, f"{package} is in dev now; drop it from OUTSIDE_DEV"


def test_the_block_list_names_every_extra_including_the_awkward_ones() -> None:
    """`EVERY_EXTRA` is derived, so this is what says the derivation is right.

    `PyJWT` imports as `jwt` and `PySide6-Essentials` as `PySide6`; a plain
    lowercased distribution name blocks neither, and a package this cannot name
    is a package the harness above silently fails to block.
    """

    packages = {
        requirement.name.lower()
        for name, requirements in extras().items()
        if name != "dev"
        for requirement in requirements
    }

    assert {"jwt", "PySide6"} <= set(EVERY_EXTRA)
    assert len(EVERY_EXTRA) == len(packages)
    # No exception may outlive the package that needed it.
    assert set(IMPORT_NAMES) <= packages
    assert OUTSIDE_DEV <= packages
