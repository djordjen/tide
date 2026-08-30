from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import shutil
import threading
from typing import Any

from rich.text import Text
from sqlalchemy import create_engine, inspect
from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Input, Select, Static, TabbedContent

from textual_support import wait_until
from tide import compile_project
from tide.cli import main
from tide.data import (
    InMemoryRepository,
    QuerySpec,
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemyRepository,
    framework_stores,
)
from tide.runtime import Channel, Principal, RequestContext
from tide.services import ActionService, AuditOutcome, RecordsService
from tide.tui import (
    TideApp,
    configure_application_runtime,
    seed_demo_data,
)
from tide.tui.audit import AuditHistoryScreen
from tide.tui.form import NumericMaskedInput, RecordEditScreen
from tide.tui.confirm import DeleteConfirmationScreen
from tide.tui.conflict import ConflictReviewScreen
from tide.tui.lookup import LookupField, LookupScreen
from tide.tui.parameters import ParametersScreen
from tide.tui.report import ReportPreviewScreen

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
PROJECTS = ROOT / "tests" / "fixtures" / "valid" / "projects"


async def _wait_until(
    pilot: Pilot[object],
    condition: Callable[[], bool],
) -> None:
    await wait_until(pilot, condition, description="the expected state")


def _delete_confirmation_ready(app: TideApp) -> bool:
    screen = app.screen
    return (
        isinstance(screen, DeleteConfirmationScreen)
        and len(screen.query("#confirm-delete")) == 1
        and len(screen.query("#cancel-delete")) == 1
    )


def _lookup_ready(
    app: TideApp,
    *,
    column_count: int | None = None,
    row_count: int | None = None,
) -> bool:
    screen = app.screen
    if not isinstance(screen, LookupScreen):
        return False
    tables = screen.query("#lookup-results")
    if len(tables) != 1:
        return False
    results = screen.query_one("#lookup-results", DataTable)
    return (
        (column_count is None or len(results.columns) == column_count)
        and (row_count is None or results.row_count == row_count)
    )


TWO_COLLECTION_PROJECT: dict[str, str] = {
    "tide.yaml": (
        'schema_version: "0.1"\n'
        "application: {name: Two Collections, version: 0.1.0}\n"
        "database: {mode: managed}\n"
        "model: {paths: [models]}\n"
        "views: {paths: [views]}\n"
        "security: {paths: [security]}\n"
    ),
    "models/parent.yaml": (
        "entity: demo.Parent\n"
        "display: name\n"
        "expose: {tui: true}\n"
        "permissions: {list: demo.all, read: demo.all, create: demo.all,"
        " update: demo.all, delete: demo.all}\n"
        "fields:\n"
        "  id: {type: integer, primary_key: true}\n"
        "  name: {type: string, length: 40, required: true}\n"
        "  firsts: {type: collection, target: demo.Child, inverse: first}\n"
        "  seconds: {type: collection, target: demo.Child, inverse: second}\n"
    ),
    "models/child.yaml": (
        "entity: demo.Child\n"
        "display: label\n"
        "expose: {tui: true}\n"
        "permissions: {list: demo.all, read: demo.all, create: demo.all,"
        " update: demo.all, delete: demo.all}\n"
        "fields:\n"
        "  id: {type: integer, primary_key: true}\n"
        "  label: {type: string, length: 40, required: true}\n"
        "  first: {type: reference, target: demo.Parent, on_delete: cascade}\n"
        "  second: {type: reference, target: demo.Parent, on_delete: cascade}\n"
    ),
    "views/parent-browse.yaml": (
        "view: demo.Parent.browse\nentity: demo.Parent\nkind: browse\n"
        "columns:\n- name\n"
    ),
    "views/parent-edit.yaml": (
        "view: demo.Parent.edit\nentity: demo.Parent\nkind: form\n"
        "layout:\n- group: Parent\n  rows:\n  - - name\n"
        "- collection: firsts\n  view: demo.Child.firsts.inline\n"
        "  actions: [add, apply, remove]\n"
        "- collection: seconds\n  view: demo.Child.seconds.inline\n"
        "  actions: [add, apply, remove]\n"
    ),
    "views/child-firsts-inline.yaml": (
        "view: demo.Child.firsts.inline\nentity: demo.Child\n"
        "kind: inline_edit\ncolumns:\n- label\n"
    ),
    "views/child-seconds-inline.yaml": (
        "view: demo.Child.seconds.inline\nentity: demo.Child\n"
        "kind: inline_edit\ncolumns:\n- label\n"
    ),
    "security/policies.yaml": (
        "permissions:\n- demo.all\nroles:\n  operator:\n    grants:\n    - demo.all\n"
    ),
}


def test_textual_form_with_two_collections_renders_both(
    tmp_path: Path,
) -> None:
    """One entity pointed at twice renders as two panes with the right rows.

    This is the XPO many-to-many shape: the same child entity reached through
    two inverse references. The screen used to compose one collection and
    silently skip the rest -- this test pinned that behaviour (`one table,
    once`) until each collection got a pane and ids of its own; now it pins
    the opposite: both tables mount, and the seeded child appears only in the
    collection whose inverse it fills.
    """

    project = tmp_path / "two"
    for path, text in TWO_COLLECTION_PROJECT.items():
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    model = compile_project(project)
    child = {"id": 1, "label": "First child", "first": 1, "second": None}
    repository = InMemoryRepository()
    repository.seed(
        "demo.Parent",
        ({"id": 1, "name": "Root", "firsts": [child], "seconds": []},),
    )
    repository.seed("demo.Child", (child,))
    app = TideApp(
        model,
        RecordsService(model, repository),
        RequestContext(
            principal=Principal("demo:user", roles=frozenset({"operator"})),
            channel=Channel.TUI,
        ),
    )

    async def scenario() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(1)
            await _wait_until(pilot, lambda: isinstance(app.screen, RecordEditScreen))
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            firsts = screen.query_one("#collection-records-firsts", DataTable)
            seconds = screen.query_one("#collection-records-seconds", DataTable)
            assert firsts.row_count == 1
            assert seconds.row_count == 0
            assert len(screen.query(DataTable)) == 2

    asyncio.run(scenario())


