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
    assert (
        "tide serve applications/invoicing --demo --role sales_clerk "
        "--role auditor --port 8000" in script
    )
    assert ".venv\\scripts\\tide.exe" not in script
