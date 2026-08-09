import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[1]


def test_windows_shortcut_has_consistent_windows_line_endings() -> None:
    raw = (ROOT / "start.bat").read_bytes()

    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_windows_shortcut_goto_targets_have_real_batch_labels() -> None:
    script = (ROOT / "start.bat").read_text(encoding="utf-8").lower()
    labels = set(re.findall(r"(?m)^:([a-z0-9_]+)\s*$", script))
    targets = set(re.findall(r"\bgoto\s+([a-z0-9_]+)", script))

    assert targets <= labels


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd.exe")
def test_contacts_web_demo_dispatches_to_its_batch_label(tmp_path: Path) -> None:
    shortcut = tmp_path / "start.bat"
    shutil.copy2(ROOT / "start.bat", shortcut)
    (tmp_path / ".tide").mkdir()
    (tmp_path / ".tide" / "contacts-local-auth.sqlite3").touch()
    (tmp_path / "web" / "node_modules").mkdir(parents=True)

    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    (command_dir / "npm.cmd").write_bytes(
        b"@echo CONTACTS_WEB_DEMO_DISPATCHED\r\nexit /b 0\r\n"
    )
    environment = os.environ.copy()
    environment["PATH"] = str(command_dir) + os.pathsep + environment["PATH"]

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "start.bat", "contacts-web-demo"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "CONTACTS_WEB_DEMO_DISPATCHED" in completed.stdout
    assert "cannot find the batch label" not in completed.stderr.lower()


def test_windows_shortcut_requests_mode_dependencies() -> None:
    script = (ROOT / "start.bat").read_text(encoding="utf-8").lower()

    assert (
        "uv run --extra tui --extra sqlserver tide run "
        "applications/invoicing --database-env" in script
    )
    assert (
        "uv run --extra seed --extra sqlserver tide db seed "
        "applications/invoicing --database-env" in script
    )
    assert "--role sales_clerk" in script
    assert "--count customers=25" in script
    assert "--count products=20" in script
    assert "--count invoices=100" in script
    assert "--customers" not in script
    assert "--products" not in script
    assert "--invoices" not in script
    assert (
        "uv run --extra sqlserver tide db check "
        "applications/invoicing --database-env" in script
    )
    assert (
        "uv run --extra sqlserver tide db diff "
        "applications/invoicing --database-env" in script
    )
    assert "uv run --extra tui tide run applications/invoicing --demo" in script
    assert "uv run --extra studio tide studio applications/invoicing" in script
    assert "--auth local --local-auth-store .tide/local-auth.sqlite3" in (
        (ROOT / "web" / "package.json").read_text(encoding="utf-8").lower()
    )
    assert (
        "uv run --extra gui --extra report tide gui applications/invoicing "
        "--api-url http://127.0.0.1:8000" in script
    )
    assert 'if /i "%~1"=="web" goto web' in script
    assert 'if /i "%~1"=="web-demo" goto web_demo' in script
    assert 'if /i "%~1"=="auth-user" goto auth_user' in script
    assert "tide auth create-user applications/invoicing" in script
    assert "call npm --prefix web run dev:sqlserver" in script
    assert "call npm --prefix web run dev:demo" in script
    assert "--view catalog.product.browse" in script
    assert "--view crm.customer.browse" in script
    assert (
        "uv run --extra tui tide run applications/contacts --demo "
        "--role contact_editor" in script
    )
    assert (
        "uv run --extra tui tide run applications/contacts --demo "
        "--role contact_viewer" in script
    )
    assert "uv run --extra studio tide studio applications/contacts" in script
    assert (
        "tide serve applications/contacts --demo --role contact_editor "
        "--role contact_viewer --port 8000" in script
    )
    assert "call npm --prefix web run dev:contacts-demo" in script
    assert (
        "tide auth create-user applications/contacts "
        "--store \".tide\\contacts-local-auth.sqlite3\"" in script
    )
    assert (
        "uv run --extra gui tide gui applications/contacts "
        "--api-url http://127.0.0.1:8000" in script
    )
    web_scripts = (
        (ROOT / "web" / "package.json").read_text(encoding="utf-8").lower()
    )
    assert "dev:contacts-demo" in web_scripts
    assert "tide serve applications/contacts --demo --auth local" in web_scripts
    assert ".venv\\scripts\\tide.exe" not in script