def test_textual_invoice_browse_incrementally_loads_on_scroll(monkeypatch) -> None:
    app = _demo_app(page_size=5)
    template = app.records.repository.get("sales.Invoice", 8)
    app.records.repository.seed(
        "sales.Invoice",
        (
            {
                **template,
                "id": identity,
                "number": f"INV-2026-{identity:04d}",
                "invoice_date": date(2026, 7, 15) + timedelta(days=identity - 8),
                "lines": [
                    {
                        **template["lines"][0],
                        "id": identity,
                        "invoice": identity,
                    }
                ],
            }
            for identity in range(9, 41)
        ),
    )
    gui_thread = threading.get_ident()
    query_threads: list[int] = []
    query_page = app.records.query_page

    def tracked_query_page(*args, **kwargs):
        query_threads.append(threading.get_ident())
        return query_page(*args, **kwargs)

    monkeypatch.setattr(app.records, "query_page", tracked_query_page)

    async def exercise() -> None:
        async with app.run_test(size=(120, 16)) as pilot:
            table = app.query_one("#records", DataTable)
            await _wait_until(
                pilot,
                lambda: table.row_count >= 5 and not app._query_loading,
            )
            initial_count = table.row_count
            assert 5 <= initial_count < 40
            row = table.get_row_at(0)
            assert [str(value) for value in row] == [
                "INV-2026-0001",
                "01.07.2026",
                "ADRIA - Adria Consulting",
                "Posted",
                "850.00",
            ]
            assert isinstance(row[-1], Text)
            assert row[-1].justify == "right"
            assert table.ordered_columns[-1].label.justify == "right"
            assert len(app.query("#previous-page")) == 0
            assert len(app.query("#next-page")) == 0
            assert query_threads
            assert all(thread != gui_thread for thread in query_threads)

            while table.row_count < 40:
                previous_count = table.row_count
                table.move_cursor(row=previous_count - 1)
                table.scroll_end(animate=False)
                await _wait_until(
                    pilot,
                    # bound as an argument: `previous_count` is rebound every
                    # pass, and a closure over it would compare against the
                    # count from whichever pass ran last
                    lambda before=previous_count: table.row_count > before
                    or app._next_cursor is None,
                )
            assert table.row_count == 40
            assert table.get_row_at(0)[0] == "INV-2026-0001"
            assert table.get_row_at(39)[0] == "INV-2026-0040"
            assert len(app.current_records) == 40
            status = str(app.query_one("#browse-status", Static).content)
            assert "40 records loaded" in status
            assert "All available records loaded" in status
            assert "Page " not in status

            await pilot.press("r")
            await _wait_until(
                pilot,
                lambda: table.row_count >= 5 and not app._query_loading,
            )
            assert table.get_row_at(0)[0] == "INV-2026-0001"

    asyncio.run(exercise())


def test_browse_updates_tolerate_a_screen_that_is_not_composed() -> None:
    """Browse workers may deliver results when the browse widgets are absent.

    ``_load_batch`` hops to a worker thread, so it can resume before compose
    finishes or while the screen is being torn down. ``App.is_mounted`` takes a
    widget argument and is not a boolean property, so testing it for truth never
    skips anything; the browse updates have to tolerate a missing widget.
    """

    app = _demo_app(page_size=3)

    app._apply_load_error(ValueError("connection reset"))
    app._update_record_controls()
    app._update_browse_status()
    app._prefetch_if_near_end()


def test_textual_browse_and_form_keep_actions_reachable_at_supported_sizes() -> None:
    for width, height in ((80, 24), (100, 30), (140, 40)):
        _assert_actions_stay_reachable(width, height)


def _assert_actions_stay_reachable(width: int, height: int) -> None:
    """Check one terminal size.

    Its own function rather than a body inside the loop: the coroutine below
    closes over the size, and a closure written in the loop reads whichever
    size the loop reached last -- so a regression at 80x24 could pass while
    silently measuring 140x40.
    """

    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(width, height)) as pilot:
            await pilot.pause()
            compact = width < 100
            assert ("compact-terminal" in app.screen.classes) is compact
            for button_id in (
                "create-record",
                "edit-record",
                "preview-report",
                "summary-report",
                "refresh-page",
                "quit-app",
            ):
                button = app.query_one(f"#{button_id}", Button)
                assert button.region.right <= width
                assert button.region.bottom <= height
            assert app.query_one("#named-filter", Select).display is not compact
            assert app.query_one("#sort-field", Select).display is not compact

            app.open_record(2)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            assert ("compact-terminal" in screen.classes) is compact
            for button_id in ("cancel-form", "save-form", "post-record"):
                button = screen.query_one(f"#{button_id}", Button)
                assert button.region.right <= width
                assert button.region.bottom <= height

            body = screen.query_one("#form-body")
            if compact:
                assert body.show_vertical_scrollbar
                assert body.max_scroll_y > 0
                body.scroll_end(animate=False)
                line_fields = screen.query_one("#line-fields-lines")
                await _wait_until(
                    pilot,
                    lambda: (
                        body.scroll_offset.y == body.max_scroll_y
                        and body.scrollable_content_region.contains_region(
                            line_fields.region
                        )
                    ),
                )
                assert body.scrollable_content_region.contains_region(
                    line_fields.region
                )

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, RecordEditScreen)

    asyncio.run(exercise())


def test_textual_compact_browse_preserves_wide_combining_and_rtl_text() -> None:
    app = _demo_app(page_size=10)
    unicode_name = "漢字 e\u0301 مرحبا"
    app.records.repository.seed(
        "crm.Customer",
        [
            {
                "id": 99,
                "code": "UNICODE",
                "name": unicode_name,
                "email": None,
                "active": True,
            }
        ],
    )

    async def exercise() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            workspace = app.query_one("#browse-view", Select)
            workspace.value = "crm.Customer.browse"
            await pilot.pause()

            table = app.query_one("#records", DataTable)
            rows = [table.get_row_at(index) for index in range(table.row_count)]
            assert any(row[0] == "UNICODE" and row[1] == unicode_name for row in rows)
            assert table.region.right <= app.size.width

    asyncio.run(exercise())


def test_textual_reference_display_fails_closed_without_target_access() -> None:
    app = _demo_app(page_size=1, role="summary_viewer")

    async def exercise() -> None:
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            row = app.query_one("#records", DataTable).get_row_at(0)
            assert row[2] == "Protected"
            assert "Adria Consulting" not in repr(row)
            assert app.query_one("#create-record", Button).disabled
            assert app.query_one("#edit-record", Button).disabled
            assert not app.query_one("#delete-record", Button).display
            assert not app.query_one("#preview-report", Button).display

    asyncio.run(exercise())


def test_textual_product_delete_confirms_cancels_and_reports_references(
    monkeypatch,
) -> None:
    app = _demo_app(page_size=10)
    app.records.repository.seed(
        "catalog.Product",
        [
            {
                "id": 4,
                "code": "TEMP",
                "name": "Temporary product",
                "unit_price": Decimal("1.00"),
                "active": True,
            }
        ],
    )
    notifications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app,
        "notify",
        lambda message, **options: notifications.append(
            (str(message), str(options.get("severity", "information")))
        ),
    )

    async def exercise() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.query_one("#browse-view", Select).value = "catalog.Product.browse"
            await pilot.pause()
            table = app.query_one("#records", DataTable)
            delete_button = app.query_one("#delete-record", Button)
            assert delete_button.display
            assert not delete_button.disabled
            assert table.row_count == 4

            table.move_cursor(row=3)
            await pilot.press("delete")
            await _wait_until(pilot, lambda: _delete_confirmation_ready(app))
            assert isinstance(app.screen, DeleteConfirmationScreen)
            assert "Temporary product" in str(
                app.screen.query_one("#delete-message", Static).content
            )
            app.screen.query_one("#cancel-delete", Button).press()
            await _wait_until(
                pilot,
                lambda: not isinstance(app.screen, DeleteConfirmationScreen),
            )
            assert app.records.repository.exists("catalog.Product", 4)

            delete_button.press()
            await _wait_until(
                pilot,
                lambda: _delete_confirmation_ready(app),
            )
            app.screen.query_one("#confirm-delete", Button).press()
            await _wait_until(
                pilot,
                lambda: not app.records.repository.exists("catalog.Product", 4)
                and table.row_count == 3
                and bool(notifications),
            )
            assert not app.records.repository.exists("catalog.Product", 4)
            assert table.row_count == 3
            assert notifications[-1] == (
                "TEMP - Temporary product deleted.",
                "information",
            )

            table.move_cursor(row=0)
            delete_button.press()
            await _wait_until(
                pilot,
                lambda: _delete_confirmation_ready(app),
            )
            app.screen.query_one("#confirm-delete", Button).press()
            await _wait_until(
                pilot,
                lambda: bool(notifications)
                and notifications[-1][1] == "warning",
            )
            assert app.records.repository.exists("catalog.Product", 1)
            assert notifications[-1] == (
                "Cannot delete 'CONS - Consulting hour': it is used by "
                "Invoice Lines (Product).",
                "warning",
            )

    asyncio.run(exercise())


