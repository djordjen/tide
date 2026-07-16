from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from rich.text import Text
from sqlalchemy import create_engine, inspect
from textual.widgets import Button, DataTable, Input, Select, Static

from tide import compile_project
from tide.cli import main
from tide.data import (
    InMemoryRepository,
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemyRepository,
)
from tide.runtime import Channel, Principal, RequestContext
from tide.services import ActionService, AuditOutcome, RecordsService
from tide.tui import (
    TideApp,
    configure_application_runtime,
    seed_demo_data,
)
from tide.tui.form import RecordEditScreen
from tide.tui.lookup import LookupField, LookupScreen

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def test_textual_invoice_browse_pages_by_keyboard_and_mouse() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#records", DataTable)
            assert table.row_count == 3
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
            assert app.query_one("#create-record", Button).disabled
            assert app.query_one("#edit-record", Button).disabled

    asyncio.run(exercise())


def test_textual_browse_search_named_filters_and_sorting() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#records", DataTable)

            search = app.query_one("#search-query", Input)
            search.value = "0008"
            await pilot.pause()
            assert table.row_count == 1
            assert table.get_row_at(0)[0] == "INV-2026-0008"
            assert app.query_one("#next-page", Button).disabled

            await pilot.click("#clear-query")
            await pilot.pause()
            assert table.row_count == 3

            app.query_one("#named-filter", Select).value = "drafts"
            await pilot.pause()
            assert table.row_count == 3
            assert all(table.get_row_at(index)[3] == "Draft" for index in range(3))
            assert "Draft invoices" in str(
                app.query_one("#browse-status", Static).content
            )

            app.query_one("#named-filter", Select).value = "high_value"
            await pilot.pause()
            assert table.row_count == 0

            await pilot.click("#clear-query")
            app.query_one("#sort-field", Select).value = "total"
            await pilot.pause()
            assert str(table.get_row_at(0)[-1]) == "240.00"

            await pilot.click("#sort-direction")
            await pilot.pause()
            assert str(table.get_row_at(0)[-1]) == "2,400.00"
            assert str(table.ordered_columns[-1].label).endswith("↓")

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
        assert set(inspect(engine).get_table_names()) == {
            "catalog_product",
            "crm_customer",
            "sales_invoice",
            "sales_invoice_line",
            "tide_action_audit",
            "tide_action_idempotency",
            "tide_query_cursor",
        }
    finally:
        engine.dispose()


def test_textual_workspace_switches_to_customer_management() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            workspace = app.query_one("#browse-view", Select)
            assert workspace.value == "sales.Invoice.browse"

            workspace.value = "crm.Customer.browse"
            await pilot.pause()
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
            assert app.query_one("#records", DataTable).row_count == 3

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
            line_table = screen.query_one("#collection-records", DataTable)
            line_fields = screen.query_one("#line-fields")
            line_actions = screen.query_one("#line-actions")
            record_actions = screen.query_one("#record-actions")
            assert line_table.row_count == 1
            assert line_table.region.height > 12
            assert line_table.region.y < line_fields.region.y < line_actions.region.y
            assert line_actions.region.y == record_actions.region.y
            assert line_actions.region.x < record_actions.region.x

            screen.query_one("#field-invoice_date", Input).value = "01.07.2026"
            screen.query_one("#field-currency", Input).value = "USD"
            screen.query_one("#line-quantity", Input).value = "3"
            screen.action_apply_line()
            line_row = screen.query_one(
                "#collection-records", DataTable
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

            assert screen.line_fields == (
                "line_number",
                "product",
                "description",
                "quantity",
                "unit_price",
                "total",
            )
            assert screen.line_editor_columns == (
                ("line_number", "product", "description"),
                ("unit_price", "quantity"),
            )
            line_number = screen.query_one("#line-line_number", Input)
            line_number.focus()
            for expected_id in (
                "line-product",
                "line-description",
                "line-unit_price",
                "line-quantity",
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
            await pilot.pause()
            assert isinstance(app.screen, LookupScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is screen
            assert screen.focused is not None
            assert screen.focused.id == "field-customer"

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
            await pilot.pause()
            form = app.screen
            assert isinstance(form, RecordEditScreen)

            product = form.query_one("#line-product", LookupField)
            product.focus()
            await pilot.press("space")
            await pilot.pause()

            lookup = app.screen
            assert isinstance(lookup, LookupScreen)
            results = lookup.query_one("#lookup-results", DataTable)
            assert len(results.columns) == 3
            assert results.row_count == 3

            search = lookup.query_one("#lookup-search", Input)
            search.value = "annual"
            await pilot.pause()
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
            await pilot.pause()
            assert app.screen is form
            assert product.value == 3
            assert form.query_one("#line-description", Input).value == "Annual license"
            assert form.query_one("#line-unit_price", Input).value == "1200.00"

            form.action_apply_line()
            assert str(
                form.query_one("#collection-records", DataTable).get_row_at(0)[-1]
            ) == "2,400.00"

    asyncio.run(exercise())


def test_textual_lookup_creates_product_and_preserves_invoice_draft() -> None:
    app = _demo_app(page_size=3)

    async def exercise() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_record(2)
            await pilot.pause()
            invoice = app.screen
            assert isinstance(invoice, RecordEditScreen)
            invoice.query_one("#field-currency", Input).value = "GBP"

            product = invoice.query_one("#line-product", LookupField)
            product.focus()
            await pilot.press("space")
            await pilot.pause()
            lookup = app.screen
            assert isinstance(lookup, LookupScreen)
            assert not lookup.query_one("#create-lookup-record", Button).disabled

            await pilot.click("#create-lookup-record")
            await pilot.pause()
            product_form = app.screen
            assert isinstance(product_form, RecordEditScreen)
            assert product_form.entity.name == "catalog.Product"
            assert str(product_form.query_one("#save-form", Button).label) == (
                "Save & Select"
            )
            product_form.query_one("#field-code", Input).value = "TRAIN"
            product_form.query_one("#field-name", Input).value = "Training day"
            product_form.query_one("#field-unit_price", Input).value = "350.00"
            await pilot.click("#save-form")
            await pilot.pause()

            assert app.screen is invoice
            assert invoice.query_one("#field-currency", Input).value == "GBP"
            assert product.value == 4
            assert invoice.query_one("#line-description", Input).value == (
                "Training day"
            )
            assert invoice.query_one("#line-unit_price", Input).value == "350.00"
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
            await pilot.pause()
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
            screen.query_one("#line-product", LookupField).set_selection(
                1,
                "CONS - Consulting hour",
            )
            screen.query_one("#line-description", Input).value = "Created in Textual"
            screen.query_one("#line-quantity", Input).value = "2.5"
            screen.query_one("#line-unit_price", Input).value = "85.00"
            screen.action_apply_line()
            screen.action_save()
            await pilot.pause()

            stored = app.records.repository.get("sales.Invoice", 9)
            assert stored["number"] == "INV-2026-000009"
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
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RecordEditScreen)

            concurrent = app.records.begin_edit("sales.Invoice", 2, app.context)
            concurrent.set("currency", "USD")
            app.records.commit(concurrent, app.context)

            screen.query_one("#field-currency", Input).value = "GBP"
            screen.action_save()
            await pilot.pause()

            message = str(screen.query_one("#form-message", Static).content)
            assert "expected 1, current 2" in message
            assert app.records.repository.get("sales.Invoice", 2)["currency"] == "USD"

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


def _demo_app(*, page_size: int, role: str = "sales_clerk") -> TideApp:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
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
    )
