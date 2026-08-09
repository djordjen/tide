"""What the Windows shortcut actually runs, asked of cmd.exe rather than of a
regular expression.

These used to be substring assertions, which made the test a second copy of the
script: it agreed with whatever was written, including `--customers` for weeks
after the CLI had replaced that flag. Stubbing `uv`, `npm` and `powershell` and
letting cmd.exe dispatch for real means the expected command line below is the
command line a developer gets.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable

import pytest


ROOT = Path(__file__).parents[1]
SHORTCUT = ROOT / "start.bat"

# `%*` reproduces the caller's spacing, and an application that leaves one of
# the settings empty leaves a double space behind it. The shell does not care
# and neither does this.
SPACING = re.compile(r"\s+")


def test_windows_shortcut_has_consistent_windows_line_endings() -> None:
    raw = SHORTCUT.read_bytes()

    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_windows_shortcut_goto_targets_have_real_batch_labels() -> None:
    script = SHORTCUT.read_text(encoding="utf-8").lower()
    labels = set(re.findall(r"(?m)^:([a-z0-9_]+)\s*$", script))
    targets = set(re.findall(r"\bgoto\s+([a-z0-9_]+)", script))

    assert targets <= labels


def instructions() -> str:
    """The script with its commentary removed.

    The comments explain which flags were retired and why, so a check for a
    retired flag has to read the instructions rather than the prose about them.
    """

    return "\n".join(
        line
        for line in SHORTCUT.read_text(encoding="utf-8").lower().splitlines()
        if not line.strip().startswith("rem")
    )


def test_no_command_is_written_twice_for_a_second_application() -> None:
    """The whole point of the settings blocks, stated as an invariant.

    Every application-aware command names `applications/%APP_ID%`. If a command
    ever names Contacts directly it has been copied, and the copy is what drifts
    the next time the `tide` CLI changes under it.
    """

    script = instructions()

    assert "applications/contacts" not in script
    assert script.count("applications/%app_id%") >= 7
    # Invoicing is still named outright, but only by commands that reach a SQL
    # Server deployment or a running server -- never by one that starts demo
    # data, because those are the ones any application can use.
    named = [line for line in script.splitlines() if "applications/invoicing" in line]
    assert named, script
    assert all(
        "--database-env" in line or "--api-url" in line or "--url" in line
        for line in named
    ), named


def test_the_seed_shortcut_uses_the_generic_count_flags() -> None:
    script = instructions()

    assert "--role sales_clerk" in script
    assert "--count customers=25 --count products=20 --count invoices=100" in script
    for retired in ("--customers", "--products", "--invoices"):
        assert retired not in script
    assert ".venv\\scripts\\tide.exe" not in script


def test_the_web_package_has_no_entry_per_application() -> None:
    """`dev:app` takes the application; adding one is not a package.json edit."""

    scripts = (ROOT / "web" / "package.json").read_text(encoding="utf-8")

    assert '"dev:app": "node scripts/dev-app.mjs"' in scripts
    assert "contacts" not in scripts
    assert "tide serve" not in scripts


DEV_SERVER: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("--app", "invoicing", "--extra", "report"),
        "cd .. && uv run --extra api --extra client --extra report tide serve"
        " applications/invoicing --demo --auth local"
        " --local-auth-store .tide/local-auth.sqlite3 --port 8000",
    ),
    (
        ("--app", "contacts"),
        "cd .. && uv run --extra api --extra client tide serve"
        " applications/contacts --demo --auth local"
        " --local-auth-store .tide/contacts-local-auth.sqlite3 --port 8000",
    ),
    (
        # An application with no settings anywhere still starts: the store
        # follows from its name. That is what makes a third one free.
        ("--app", "payroll"),
        "cd .. && uv run --extra api --extra client tide serve"
        " applications/payroll --demo --auth local"
        " --local-auth-store .tide/payroll-local-auth.sqlite3 --port 8000",
    ),
    (
        ("--app", "invoicing", "--extra", "report", "--extra", "sqlserver",
         "--database-env"),
        "cd .. && uv run --extra api --extra client --extra report"
        " --extra sqlserver tide serve applications/invoicing --database-env"
        " --auth local --local-auth-store .tide/local-auth.sqlite3 --port 8000",
    ),
)


@pytest.mark.skipif(shutil.which("node") is None, reason="requires Node.js")
@pytest.mark.parametrize(("arguments", "expected"), DEV_SERVER)
def test_the_web_launcher_composes_the_documented_serve_command(
    arguments: tuple[str, ...],
    expected: str,
) -> None:
    """`--print` is the dry run; nothing is started to check what would be."""

    completed = subprocess.run(
        ["node", "scripts/dev-app.mjs", "--print", *arguments],
        cwd=ROOT / "web",
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert SPACING.sub(" ", completed.stdout).strip() == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="requires Node.js")
def test_the_web_launcher_refuses_a_value_that_could_carry_a_shell() -> None:
    completed = subprocess.run(
        ["node", "scripts/dev-app.mjs", "--print", "--app", "a&calc"],
        cwd=ROOT / "web",
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "not a name or a path" in completed.stderr


@pytest.fixture
def shortcut(tmp_path: Path) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run the real shortcut with every external command replaced by an echo."""

    copied = tmp_path / "start.bat"
    shutil.copy2(SHORTCUT, copied)
    (tmp_path / "web" / "node_modules").mkdir(parents=True)
    (tmp_path / ".tide").mkdir()
    for store in ("local-auth.sqlite3", "contacts-local-auth.sqlite3"):
        (tmp_path / ".tide" / store).touch()

    # The stubs are batch files, and cmd.exe *chains* to a batch file invoked
    # without `call` rather than returning to its caller. The real `uv` is an
    # executable, so the shortcut is right to invoke it bare -- but it means a
    # stubbed command is the last thing that runs, and these cases assert the
    # command line rather than anything after it. `npm` is genuinely `npm.cmd`
    # on Windows, which is why the shortcut calls that one with `call`.
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, line in (
        ("uv", b"UV %*"),
        ("npm", b"NPM %*"),
        # Stands in for both the token prompt and the generated token, so the
        # commands behind them can be checked without a human at the keyboard.
        ("powershell", b"development-token"),
    ):
        (stubs / f"{name}.cmd").write_bytes(b"@echo " + line + b"\r\n@exit /b 0\r\n")

    environment = os.environ.copy()
    environment["PATH"] = str(stubs) + os.pathsep + environment["PATH"]

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        # Named by full path rather than relying on cmd.exe finding it in the
        # working directory: that lookup is off wherever
        # `NoDefaultCurrentDirectoryInExePath` is set, and the failure reads as
        # a broken shortcut ("'start.bat' is not recognized") rather than as a
        # hostile environment.
        return subprocess.run(
            ["cmd.exe", "/d", "/c", str(copied), *arguments],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    return run


def commands(completed: subprocess.CompletedProcess[str]) -> list[str]:
    return [
        SPACING.sub(" ", line).strip()
        for line in completed.stdout.splitlines()
        if line.startswith(("UV ", "NPM "))
    ]


DISPATCH: tuple[tuple[str, str], ...] = (
    (
        "demo",
        "UV run --extra tui tide run applications/invoicing --demo",
    ),
    (
        "demo contacts",
        "UV run --extra tui tide run applications/contacts --demo"
        " --role contact_editor",
    ),
    (
        "contacts-demo",
        "UV run --extra tui tide run applications/contacts --demo"
        " --role contact_editor",
    ),
    (
        "auditor-demo",
        "UV run --extra tui tide run applications/invoicing --demo --role auditor",
    ),
    (
        "contacts-viewer-demo",
        "UV run --extra tui tide run applications/contacts --demo"
        " --role contact_viewer",
    ),
    ("studio", "UV run --extra studio tide studio applications/invoicing"),
    ("studio contacts", "UV run --extra studio tide studio applications/contacts"),
    (
        "api-demo",
        "UV run --extra api --extra client --extra report tide serve"
        " applications/invoicing --demo --role sales_clerk --role auditor"
        " --port 8000",
    ),
    (
        "contacts-api-demo",
        "UV run --extra api --extra client tide serve applications/contacts"
        " --demo --role contact_editor --role contact_viewer --port 8000",
    ),
    (
        "mcp-demo contacts",
        "UV run --extra api --extra client --extra mcp tide serve"
        " applications/contacts --demo --role contact_editor"
        " --role contact_viewer --port 8000 --mcp",
    ),
    (
        "web-demo",
        "NPM --prefix web run dev:app -- --app invoicing --extra report",
    ),
    ("contacts-web-demo", "NPM --prefix web run dev:app -- --app contacts"),
    ("web-demo contacts", "NPM --prefix web run dev:app -- --app contacts"),
    (
        "gui",
        "UV run --extra gui --extra report tide gui applications/invoicing"
        " --api-url http://127.0.0.1:8000",
    ),
    (
        "contacts-gui",
        "UV run --extra gui tide gui applications/contacts"
        " --api-url http://127.0.0.1:8000",
    ),
    (
        "gui-customers",
        "UV run --extra gui --extra report tide gui applications/invoicing"
        " --api-url http://127.0.0.1:8000 --view crm.Customer.browse",
    ),
    (
        "seed",
        "UV run --extra seed --extra sqlserver tide db seed"
        " applications/invoicing --database-env --role sales_clerk"
        " --count customers=25 --count products=20 --count invoices=100"
        " --random-seed 20260716",
    ),
)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd.exe")
@pytest.mark.parametrize(("invocation", "expected"), DISPATCH, ids=[c for c, _ in DISPATCH])
def test_each_shortcut_runs_the_command_it_names(
    shortcut: Any,
    invocation: str,
    expected: str,
) -> None:
    completed = shortcut(*invocation.split(" "))

    assert completed.returncode == 0, completed.stderr
    assert "cannot find the batch label" not in completed.stderr.lower()
    assert commands(completed) == [expected]


def application_stores() -> dict[str, str]:
    """Every application's identity store, as the shortcut sets it."""

    blocks = re.findall(
        r"(?ms)^:app_(\w+)$(.*?)^exit /b 0$",
        SHORTCUT.read_text(encoding="utf-8"),
    )
    found = {}
    for name, body in blocks:
        store = re.search(r'set "APP_STORE=(.+?)"', body)
        assert store, f":app_{name} sets no store"
        found[name] = store.group(1).replace("\\", "/")
    return found


@pytest.mark.skipif(shutil.which("node") is None, reason="requires Node.js")
def test_the_shortcut_and_the_web_launcher_agree_on_every_identity_store() -> None:
    """Two applications must not share one store, or one set of credentials.

    The two surfaces reach the store by different routes -- the shortcut sets it
    so it can create the administrator, the launcher passes it to `tide serve`
    -- and a disagreement would authenticate against one file while the account
    lives in another. This is also the path `.gitignore` has to cover, which
    `test_api_hardening` asserts separately.
    """

    stores = application_stores()

    assert set(stores) == {"invoicing", "contacts"}
    for application, expected in stores.items():
        printed = subprocess.run(
            ["node", "scripts/dev-app.mjs", "--print", "--app", application],
            cwd=ROOT / "web",
            capture_output=True,
            text=True,
            check=False,
        )

        assert printed.returncode == 0, printed.stderr
        assert f"--local-auth-store {expected} " in f"{printed.stdout.strip()} "


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd.exe")
def test_an_unknown_application_is_refused_rather_than_defaulted(
    shortcut: Any,
) -> None:
    """Silently falling back to Invoicing would run the wrong application."""

    completed = shortcut("web-demo", "payroll")

    assert completed.returncode == 2
    assert "unknown application: payroll" in completed.stdout.lower()
    assert commands(completed) == []