def test_textual_customer_delete_is_permission_driven_and_compact_safe() -> None:
    app = _demo_app(page_size=10)
    app.records.repository.seed(
        "crm.Customer",
        [
            {
                "id": 4,
                "code": "TEMP",
                "name": "Temporary customer",
                "email": None,
                "active": True,
                "invoices": [],
            }
        ],
    )

    async def exercise() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.query_one("#browse-view", Select).value = "crm.Customer.browse"
            await pilot.pause()
            table = app.query_one("#records", DataTable)
            delete_button = app.query_one("#delete-record", Button)
            assert delete_button.display
            assert delete_button.region.right <= 80
            assert delete_button.region.bottom <= 24

            table.move_cursor(row=3)
            delete_button.press()
            await _wait_until(
                pilot,
                lambda: _delete_confirmation_ready(app),
            )
            app.screen.query_one("#confirm-delete", Button).press()
            await _wait_until(
                pilot,
                lambda: not app.records.repository.exists("crm.Customer", 4)
                and table.row_count == 3,
            )
            assert not app.records.repository.exists("crm.Customer", 4)
            assert table.row_count == 3

    asyncio.run(exercise())


def _projects_app() -> TideApp:
    """A record with two collections, which neither checked-in application has."""

    model = compile_project(PROJECTS)
    repository = InMemoryRepository()
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    # The fixture declares no runtime.py, so this verifies and returns False.
    configure_application_runtime(model, records, actions)
    context = RequestContext(
        principal=Principal("plan:user", roles=frozenset({"editor"})),
        channel=Channel.TUI,
    )
    stored = records.commit(
        records.create(
            "plan.Project",
            context,
            {
                "name": "Alpha",
                "tasks": [{"title": "Survey", "estimate": Decimal("4.00")}],
                "members": [{"person": "Mira", "role_name": "Lead"}],
            },
        ),
        context,
    )
    assert stored["id"] == 1
    return TideApp(
        model,
        records,
        context,
        actions=actions,
        page_size=10,
        source_label="fixture",
    )


def test_textual_form_renders_every_collection_and_scopes_its_actions() -> None:
    """Both collections are on the form, and the line actions follow the focus.

    The screen used to resolve one collection and silently skip the rest, so
    the terminal and the browser disagreed about what a record contains. Now
    each collection owns its table and editors, and the one bar of line
    actions acts on whichever collection the focus is in -- including its
    declared action order, which is why Members deliberately has no remove.
    """

    app = _projects_app()

    async def exercise() -> None:
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            app.open_record(1)
            await _wait_until(
                pilot, lambda: isinstance(app.screen, RecordEditScreen)
            )
            screen = app.screen

            tasks_table = screen.query_one("#collection-records-tasks", DataTable)
            members_table = screen.query_one(
                "#collection-records-members", DataTable
            )
            assert tasks_table.row_count == 1
            assert members_table.row_count == 1

            # Tasks is the first collection and starts active; its declared
            # action order includes remove.
            assert screen.query_one("#remove-line", Button).display

            # Focus the members table: the bar follows, and Members has none.
            members_table.focus()
            await pilot.pause()
            assert not screen.query_one("#remove-line", Button).display

            # Selecting a row loads that pane's editors, not the first pane's.
            await pilot.press("enter")
            await pilot.pause()
            assert (
                screen.query_one("#line-members--person", Input).value == "Mira"
            )

            # Add through the same bar: the line lands in Members, not Tasks.
            screen.query_one("#add-line", Button).press()
            await pilot.pause()
            assert members_table.row_count == 2
            assert tasks_table.row_count == 1
            screen.query_one("#line-members--person", Input).value = "Novak"
            # Deliberately left unapplied: Save must sweep it up.

            # Switch back to Tasks: remove returns, and add goes to Tasks.
            tasks_table.focus()
            await pilot.pause()
            assert screen.query_one("#remove-line", Button).display
            screen.query_one("#add-line", Button).press()
            await pilot.pause()
            assert tasks_table.row_count == 2
            assert members_table.row_count == 2
            screen.query_one("#line-tasks--title", Input).value = "Install"
            # Also unapplied -- one background pane, one active, both pending.

            screen.query_one("#save-form", Button).press()
            await _wait_until(
                pilot, lambda: not isinstance(app.screen, RecordEditScreen)
            )

            stored = app.records.get("plan.Project", 1, app.context)
            assert sorted(task["title"] for task in stored["tasks"]) == [
                "Install",
                "Survey",
            ]
            assert sorted(member["person"] for member in stored["members"]) == [
                "Mira",
                "Novak",
            ]

    asyncio.run(exercise())


def test_two_collection_form_lays_out_at_the_rail_sizes() -> None:
    """Both tables hold their ground at 140×40 and stay reachable at 80×24.

    Containment is only half a layout claim -- controls can overlap and still
    be inside the viewport -- so at the large size the two tables must occupy
    disjoint regions. At 80×24 the second collection may sit below the fold,
    which is what the scrollable form body is for; it must still focus and
    accept a line.
    """

    async def exercise_large() -> None:
        app = _projects_app()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.open_record(1)
            await _wait_until(
                pilot, lambda: isinstance(app.screen, RecordEditScreen)
            )
            screen = app.screen
            tasks_table = screen.query_one("#collection-records-tasks", DataTable)
            members_table = screen.query_one(
                "#collection-records-members", DataTable
            )
            assert tasks_table.region.height >= 5
            assert members_table.region.height >= 5
            assert not tasks_table.region.overlaps(members_table.region), (
                "the two collection tables share screen space"
            )

    async def exercise_small() -> None:
        app = _projects_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.open_record(1)
            await _wait_until(
                pilot, lambda: isinstance(app.screen, RecordEditScreen)
            )
            screen = app.screen
            members_table = screen.query_one(
                "#collection-records-members", DataTable
            )
            members_table.focus()
            await pilot.pause()
            screen.query_one("#add-line", Button).press()
            await pilot.pause()
            assert members_table.row_count == 2

    asyncio.run(exercise_large())
    asyncio.run(exercise_small())


