from pathlib import Path


ROOT = Path(__file__).parents[1]


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
    assert ".venv\\scripts\\tide.exe" not in script
