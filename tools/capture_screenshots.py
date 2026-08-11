"""Regenerate the terminal screenshots the README and documentation show.

The first set of these was captured by hand in July and committed with no way
to make another. Six weeks later the invoice editor had gained a transition
guard, Studio had gained a toolbar, and nothing in the repository could say so:
a screenshot is documentation that cannot be link-checked, compiled, or run,
so the only defence against it going stale is being cheap to replace.

Each capture drives the real application through the same headless pilot the
Textual suites use, against the bundled invoicing project and its deterministic
demo data. Nothing is staged: if a screen looks wrong here, it looks wrong to a
person running `tide run applications/invoicing --demo`.

    uv run python tools/capture_screenshots.py

Sizes are per capture and deliberate. A hero image is rendered at the width it
is read at; the two that sit side by side in a table are narrower, because a
140-column terminal halved by a two-column layout is not legible on GitHub.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from textual.containers import Horizontal
from textual.pilot import Pilot
from textual.widgets import DataTable, Tree

from tide import compile_project
from tide.data import InMemoryRepository
from tide.development import StudioService
from tide.runtime import Channel, Principal, RequestContext
from tide.services import ActionService, RecordsService
from tide.tui import StudioApp, TideApp, configure_application_runtime, seed_demo_data
from tide.tui.form import RecordEditScreen
from tide.tui.lookup import LookupField, LookupScreen

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
IMAGES = ROOT / "docs" / "images"

WAIT_TIMEOUT_SECONDS = 15.0
"""Longer than the suite's, because a capture run has no retry and no worker.

A wait that gives up early here produces a screenshot of a half-drawn screen
rather than a failure, which is the one outcome worth spending seconds to
avoid.
"""


async def _wait_until(
    pilot: Pilot[Any],
    condition: Callable[[], bool],
    description: str,
) -> None:
    """Drain Textual messages until the screen is the one being captured."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_TIMEOUT_SECONDS
    while not condition():
        if loop.time() >= deadline:
            raise TimeoutError(f"Textual did not reach {description}")
        await pilot.pause(0.01)
    # Twice, with a pause between: Textual can pass through a state on its way
    # somewhere else, and a screenshot of the moment in between is worse than
    # no screenshot at all.
    await pilot.pause()
    await pilot.pause()


def _demo_application(*, page_size: int = 12) -> TideApp:
    """The application `tide run applications/invoicing --demo` starts."""

    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    seed_demo_data(model, repository)
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    configure_application_runtime(model, records, actions)
    return TideApp(
        model,
        records,
        RequestContext(
            principal=Principal("demo:user", roles=frozenset({"sales_clerk"})),
            channel=Channel.TUI,
        ),
        actions=actions,
        page_size=page_size,
        source_label="demo data",
    )


async def _capture_invoice_browser(destination: Path) -> None:
    """The screen a person sees first: a browse over the invoices."""

    app = _demo_application()
    async with app.run_test(size=(126, 25)) as pilot:
        table = app.screen.query_one("#records", DataTable)
        await _wait_until(
            pilot,
            lambda: table.row_count > 0,
            "a populated invoice browse",
        )
        _write(
            destination,
            app.export_screenshot(title="tide run applications/invoicing --demo"),
        )


async def _capture_invoice_editor(destination: Path) -> None:
    """A master-detail form: header fields, invoice lines, computed totals."""

    app = _demo_application()
    async with app.run_test(size=(102, 30)) as pilot:
        await pilot.pause()
        app.open_record(2)
        await _wait_until(
            pilot,
            lambda: isinstance(app.screen, RecordEditScreen)
            and len(app.screen.query("#collection-records")) == 1,
            "the invoice editor",
        )
        _write(destination, app.export_screenshot(title="Edit invoice INV-2026-0002"))


async def _capture_product_lookup(destination: Path) -> None:
    """The searchable reference picker, opened from an invoice line."""

    app = _demo_application()
    async with app.run_test(size=(102, 30)) as pilot:
        await pilot.pause()
        app.open_record(2)
        await _wait_until(
            pilot,
            lambda: isinstance(app.screen, RecordEditScreen)
            and len(app.screen.query("#line-product")) == 1,
            "the invoice editor",
        )
        app.screen.query_one("#line-product", LookupField).focus()
        await pilot.press("space")
        await _wait_until(
            pilot,
            lambda: isinstance(app.screen, LookupScreen)
            and app.screen.query_one("#lookup-results", DataTable).row_count > 0,
            "a populated product lookup",
        )
        _write(destination, app.export_screenshot(title="Select a product"))


async def _capture_studio(destination: Path) -> None:
    """Studio with a view selected: its fields, its layout, and its source.

    `sales.Invoice.edit` rather than the tree's default selection, because the
    view-structure panel is the half of Studio a screenshot has to earn -- an
    entity's property table alone looks like a YAML file with extra steps.
    """

    app = StudioApp(StudioService(INVOICING))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        tree = app.query_one("#studio-tree", Tree)
        views = tree.root.children[2]
        tree.select_node(
            next(
                node
                for node in views.children
                if node.label.plain == "sales.Invoice.edit"
            )
        )
        table = app.query_one("#view-field-table", DataTable)
        await _wait_until(
            pilot,
            lambda: app.view_structure is not None
            and app.query_one("#view-structure", Horizontal).display
            and table.row_count > 0,
            "a Studio view structure",
        )
        _write(
            destination,
            app.export_screenshot(title="tide studio applications/invoicing"),
        )


def _write(destination: Path, svg: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(svg, encoding="utf-8")
    print(f"wrote {destination.relative_to(ROOT).as_posix()}")


CAPTURES: tuple[tuple[str, Callable[[Path], Awaitable[None]]], ...] = (
    ("tide-invoice-browser", _capture_invoice_browser),
    ("tide-invoice-editor", _capture_invoice_editor),
    ("tide-product-lookup", _capture_product_lookup),
    ("tide-studio", _capture_studio),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "names",
        nargs="*",
        metavar="NAME",
        help=f"capture only these ({', '.join(name for name, _ in CAPTURES)})",
    )
    arguments = parser.parse_args(argv)

    selected = [
        (name, capture)
        for name, capture in CAPTURES
        if not arguments.names or name in arguments.names
    ]
    unknown = set(arguments.names) - {name for name, _ in CAPTURES}
    if unknown:
        parser.error(f"unknown capture: {', '.join(sorted(unknown))}")

    for name, capture in selected:
        asyncio.run(capture(IMAGES / f"{name}.svg"))
    return 0


if __name__ == "__main__":  # pragma: no cover - a developer tool
    raise SystemExit(main())