def test_textual_invoice_report_preview_and_exports(tmp_path: Path) -> None:
    app = _demo_app(page_size=3, report_output_directory=tmp_path)

    async def exercise() -> None:
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            preview_button = app.query_one("#preview-report", Button)
            assert preview_button.display
            assert not preview_button.disabled

            await pilot.click("#preview-report")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ReportPreviewScreen)
            assert "INV-2026-0001" in screen.document.plain_text()
            assert "CONS - Consulting hour" in screen.document.plain_text()

            await pilot.click("#export-html")
            await pilot.click("#export-pdf")
            await pilot.click("#export-csv")
            await pilot.pause()
            assert (tmp_path / "invoice-INV-2026-0001.html").is_file()
            assert (tmp_path / "invoice-INV-2026-0001.pdf").read_bytes().startswith(
                b"%PDF-"
            )
            assert (tmp_path / "invoice-INV-2026-0001.csv").is_file()

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ReportPreviewScreen)

            summary_button = app.query_one("#summary-report", Button)
            assert summary_button.display
            assert not summary_button.disabled
            await pilot.click("#summary-report")
            await pilot.pause()
            # The summary declares optional parameters, so a prompt comes
            # first. Leaving every input blank sends `{}`: each unsupplied
            # optional parameter drops its criteria clause, so this is the
            # same report the button built before parameters existed.
            assert isinstance(app.screen, ParametersScreen)
            await pilot.click("#confirm-parameters")
            await pilot.pause()
            assert isinstance(app.screen, ReportPreviewScreen)
            preview_text = app.screen.document.plain_text()
            assert "Posted Sales Summary" in preview_text
            assert "Customer: ADRIA - Adria Consulting" in preview_text
            assert "4,610.00" in preview_text
            await pilot.click("#export-csv")
            await pilot.pause()
            assert list(tmp_path.glob("posted-sales-summary-*.csv"))

            await pilot.press("escape")
            await pilot.pause()

            # A supplied date narrows the report to the period it names.
            await pilot.click("#summary-report")
            await pilot.pause()
            assert isinstance(app.screen, ParametersScreen)
            from_date = app.screen.query_one("#parameter-from-date", Input)
            from_date.value = "2026-07-10"
            await pilot.click("#confirm-parameters")
            await pilot.pause()
            assert isinstance(app.screen, ReportPreviewScreen)
            narrowed = app.screen.document.plain_text()
            assert "INV-2026-0007" in narrowed
            assert "INV-2026-0001" not in narrowed
            await pilot.press("escape")
            await pilot.pause()

            # Cancelling the prompt builds nothing.
            await pilot.click("#summary-report")
            await pilot.pause()
            assert isinstance(app.screen, ParametersScreen)
            await pilot.click("#cancel-parameters")
            await pilot.pause()
            assert not isinstance(app.screen, ReportPreviewScreen)
            assert not isinstance(app.screen, ParametersScreen)

    asyncio.run(exercise())


def test_textual_browse_search_named_filters_and_sorting() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#records", DataTable)

            search = app.query_one("#search-query", Input)
            clear_button = app.query_one("#clear-query", Button)
            search.value = "0008"
            await _wait_until(
                pilot,
                lambda: table.row_count == 1
                and table.get_row_at(0)[0] == "INV-2026-0008",
            )
            assert table.row_count == 1
            assert table.get_row_at(0)[0] == "INV-2026-0008"

            clear_button.press()
            await _wait_until(
                pilot,
                lambda: table.row_count == 9,
            )
            assert table.row_count == 9

            app.query_one("#named-filter", Select).value = "drafts"
            await _wait_until(
                pilot,
                lambda: table.row_count == 5
                and all(
                    table.get_row_at(index)[3] == "Draft" for index in range(5)
                ),
            )
            assert table.row_count == 5
            assert all(table.get_row_at(index)[3] == "Draft" for index in range(5))
            assert "Draft invoices" in str(
                app.query_one("#browse-status", Static).content
            )

            app.query_one("#named-filter", Select).value = "high_value"
            await _wait_until(pilot, lambda: table.row_count == 0)
            assert table.row_count == 0

            clear_button.press()
            await _wait_until(pilot, lambda: table.row_count == 9)
            assert table.row_count == 9
            app.query_one("#sort-field", Select).value = "total"
            await _wait_until(
                pilot,
                lambda: table.row_count == 9
                and str(table.get_row_at(0)[-1]) == "0.00",
            )
            assert str(table.get_row_at(0)[-1]) == "0.00"

            app.query_one("#sort-direction", Button).press()
            await _wait_until(
                pilot,
                lambda: table.row_count == 9
                and str(table.get_row_at(0)[-1]) == "2,400.00",
            )
            assert str(table.get_row_at(0)[-1]) == "2,400.00"
            assert str(table.ordered_columns[-1].label).endswith("↓")

    asyncio.run(exercise())


def test_textual_browse_summary_bar_answers_for_the_whole_set() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#records", DataTable)
            # The first batch is three rows; the bar arrives with it and
            # answers for all nine, because a summary describes the filtered
            # set and not the fetched slice. (Prefetch keeps loading after
            # this, which changes the table and must not change the bar.)
            await _wait_until(
                pilot,
                lambda: table.row_count > 0
                and "Count 9"
                in str(app.query_one("#browse-summary", Static).content),
            )

            totals = [
                record["total"]
                for record in app.records.repository.all("sales.Invoice")
            ]
            expected_sum = f"{sum(totals):,.2f}"
            bar = app.query_one("#browse-summary", Static)
            assert bar.display
            # The whole line, exactly: "Count 9" is also a substring of a
            # count the column's money format wrongly dressed as 9.00.
            assert str(bar.content) == (
                f"Number Count 9  ·  Total Sum {expected_sum}"
            )

            # The bar follows the filter, not the scroll position.
            app.query_one("#named-filter", Select).value = "drafts"
            await _wait_until(
                pilot,
                lambda: "Count 5"
                in str(app.query_one("#browse-summary", Static).content),
            )

            # A view that declares no summaries shows no bar.
            app.query_one("#browse-view", Select).value = "crm.Customer.browse"
            await _wait_until(
                pilot,
                lambda: not app.query_one("#browse-summary", Static).display,
            )

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
    assert len(app.records.repository.all("sales.Invoice")) == 9


def test_tide_run_database_constructs_durable_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "tide.db"
    database_url = f"sqlite+pysqlite:///{database.as_posix()}"
    monkeypatch.setenv("TIDE_DATABASE_URL", database_url)
    launched: list[TideApp] = []
    monkeypatch.setattr(TideApp, "run", lambda self: launched.append(self))

    base_arguments = [
        "run",
        str(INVOICING),
        "--database-env",
        "--role",
        "sales_clerk",
    ]
    result = main([*base_arguments, "--create-schema"])
    restarted = main(base_arguments)

    assert result == 0
    assert restarted == 0
    assert len(launched) == 2
    app = launched[0]
    assert isinstance(app.records.repository, SQLAlchemyRepository)
    assert isinstance(app.records.cursor_store, SQLAlchemyCursorStore)
    assert isinstance(app.actions.execution_store, SQLAlchemyActionExecutionStore)
    assert app.context.principal.roles == frozenset({"sales_clerk"})
    assert app.source_label == "database via TIDE_DATABASE_URL (durable state)"

    engine = create_engine(database_url)
    try:
        # The application's own tables plus `tide_sequence`, which the
        # repository creates, plus whatever the framework stores declare. The
        # framework half is derived rather than listed: this used to be one
        # set of eleven names, and adding a store meant remembering to edit
        # it. What it still measures is that executing the DDL produces
        # exactly what the metadata declares, and nothing besides.
        assert set(inspect(engine).get_table_names()) == {
            "catalog_product",
            "crm_customer",
            "sales_invoice",
            "sales_invoice_line",
            "tide_sequence",
        } | {table.name for table in framework_stores(engine).tables}
    finally:
        engine.dispose()


