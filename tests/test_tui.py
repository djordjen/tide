from __future__ import annotations

import asyncio
from pathlib import Path

from textual.widgets import Button, DataTable, Static

from tide import compile_project
from tide.cli import main
from tide.data import InMemoryRepository
from tide.runtime import Channel, Principal, RequestContext
from tide.services import RecordsService
from tide.tui import TideApp, seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def test_textual_invoice_browse_pages_by_keyboard_and_mouse() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#records", DataTable)
            assert table.row_count == 3
            assert table.get_row_at(0) == [
                "INV-2026-0001",
                "01.07.2026",
                "ADRIA - Adria Consulting",
                "Posted",
                "850.00",
            ]
            assert app.page_number == 1
            assert app.query_one("#previous-page", Button).disabled
            assert not app.query_one("#next-page", Button).disabled

            await pilot.click("#next-page")
            await pilot.pause()
            assert app.page_number == 2
            assert table.get_row_at(0)[0] == "INV-2026-0004"

            await pilot.press("p")
            await pilot.pause()
            assert app.page_number == 1
            assert table.get_row_at(0)[0] == "INV-2026-0001"

            await pilot.press("n")
            await pilot.press("n")
            await pilot.pause()
            assert app.page_number == 3
            assert table.row_count == 2
            assert app.query_one("#next-page", Button).disabled

            await pilot.press("r")
            await pilot.pause()
            assert app.page_number == 1
            assert "Page 1" in str(app.query_one("#browse-status", Static).content)

    asyncio.run(exercise())


def test_textual_reference_display_fails_closed_without_target_access() -> None:
    app = _demo_app(page_size=1, role="summary_viewer")

    async def exercise() -> None:
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            row = app.query_one("#records", DataTable).get_row_at(0)
            assert row[2] == "Protected"
            assert "Adria Consulting" not in repr(row)

    asyncio.run(exercise())


def test_tide_run_demo_constructs_textual_app(monkeypatch) -> None:
    launched: list[TideApp] = []
    monkeypatch.setattr(TideApp, "run", lambda self: launched.append(self))

    result = main(
        [
            "run",
            str(INVOICING),
            "--demo",
            "--page-size",
            "3",
        ]
    )

    assert result == 0
    assert len(launched) == 1
    app = launched[0]
    assert app.view.name == "sales.Invoice.browse"
    assert app.page_size == 3
    assert app.context.principal.roles == frozenset({"sales_clerk"})
    assert len(app.records.repository.all("sales.Invoice")) == 8


def _demo_app(*, page_size: int, role: str = "sales_clerk") -> TideApp:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    context = RequestContext(
        principal=Principal("demo:user", roles=frozenset({role})),
        channel=Channel.TUI,
    )
    return TideApp(
        model,
        records,
        context,
        page_size=page_size,
        source_label="demo data",
    )
