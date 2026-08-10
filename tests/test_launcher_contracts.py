"""Everything that spells out a `tide` command agrees with the parser.

Launchers, the Windows shortcut, and the documentation. The docs are the
largest of the three by far -- 106 invocations a reader is invited to copy --
and the only one where the runtime is a person, who gets an argparse error
rather than a failing build.

Two Node scripts under `web/` independently compose a `tide serve` command
line: the Playwright launcher and the dev-server runner. Merging them was
considered and refused -- most of each file is different work -- so the
duplication stands, and these are what make it safe.

A retired or renamed flag is the failure this catches. It would not be caught
by the launchers' own tests: `--print` asserts the string the launcher was
written to produce, which agrees with itself no matter what the CLI accepts.
Nor evenly by CI: the Playwright launcher is executed for real on every push,
so a bad flag turns the web job red, while the dev runner is never executed by
any job at all.

The parser is introspected rather than shelled out to, so this costs
milliseconds and reports per-subcommand rather than one flat list of every
option TIDE has.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from tide.cli.main import _create_parser

ROOT = Path(__file__).parents[1]
WEB = ROOT / "web"
DEV_LAUNCHER = WEB / "scripts" / "dev-app.mjs"
E2E_LAUNCHER = WEB / "tests" / "e2e" / "tide-server.mjs"

DOCUMENTS = sorted(
    {
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "applications").rglob("README.md"),
    }
)
# Only fences a reader would expect to run. An example of a command TIDE does
# not have yet belongs in a `text` fence, which is what this repository already
# uses for illustrations -- `docs/ROADMAP.md` sketches the CLI it is growing
# towards, and `docs/APPLICATION-MODEL.md` a migration workflow that does not
# exist. Both say so in prose; the fence is what says it to a copy-paste.
RUNNABLE = re.compile(r"```(?:bash|sh|shell|console|powershell|pwsh)\n(.*?)```", re.S)
# PowerShell continues a line with a backtick, POSIX shells with a backslash.
CONTINUATION = re.compile("[" + chr(96) + "\\\\]\\s*\\n\\s*")

# Flags in the launchers that belong to something other than `tide`.
CONCURRENTLY = frozenset({"--kill-others", "--names", "--prefix-colors"})
# The dev runner's own options, consumed before a command is built.
OWN = frozenset({"--app", "--extra", "--store", "--print"})


def subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """argparse exposes no public way to reach a subparser it created."""

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def declared(*path: str) -> frozenset[str]:
    """The long options one `tide` subcommand accepts, e.g. `("auth", "create-user")`."""

    parser = _create_parser()
    for name in path:
        children = subcommands(parser)
        assert name in children, f"`tide {' '.join(path)}` no longer exists"
        parser = children[name]
    return frozenset(
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--")
    )


def mentioned(launcher: Path) -> frozenset[str]:
    return frozenset(re.findall(r'"(--[a-z][a-z0-9-]*)"', launcher.read_text(encoding="utf-8")))


def documented() -> list[tuple[str, str, list[str]]]:
    """Every `tide …` invocation a reader is invited to run, as (doc, line, tokens)."""

    found = []
    for document in DOCUMENTS:
        for block in RUNNABLE.findall(document.read_text(encoding="utf-8")):
            for raw in CONTINUATION.sub(" ", block).splitlines():
                line = raw.strip()
                if line.startswith("#") or not re.search(r"(^|\s)tide\s", line):
                    continue
                try:
                    tokens = shlex.split(line, posix=False)
                except ValueError:  # pragma: no cover - an unbalanced quote
                    continue
                if "tide" in tokens:
                    found.append(
                        (
                            document.relative_to(ROOT).as_posix(),
                            line,
                            tokens[tokens.index("tide") + 1 :],
                        )
                    )
    return found


def walk(tokens: list[str]) -> tuple[argparse.ArgumentParser, list[str], list[str]]:
    """Follow a documented invocation down to the subparser it names.

    Returns the parser reached, the path taken, and what is left. A group
    command takes no positional of its own, so while one has subcommands the
    next bare word has to be one of them.
    """

    parser, path, rest = _create_parser(), [], list(tokens)
    while True:
        children = subcommands(parser)
        if not children or not rest or rest[0].startswith("-"):
            return parser, path, rest
        name = rest.pop(0)
        if name not in children:
            path.append(f"<unknown:{name}>")
            return parser, path, rest
        path.append(name)
        parser = children[name]


@pytest.mark.skipif(shutil.which("node") is None, reason="requires Node.js")
def test_the_dev_launcher_composes_a_command_tide_serve_accepts() -> None:
    """Asked of the command it actually builds, not of the source text."""

    printed = subprocess.run(
        ["node", "scripts/dev-app.mjs", "--print", "--app", "contacts"],
        cwd=WEB,
        capture_output=True,
        text=True,
        check=False,
    )

    assert printed.returncode == 0, printed.stderr
    tokens = printed.stdout.split()
    assert "serve" in tokens, printed.stdout
    passed = {token for token in tokens[tokens.index("serve"):] if token.startswith("--")}

    assert passed, printed.stdout
    assert passed <= declared("serve"), sorted(passed - declared("serve"))


def test_the_e2e_launcher_names_no_flag_the_cli_has_retired() -> None:
    """Read statically: this one has no dry run, and adding one to Playwright's
    launcher would mean restructuring the setup order it documents at length.

    It invokes two subcommands, so the check is against their union. That is
    coarser than the dev runner's -- it would not notice a `serve` flag being
    passed to `create-user` -- but a *retired* flag, the drift this exists for,
    still has nowhere to hide.
    """

    accepted = declared("serve") | declared("auth", "create-user") | CONCURRENTLY | OWN
    passed = mentioned(E2E_LAUNCHER)

    assert passed, E2E_LAUNCHER
    assert passed <= accepted, sorted(passed - accepted)


def test_both_launchers_still_agree_on_how_the_server_authenticates() -> None:
    """The duplication is only tolerable while the copies say the same thing.

    Both stand up a server behind a TIDE-owned identity store. If one is moved
    to a different auth mechanism or store flag and the other is not, the two
    stop testing and demonstrating the same product.
    """

    shared = {"--auth", "--local-auth-store", "--port", "--demo"}

    for launcher in (DEV_LAUNCHER, E2E_LAUNCHER):
        assert shared <= mentioned(launcher), (
            launcher.name,
            sorted(shared - mentioned(launcher)),
        )


def test_the_windows_shortcut_names_no_flag_the_cli_has_retired() -> None:
    """`start.bat` is the third launcher, and the one that already drifted.

    It passed `--customers` for weeks after `tide db seed` replaced it with
    `--count NAME=NUMBER`. Nothing noticed until a second application arrived.
    """

    script = (ROOT / "start.bat").read_text(encoding="utf-8")
    instructions = "\n".join(
        line for line in script.splitlines() if not line.strip().lower().startswith("rem")
    )
    accepted = (
        declared("serve")
        | declared("run")
        | declared("studio")
        | declared("db", "seed")
        | declared("db", "check")
        | declared("db", "diff")
        | declared("auth", "create-user")
        | declared("api", "check-server")
        # Not `tide`'s: `--prefix` is npm's, `--extra` is `uv run`'s, and
        # `--app` is the dev launcher's, forwarded through `npm run dev:app`.
        | {"--prefix", "--extra", "--app"}
    )
    passed = frozenset(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", instructions))

    assert passed, script
    assert passed <= accepted, sorted(passed - accepted)


def test_every_documented_command_is_a_command_tide_has() -> None:
    """`tide api describe` was in "Useful commands" and has never existed."""

    invented = [
        (doc, line)
        for doc, line, tokens in documented()
        if any(step.startswith("<unknown:") for step in walk(tokens)[1])
    ]

    assert invented == []


def test_every_documented_command_passes_flags_that_subcommand_declares() -> None:
    """Checked against the subcommand named on the line, not a union.

    The launchers get a coarser check because a static read cannot tell which
    of two subcommands a flag was meant for. A documented line says so itself.
    """

    wrong = []
    for doc, line, tokens in documented():
        parser, path, rest = walk(tokens)
        if not path or any(step.startswith("<unknown:") for step in path):
            continue
        known = declared(*path)
        for token in rest:
            name = token.split("=")[0]
            if name.startswith("--") and name not in known:
                wrong.append((doc, f"tide {' '.join(path)}", name, line))

    assert wrong == []


def test_the_documentation_scan_reaches_the_commands_it_claims_to() -> None:
    """The two checks above pass on an empty list, so prove the list is full.

    They also only see fences a reader would run: the aspirational blocks in
    ROADMAP and APPLICATION-MODEL are `text`, and moving one back to `bash`
    should fail the check above rather than slip through here.
    """

    commands = documented()

    assert len(commands) > 90, len(commands)
    resolved = {" ".join(walk(tokens)[1]) for _, _, tokens in commands}
    assert {"serve", "run", "studio", "db seed", "model validate"} <= resolved
    assert not any("unknown" in name for name in resolved)
    assert len({doc for doc, _, _ in commands}) >= 8


def test_the_parser_introspection_would_notice_a_retired_flag() -> None:
    """The checks above are subset assertions, which pass on an empty set.

    This is what says the option lists are really being read.
    """

    assert "--local-auth-store" in declared("serve")
    assert "--count" in declared("db", "seed")
    assert "--customers" not in declared("db", "seed")
    assert declared("serve") != declared("run")
    assert os.path.isfile(DEV_LAUNCHER) and os.path.isfile(E2E_LAUNCHER)