def test_textual_workspace_switches_to_customer_management() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            workspace = app.query_one("#browse-view", Select)
            assert workspace.value == "sales.Invoice.browse"
            assert [view.name for view in app.browse_views] == [
                "sales.Invoice.browse",
                "crm.Customer.browse",
                "catalog.Product.browse",
            ]
            assert app._browse_view_labels == {
                "sales.Invoice.browse": "Invoices",
                "crm.Customer.browse": "Customers",
                "catalog.Product.browse": "Products",
            }

            workspace.value = "crm.Customer.browse"
            # Switching the workspace re-queries on a worker thread, so a single
            # pause only drains the message queue and can return before any row
            # arrives. Every other row assertion in this file waits.
            await _wait_until(
                pilot,
                lambda: app.view.name == "crm.Customer.browse"
                and app.query_one("#records", DataTable).row_count == 3,
            )
            assert app.entity.name == "crm.Customer"
            assert app.view.name == "crm.Customer.browse"
            customer_table = app.query_one("#records", DataTable)
            assert customer_table.row_count == 3
            assert customer_table.ordered_columns[1].width == 32

            browse = app.screen
            await pilot.click("#create-record")
            await pilot.pause()
            form = app.screen
            assert isinstance(form, RecordEditScreen)
            assert form.entity.name == "crm.Customer"
            form.query_one("#field-code", Input).value = "NOVA"
            form.query_one("#field-name", Input).value = "Nova Customer"
            form.query_one("#field-email", Input).value = "office@nova.example"
            await pilot.click("#save-form")
            await pilot.pause()

            assert app.screen is browse
            assert app.records.repository.get("crm.Customer", 4)["name"] == (
                "Nova Customer"
            )
            assert app.query_one("#records", DataTable).row_count == 4

    asyncio.run(exercise())


def test_textual_invoice_edit_saves_header_and_line_transactionally() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            table = app.query_one("#records", DataTable)
            table.focus()
            await pilot.press("down", "enter")
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            assert screen.session.identity == 2
            assert screen.query_one("#field-invoice_date", Input).value == "03.07.2026"
            line_table = screen.query_one("#collection-records-lines", DataTable)
            line_fields = screen.query_one("#line-fields-lines")
            line_actions = screen.query_one("#line-actions")
            record_actions = screen.query_one("#record-actions")
            assert line_table.row_count == 1
            assert line_table.region.height > 12
            assert line_table.region.y < line_fields.region.y < line_actions.region.y
            assert line_actions.region.y == record_actions.region.y
            assert line_actions.region.x < record_actions.region.x

            screen.query_one("#field-invoice_date", Input).value = "01.07.2026"
            screen.query_one("#field-currency", Input).value = "USD"
            screen.query_one("#line-lines--quantity", Input).value = "3"
            screen.action_apply_line()
            line_row = screen.query_one(
                "#collection-records-lines", DataTable
            ).get_row_at(0)
            assert str(line_row[-1]) == "720.00"
            assert all(
                isinstance(line_row[index], Text)
                and line_row[index].justify == "right"
                for index in (0, 3, 4, 5)
            )

            await pilot.click("#save-form")
            await pilot.pause()

            stored = app.records.repository.get("sales.Invoice", 2)
            assert stored["invoice_date"] == date(2026, 7, 1)
            assert stored["currency"] == "USD"
            assert stored["lines"][0]["quantity"] == Decimal("3")
            assert stored["lines"][0]["total"] == Decimal("720.00")
            assert stored["total"] == Decimal("720.00")
            assert stored["version"] == 2
            assert not isinstance(app.screen, RecordEditScreen)

    asyncio.run(exercise())


def test_textual_form_focuses_columns_and_enter_advances() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            assert screen.focused is not None
            assert screen.focused.id == "field-customer"

            await pilot.press("tab")
            assert screen.focused is not None
            assert screen.focused.id == "field-invoice_date"
            await pilot.press("tab")
            assert screen.focused is not None
            assert screen.focused.id == "field-currency"

            lines_pane = screen.collections["lines"]
            assert lines_pane.line_fields == (
                "line_number",
                "product",
                "description",
                "quantity",
                "unit_price",
                "total",
            )
            assert lines_pane.editor_columns == (
                ("line_number", "product", "description"),
                ("unit_price", "quantity"),
            )
            line_number = screen.query_one("#line-lines--line_number", Input)
            line_number.focus()
            for expected_id in (
                "line-lines--product",
                "line-lines--description",
                "line-lines--unit_price",
                "line-lines--quantity",
            ):
                await pilot.press("tab")
                assert screen.focused is not None
                assert screen.focused.id == expected_id

            invoice_date = screen.query_one("#field-invoice_date", Input)
            invoice_date.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert screen.focused is not None
            assert screen.focused.id == "field-currency"

            customer = screen.query_one("#field-customer", LookupField)
            customer.focus()
            await pilot.press("enter")
            assert screen.focused is not None
            assert screen.focused.id == "field-invoice_date"

            customer.focus()
            await pilot.press("space")
            await _wait_until(
                pilot,
                lambda: _lookup_ready(app),
            )
            assert isinstance(app.screen, LookupScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is screen
            assert screen.focused is not None
            assert screen.focused.id == "field-customer"

    asyncio.run(exercise())


def test_textual_form_renders_portable_tabs_and_action_bar_order(
    tmp_path: Path,
) -> None:
    project = shutil.copytree(INVOICING, tmp_path / "invoicing")
    view_file = project / "views" / "sales" / "invoice-edit.yaml"
    source = view_file.read_text(encoding="utf-8")
    # Prove each rewrite has something to rewrite: when the checked-in view
    # drifts, a silent no-op here re-tests the default order and calls it
    # portable.
    for anchor in (
        "  - group: Invoice\n",
        "  - collection: lines\n",
        "    actions: [add, apply, remove]",
        "actions: [cancel, save, post, void]",
    ):
        assert anchor in source, f"rewrite anchor missing: {anchor!r}"
    source = source.replace(
        "  - group: Invoice\n",
        "  - group: Invoice\n    tab: Details\n",
    ).replace(
        "  - collection: lines\n",
        "  - collection: lines\n    tab: Lines\n",
    ).replace(
        "    actions: [add, apply, remove]",
        "    actions: [remove, add, apply]",
    ).replace(
        "actions: [cancel, save, post, void]",
        "actions: [post, cancel, save, void]",
    )
    view_file.write_text(source, encoding="utf-8")
    app = _demo_app(page_size=3, project=project)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, RecordEditScreen)
                and len(app.screen.query("#form-tabs")) == 1
                and app.screen.query_one(
                    "#collection-records-lines", DataTable
                ).row_count
                == 1,
            )

            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            tabs = screen.query_one("#form-tabs", TabbedContent)
            assert tabs.active == "form-tab-0"
            assert [child.id for child in screen.query_one("#record-actions").children] == [
                "post-record",
                "cancel-form",
                "save-form",
                "record-action-void",
            ]
            assert [child.id for child in screen.query_one("#line-actions").children] == [
                "remove-line",
                "add-line",
                "apply-line",
            ]
            assert screen.query_one("#collection-records-lines", DataTable).row_count == 1
            tabs.active = "form-tab-1"
            await _wait_until(pilot, lambda: tabs.active == "form-tab-1")
            assert tabs.active == "form-tab-1"

    asyncio.run(exercise())


def test_textual_duplicate_opens_a_prefilled_draft_that_saves_as_new() -> None:
    """Duplicate is a head start, not a copy: the form opens as a new
    record carrying what a person could have typed on the original, and
    Save allocates a fresh number while the original stays whole."""

    app = _demo_app(page_size=10)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            table = app.query_one("#records", DataTable)
            await _wait_until(pilot, lambda: table.row_count > 0)
            table.move_cursor(row=0)  # INV-2026-0001, posted, one line
            await pilot.pause()

            duplicate = app.query_one("#duplicate-record", Button)
            assert duplicate.display
            assert not duplicate.disabled
            await pilot.click("#duplicate-record")
            await _wait_until(
                pilot, lambda: isinstance(app.screen, RecordEditScreen)
            )

            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            # A new record: no number yet, default state, copied values.
            assert screen.session.identity is None
            assert screen.session.values.get("number") in (None, "")
            assert screen.session.values["status"] == "draft"
            assert screen.session.values["currency"] == "EUR"
            assert len(screen.session.values["lines"]) == 1

            await pilot.click("#save-form")
            await _wait_until(
                pilot, lambda: not isinstance(app.screen, RecordEditScreen)
            )

            source = app.records.get("sales.Invoice", 1, app.context)
            rows = app.records.query(
                "sales.Invoice", QuerySpec(), app.context
            )
            copies = [
                row
                for row in rows
                if row["total"] == source["total"]
                and row["customer"] == source["customer"]
                and row["id"] != 1
                and row["status"] == "draft"
            ]
            assert copies, "the duplicate was not saved as a new record"
            assert copies[0]["number"] != source["number"]

    asyncio.run(exercise())


def test_textual_void_asks_for_its_reason_and_the_record_keeps_it() -> None:
    """A required parameter opens the dialog before the action runs.

    The dialog collects raw text; the action service does the typing, so
    the reason lands through the same door as every other surface.
    """

    app = _demo_app(page_size=5)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await _wait_until(
                pilot, lambda: isinstance(app.screen, RecordEditScreen)
            )

            await pilot.click("#record-action-void")
            await _wait_until(
                pilot, lambda: isinstance(app.screen, ParametersScreen)
            )
            assert "Void parameters" in str(
                app.screen.query_one("#parameters-title", Static).content
            )

            reason = app.screen.query_one("#parameter-reason", Input)
            reason.value = "Ordered twice by mistake"
            await pilot.click("#confirm-parameters")
            await _wait_until(
                pilot,
                lambda: not isinstance(
                    app.screen, (ParametersScreen, RecordEditScreen)
                ),
            )

            stored = app.records.get("sales.Invoice", 2, app.context)
            assert stored["status"] == "cancelled"
            assert stored["cancelled_reason"] == "Ordered twice by mistake"
            assert stored["cancelled_by"] == "demo:user"

    asyncio.run(exercise())


def test_textual_cancelling_the_parameters_dialog_leaves_the_record_alone() -> None:
    app = _demo_app(page_size=5)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await _wait_until(
                pilot, lambda: isinstance(app.screen, RecordEditScreen)
            )
            await pilot.click("#record-action-void")
            await _wait_until(
                pilot, lambda: isinstance(app.screen, ParametersScreen)
            )
            await pilot.click("#cancel-parameters")
            await _wait_until(
                pilot, lambda: isinstance(app.screen, RecordEditScreen)
            )
            stored = app.records.get("sales.Invoice", 2, app.context)
            assert stored["status"] == "draft"
            assert stored.get("cancelled_reason") is None

    asyncio.run(exercise())


def test_textual_view_hidden_fields_match_browse_and_form_rendering(
    tmp_path: Path,
) -> None:
    project = shutil.copytree(INVOICING, tmp_path / "invoicing")
    browse_file = project / "views" / "sales" / "invoice-browse.yaml"
    browse_file.write_text(
        browse_file.read_text(encoding="utf-8")
        + "\nfields:\n  total:\n    hidden: true\n",
        encoding="utf-8",
    )
    form_file = project / "views" / "sales" / "invoice-edit.yaml"
    form_file.write_text(
        form_file.read_text(encoding="utf-8")
        .replace(
            "  - group: Invoice\n",
            "  - group: Invoice\n    tab: General\n",
        )
        .replace(
            "  - collection: lines\n",
            "  - collection: lines\n    tab: Hidden lines\n",
        )
        .replace(
            "fields:\n",
            "fields:\n  number:\n    hidden: true\n  lines:\n    hidden: true\n",
        ),
        encoding="utf-8",
    )
    app = _demo_app(page_size=3, project=project)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            table = app.query_one("#records", DataTable)
            assert [column.key.value for column in table.ordered_columns] == [
                "number",
                "invoice_date",
                "customer",
                "status",
            ]

            app.open_record(2)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            assert not screen.query("#field-number")
            assert not screen.query("#collection-records-lines")
            assert not screen.query("#line-actions")
            assert len(screen.query("TabPane")) == 1

    asyncio.run(exercise())


def test_textual_product_lookup_search_and_selection_defaults() -> None:
    app = _demo_app(page_size=3)

    matches = app.records.lookup_records(
        "catalog.Product",
        ("code", "name"),
        "PRIORITY",
        app.context,
    )
    assert [record["code"] for record in matches] == ["SUP"]
    selected = app.records.apply_reference_selection(
        "sales.InvoiceLine",
        "product",
        {"description": "Old description", "unit_price": Decimal("1.00")},
        3,
        app.context,
    )
    assert selected["product"] == 3
    assert selected["description"] == "Annual license"
    assert selected["unit_price"] == Decimal("1200.00")

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, RecordEditScreen)
                and len(app.screen.query("#line-lines--product")) == 1,
            )
            form = app.screen
            assert isinstance(form, RecordEditScreen)

            product = form.query_one("#line-lines--product", LookupField)
            product.focus()
            await pilot.press("space")
            await _wait_until(
                pilot,
                lambda: _lookup_ready(app, column_count=3, row_count=3),
            )

            lookup = app.screen
            assert isinstance(lookup, LookupScreen)
            results = lookup.query_one("#lookup-results", DataTable)
            assert len(results.columns) == 3
            assert results.row_count == 3

            search = lookup.query_one("#lookup-search", Input)
            search.value = "annual"
            await _wait_until(pilot, lambda: results.row_count == 1)
            assert results.row_count == 1
            result_row = results.get_row_at(0)
            assert [str(value) for value in result_row] == [
                "LIC",
                "Annual license",
                "1,200.00",
            ]
            assert isinstance(result_row[-1], Text)
            assert result_row[-1].justify == "right"

            search.focus()
            await pilot.press("enter")
            await _wait_until(
                pilot,
                lambda: app.screen is form and product.value == 3,
            )
            assert app.screen is form
            assert product.value == 3
            assert form.query_one("#line-lines--description", Input).value == "Annual license"
            assert form.query_one("#line-lines--unit_price", Input).value == "1200.00"

            form.action_apply_line()
            assert str(
                form.query_one("#collection-records-lines", DataTable).get_row_at(0)[-1]
            ) == "2,400.00"

    asyncio.run(exercise())


def test_textual_lookup_creates_product_and_preserves_invoice_draft() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, RecordEditScreen)
                and len(app.screen.query("#line-lines--product")) == 1,
            )
            invoice = app.screen
            assert isinstance(invoice, RecordEditScreen)
            invoice.query_one("#field-currency", Input).value = "GBP"

            product = invoice.query_one("#line-lines--product", LookupField)
            product.focus()
            await pilot.press("space")
            await _wait_until(
                pilot,
                lambda: _lookup_ready(app)
                and not app.screen.query_one(
                    "#create-lookup-record", Button
                ).disabled,
            )
            lookup = app.screen
            assert isinstance(lookup, LookupScreen)
            assert not lookup.query_one("#create-lookup-record", Button).disabled

            await pilot.click("#create-lookup-record")
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, RecordEditScreen)
                and app.screen.entity.name == "catalog.Product"
                and len(app.screen.query("#save-form")) == 1,
            )
            product_form = app.screen
            assert isinstance(product_form, RecordEditScreen)
            assert product_form.entity.name == "catalog.Product"
            assert str(product_form.query_one("#save-form", Button).label) == (
                "Save & Select"
            )
            product_form.query_one("#field-code", Input).value = "TRAIN"
            product_form.query_one("#field-name", Input).value = "Training day"
            price = product_form.query_one(
                "#field-unit_price", NumericMaskedInput
            )
            price.value = "350.00"
            price.focus()
            await pilot.press("end", "1")
            assert price.value == "350.00"
            price.value = "350."
            await pilot.press("tab")
            assert price.value == "350.00"
            await pilot.click("#save-form")
            await _wait_until(
                pilot,
                lambda: app.screen is invoice and product.value == 4,
            )

            assert app.screen is invoice
            assert invoice.query_one("#field-currency", Input).value == "GBP"
            assert product.value == 4
            assert invoice.query_one("#line-lines--description", Input).value == (
                "Training day"
            )
            assert invoice.query_one("#line-lines--unit_price", Input).value == "350.00"
            assert app.records.repository.get("catalog.Product", 4)["code"] == (
                "TRAIN"
            )

            await pilot.press("escape")
            await pilot.pause()
            assert app.records.repository.get("sales.Invoice", 2)["currency"] == (
                "EUR"
            )
            assert app.records.repository.get("catalog.Product", 4)["name"] == (
                "Training day"
            )

    asyncio.run(exercise())


def test_textual_invoice_post_uses_registered_action_and_audit() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, RecordEditScreen)
                and len(app.screen.query("#save-form")) == 1,
            )
            assert isinstance(app.screen, RecordEditScreen)

            await pilot.press("ctrl+p")
            await pilot.pause()

            stored = app.records.repository.get("sales.Invoice", 2)
            assert stored["status"] == "posted"
            assert stored["posted_by"] == "demo:user"
            assert stored["version"] == 2
            events = app.actions.execution_store.audit_events()
            assert len(events) == 1
            assert events[0].action == "post"
            assert events[0].outcome is AuditOutcome.SUCCEEDED
            assert not isinstance(app.screen, RecordEditScreen)

    asyncio.run(exercise())


def test_textual_auditor_opens_safe_record_action_history() -> None:
    app = _demo_app(page_size=3, role="auditor")
    app.actions.execute(
        "sales.Invoice",
        "post",
        2,
        {},
        RequestContext(
            Principal("audit:clerk", roles=frozenset({"sales_clerk"})),
            channel=Channel.TUI,
            correlation_id="tui-history-post",
        ),
        idempotency_key="tui-history-post-2",
    )

    async def exercise() -> None:
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            table = app.query_one("#records", DataTable)
            history_button = app.query_one("#audit-history", Button)
            assert history_button.display
            assert not history_button.disabled

            table.move_cursor(row=1)
            history_button.press()
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, AuditHistoryScreen),
            )
            screen = app.screen
            assert isinstance(screen, AuditHistoryScreen)
            events = screen.query_one("#audit-events", DataTable)
            await _wait_until(pilot, lambda: events.row_count == 2)
            assert events.row_count == 2
            assert [str(value) for value in events.get_row_at(0)[1:]] == [
                "Action",
                "post",
                "Succeeded",
                "—",
                "audit:clerk",
                "tui",
                "tui-history-post",
            ]
            record_row = [str(value) for value in events.get_row_at(1)[1:]]
            assert record_row[:3] == ["Record", "Update", "Succeeded"]
            assert "status: draft → posted" in record_row[3]
            assert "posted_by: [redacted]" in record_row[3]
            assert "Protected values stay redacted" in str(
                screen.query_one("#audit-status", Static).content
            )

            await pilot.press("escape")
            await _wait_until(
                pilot,
                lambda: not isinstance(app.screen, AuditHistoryScreen),
            )

    asyncio.run(exercise())


def test_textual_invoice_create_uses_generator_and_inline_line_editor() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#create-record")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            assert screen.session.is_new

            date_editor = screen.query_one("#field-invoice_date", Input)
            assert date_editor.value == date.today().strftime("%d.%m.%Y")
            date_editor.focus()
            await pilot.press("plus")
            assert date_editor.value == (date.today() + timedelta(days=1)).strftime(
                "%d.%m.%Y"
            )
            await pilot.press("minus")
            assert date_editor.value == date.today().strftime("%d.%m.%Y")

            date_editor.value = "20.07.2026"
            screen.query_one("#field-customer", LookupField).set_selection(
                1,
                "ADRIA - Adria Consulting",
            )
            screen.action_add_line()
            screen.query_one("#line-lines--product", LookupField).set_selection(
                1,
                "CONS - Consulting hour",
            )
            screen.query_one("#line-lines--description", Input).value = "Created in Textual"
            screen.query_one("#line-lines--quantity", Input).value = "2.5"
            screen.query_one("#line-lines--unit_price", Input).value = "85.00"
            screen.action_apply_line()
            screen.action_save()
            await pilot.pause()

            stored = app.records.repository.get("sales.Invoice", 10)
            assert stored["number"] == "INV-2026-0010"
            assert stored["invoice_date"] == date(2026, 7, 20)
            assert stored["status"] == "draft"
            assert stored["customer"] == 1
            assert stored["total"] == Decimal("212.50")
            assert stored["lines"][0]["description"] == "Created in Textual"
            assert stored["version"] == 1

    asyncio.run(exercise())


def test_textual_posted_invoice_is_readonly() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(1)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)
            assert not screen.query("#field-invoice_date")
            assert screen.query_one("#value-invoice_date", Static).has_class(
                "readonly-value"
            )
            assert len(screen.query(".readonly-label")) >= 1
            assert screen.query_one("#save-form", Button).disabled
            assert screen.query_one("#post-record", Button).disabled
            assert screen.query_one("#add-line", Button).disabled

    asyncio.run(exercise())


def test_textual_stale_edit_reports_concurrency_conflict() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, RecordEditScreen),
            )
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)

            concurrent = app.records.begin_edit("sales.Invoice", 2, app.context)
            concurrent.set("currency", "USD")
            app.records.commit(concurrent, app.context)

            screen.query_one("#field-currency", Input).value = "GBP"
            screen.action_save()
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, ConflictReviewScreen),
            )

            review = app.screen
            assert isinstance(review, ConflictReviewScreen)
            assert review.conflict.conflicting_fields == ("currency",)
            assert not review.conflict.rebase_fields
            row = review.query_one("#conflict-fields", DataTable).get_row_at(0)
            assert [str(value) for value in row] == [
                "Currency",
                "EUR",
                "USD",
                "GBP",
                "Choose value",
            ]
            assert review.query_one("#apply-conflict-resolution", Button).disabled
            assert not review.query_one("#use-current-conflict", Button).disabled
            assert not review.query_one("#use-draft-conflict", Button).disabled

            await pilot.click("#keep-conflict-draft")
            await _wait_until(pilot, lambda: app.screen is screen)
            message = str(screen.query_one("#form-message", Static).content)
            assert "draft remains open and unsaved" in message

            screen.action_save()
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, ConflictReviewScreen),
            )
            await pilot.click("#reload-conflict-record")
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, RecordEditScreen)
                and app.screen is not screen,
            )

            reloaded = app.screen
            assert isinstance(reloaded, RecordEditScreen)
            assert reloaded is not screen
            assert reloaded.session.expected_version == 2
            assert reloaded.query_one("#field-currency", Input).value == "USD"
            assert app.records.repository.get("sales.Invoice", 2)["currency"] == "USD"

    asyncio.run(exercise())


def test_textual_stale_edit_applies_explicit_current_and_draft_choices() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)

            concurrent = app.records.begin_edit("sales.Invoice", 2, app.context)
            concurrent.set("invoice_date", date(2026, 7, 5))
            concurrent.set("currency", "USD")
            app.records.commit(concurrent, app.context)

            screen.query_one("#field-invoice_date", Input).value = "04.07.2026"
            screen.query_one("#field-currency", Input).value = "GBP"
            screen.action_save()
            await pilot.pause()
            review = app.screen
            assert isinstance(review, ConflictReviewScreen)
            assert review.conflict.conflicting_fields == (
                "invoice_date",
                "currency",
            )
            assert review.has_class("compact-terminal")
            apply_button = review.query_one("#apply-conflict-resolution", Button)
            assert apply_button.disabled
            assert apply_button.region.y < 24

            table = review.query_one("#conflict-fields", DataTable)
            table.move_cursor(row=0)
            await pilot.click("#use-current-conflict")
            assert str(table.get_row_at(0)[-1]) == "Use Current"
            assert apply_button.disabled

            table.move_cursor(row=1)
            await pilot.pause()
            await pilot.click("#use-draft-conflict")
            assert str(table.get_row_at(1)[-1]) == "Use Draft"
            assert not apply_button.disabled

            await pilot.click("#apply-conflict-resolution")
            await pilot.pause()
            resolved = app.screen
            assert isinstance(resolved, RecordEditScreen)
            assert resolved.session.expected_version == 2
            assert (
                resolved.query_one("#field-invoice_date", Input).value
                == "05.07.2026"
            )
            assert resolved.query_one("#field-currency", Input).value == "GBP"

            resolved.action_save()
            await pilot.pause()
            stored = app.records.repository.get("sales.Invoice", 2)
            assert stored["invoice_date"] == date(2026, 7, 5)
            assert stored["currency"] == "GBP"
            assert stored["version"] == 3

    asyncio.run(exercise())


def test_textual_stale_edit_rebases_only_non_conflicting_draft_fields() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)

            concurrent = app.records.begin_edit("sales.Invoice", 2, app.context)
            concurrent.set("currency", "USD")
            app.records.commit(concurrent, app.context)

            screen.query_one("#field-invoice_date", Input).value = "04.07.2026"
            screen.action_save()
            await pilot.pause()

            review = app.screen
            assert isinstance(review, ConflictReviewScreen)
            assert not review.conflict.conflicting_fields
            assert review.conflict.rebase_fields == ("invoice_date",)
            assert review.has_class("compact-terminal")
            assert not review.query_one(
                "#apply-conflict-resolution", Button
            ).disabled

            await pilot.click("#apply-conflict-resolution")
            await pilot.pause()
            rebased = app.screen
            assert isinstance(rebased, RecordEditScreen)
            assert rebased.session.expected_version == 2
            assert rebased.query_one("#field-currency", Input).value == "USD"
            assert (
                rebased.query_one("#field-invoice_date", Input).value
                == "04.07.2026"
            )

            rebased.action_save()
            await pilot.pause()
            stored = app.records.repository.get("sales.Invoice", 2)
            assert stored["currency"] == "USD"
            assert stored["invoice_date"] == date(2026, 7, 4)
            assert stored["version"] == 3

    asyncio.run(exercise())


def test_textual_stale_edit_does_not_rebase_fields_locked_by_new_workflow_state() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, RecordEditScreen),
            )
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)

            posted = app.actions.execute(
                "sales.Invoice",
                "post",
                2,
                {},
                app.context,
                idempotency_key="concurrent-post-before-rebase",
                expected_version=1,
            )
            assert posted["status"] == "posted"

            screen.query_one("#field-currency", Input).value = "GBP"
            screen.action_save()
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, ConflictReviewScreen),
            )
            review = app.screen
            assert isinstance(review, ConflictReviewScreen)
            assert review.conflict.rebase_fields == ("currency",)

            await pilot.click("#apply-conflict-resolution")
            await _wait_until(
                pilot,
                lambda: isinstance(app.screen, RecordEditScreen),
            )
            reloaded = app.screen
            assert isinstance(reloaded, RecordEditScreen)
            assert not reloaded.query("#field-currency")
            assert str(
                reloaded.query_one("#value-currency", Static).content
            ) == "EUR"
            assert reloaded.query_one("#save-form", Button).disabled
            stored = app.records.repository.get("sales.Invoice", 2)
            assert stored["currency"] == "EUR"
            assert stored["version"] == 2

    asyncio.run(exercise())


def test_textual_validation_feedback_and_cancel_preserve_record() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)

            screen.query_one("#field-invoice_date", Input).value = "not-a-date"
            screen.action_save()
            await pilot.pause()
            message = str(screen.query_one("#form-message", Static).content)
            assert "invoice_date must be a date" in message

            await pilot.press("escape")
            await pilot.pause()
            stored = app.records.repository.get("sales.Invoice", 2)
            assert stored["invoice_date"].isoformat() == "2026-07-03"
            assert stored["version"] == 1
            assert not isinstance(app.screen, RecordEditScreen)

    asyncio.run(exercise())


def test_textual_browse_names_its_references_without_a_read_each(
    monkeypatch,
) -> None:
    """The grid shows "ADRIA - Adria Consulting"; it should not buy it.

    Eight invoices over three customers used to cost three record reads on
    top of the page, renewed whenever the cache was cleared. The page now
    arrives already knowing, and a read here means it did not.
    """

    app = _demo_app(page_size=25)
    reads: list[tuple[str, Any]] = []
    loaded = app.records.get

    def tracked_get(entity_name, identity, context):
        reads.append((entity_name, identity))
        return loaded(entity_name, identity, context)

    monkeypatch.setattr(app.records, "get", tracked_get)

    async def exercise() -> None:
        async with app.run_test(size=(120, 16)) as pilot:
            table = app.query_one("#records", DataTable)
            await _wait_until(
                pilot,
                lambda: table.row_count >= 8 and not app._query_loading,
            )
            assert [str(value) for value in table.get_row_at(0)][2] == (
                "ADRIA - Adria Consulting"
            )
            assert [entity for entity, _ in reads if entity == "crm.Customer"] == []

    asyncio.run(exercise())


def _demo_app(
    *,
    page_size: int,
    role: str = "sales_clerk",
    report_output_directory: Path | None = None,
    project: Path = INVOICING,
) -> TideApp:
    model = compile_project(project)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    context = RequestContext(
        principal=Principal("demo:user", roles=frozenset({role})),
        channel=Channel.TUI,
    )
    return TideApp(
        model,
        records,
        context,
        actions=actions,
        page_size=page_size,
        source_label="demo data",
        report_output_directory=report_output_directory,
    )
