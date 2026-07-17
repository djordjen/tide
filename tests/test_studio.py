from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Input, Select, Static, TextArea, Tree

from tide.cli import main
from tide.development import DesignerDocumentReference, StudioError, StudioService
from tide.tui import StudioApp


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def test_studio_service_builds_semantic_tree_without_writing_sources() -> None:
    before = _source_state(INVOICING)

    service = StudioService(INVOICING)
    workspace = service.workspace
    invoice = service.document(
        DesignerDocumentReference(kind="entity", name="sales.Invoice")
    )

    assert workspace.application == "TIDE Invoicing"
    assert workspace.valid
    assert (workspace.entity_count, workspace.view_count, workspace.report_count) == (
        4,
        9,
        1,
    )
    assert [group.label for group in workspace.groups] == [
        "Application",
        "Entities",
        "Views",
        "Reports",
        "Source files",
    ]
    entities = next(group for group in workspace.groups if group.kind == "entity")
    assert [document.label for document in entities.documents] == [
        "catalog.Product",
        "crm.Customer",
        "sales.Invoice",
        "sales.InvoiceLine",
    ]
    assert invoice.file == "models/sales/invoice.yaml"
    assert invoice.properties[0].name == "entity"
    assert invoice.properties[0].value == "sales.Invoice"
    assert invoice.properties[0].editable is False
    assert next(item for item in invoice.properties if item.name == "fields").value == (
        "11 properties"
    )
    label = next(item for item in invoice.properties if item.path == ("label",))
    assert label.editable is True
    assert (
        next(
            item
            for item in invoice.properties
            if item.path == ("fields", "number", "length")
        ).value
        == "30"
    )
    field_type = next(
        item for item in invoice.properties if item.path == ("fields", "id", "type")
    )
    assert field_type.editor == "choice"
    assert field_type.choices == (
        "string",
        "integer",
        "decimal",
        "boolean",
        "date",
        "datetime",
        "choice",
        "reference",
        "collection",
    )
    primary_key = next(
        item
        for item in invoice.properties
        if item.path == ("fields", "id", "primary_key")
    )
    assert primary_key.editor == "boolean"
    assert primary_key.choices == ("true", "false")
    assert "actions:" in invoice.source
    assert _source_state(INVOICING) == before


def test_studio_service_edits_typed_properties_with_diff_undo_and_validation() -> None:
    before = _source_state(INVOICING)
    service = StudioService(INVOICING)
    invoice = DesignerDocumentReference(kind="entity", name="sales.Invoice")

    changed = service.set_property(invoice, ("label",), "Sales invoices")

    assert changed.valid
    assert changed.dirty
    assert changed.can_undo
    assert changed.changed_files == ("models/sales/invoice.yaml",)
    assert "-label: Invoices" in changed.diff
    assert "+label: Sales invoices" in changed.diff
    assert "label: Sales invoices" in service.document(invoice).source

    restored = service.undo()
    assert restored.valid
    assert not restored.dirty
    assert restored.can_redo
    assert "label: Invoices" in service.document(invoice).source

    repeated = service.redo()
    assert repeated.dirty
    assert "label: Sales invoices" in service.document(invoice).source

    invalid = service.set_property(invoice, ("display",), "missing")
    assert not invalid.valid
    assert invalid.can_undo
    assert invalid.diagnostics[0]["code"] == "TIDE215"
    assert invalid.workspace.application == "TIDE Invoicing"
    assert invalid.workspace.entity_count == 4

    with pytest.raises(StudioError, match="not directly editable"):
        service.set_property(invoice, ("entity",), "sales.RenamedInvoice")

    assert _source_state(INVOICING) == before


def test_studio_service_preserves_existing_scalar_types() -> None:
    service = StudioService(INVOICING)
    invoice = DesignerDocumentReference(kind="entity", name="sales.Invoice")

    service.set_property(invoice, ("fields", "number", "length"), "40")
    source = service.document(invoice).source

    assert "    length: 40" in source
    assert '    length: "40"' not in source
    with pytest.raises(StudioError, match="requires an integer"):
        service.set_property(invoice, ("fields", "number", "length"), "forty")
    with pytest.raises(StudioError, match="requires one of"):
        service.set_property(invoice, ("fields", "id", "type"), "currency")


def test_studio_service_applies_expert_yaml_in_memory_without_identity_bypass() -> None:
    before = _source_state(INVOICING)
    service = StudioService(INVOICING)
    invoice = DesignerDocumentReference(kind="entity", name="sales.Invoice")
    original = service.document(invoice).source
    replacement = original.replace("label: Invoices", "label: Expert invoices")

    changed = service.replace_document_source(invoice, replacement)

    assert changed.valid
    assert changed.dirty
    assert changed.can_undo
    assert "+label: Expert invoices" in changed.diff
    assert service.document(invoice).source == replacement
    assert service.undo().dirty is False

    with pytest.raises(StudioError, match="TIDEDES003"):
        service.replace_document_source(invoice, "entity: [\n")
    with pytest.raises(StudioError, match="TIDEDES012"):
        service.replace_document_source(
            invoice,
            original.replace("sales.Invoice", "sales.RenamedInvoice", 1),
        )

    assert _source_state(INVOICING) == before


def test_textual_studio_browses_properties_and_yaml_without_writes() -> None:
    before = _source_state(INVOICING)
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            tree = app.query_one("#studio-tree", Tree)
            assert tree.root.label.plain == "TIDE Invoicing"
            assert [node.label.plain for node in tree.root.children] == [
                "Application (1)",
                "Entities (4)",
                "Views (9)",
                "Reports (1)",
                "Source files (19)",
            ]
            entity_group = tree.root.children[1]
            invoice_node = next(
                node
                for node in entity_group.children
                if node.label.plain == "sales.Invoice"
            )
            tree.select_node(invoice_node)
            await pilot.pause()

            assert app.selected_target == DesignerDocumentReference(
                kind="entity",
                name="sales.Invoice",
            )
            rows = app.query_one("#property-table", DataTable)
            entity_key = _property_key(app, ("entity",))
            assert rows.get_row(entity_key) == [
                "entity",
                "sales.Invoice",
                "Locked",
            ]
            source = app.query_one("#source-preview", TextArea)
            assert "entity: sales.Invoice" in source.text
            assert "models/sales/invoice.yaml" in str(
                app.query_one("#source-title", Static).content
            )
            assert "no database connection" in str(
                app.query_one("#studio-status", Static).content
            )

    asyncio.run(exercise())
    assert _source_state(INVOICING) == before


def test_textual_studio_applies_reviews_and_undoes_in_memory_edit() -> None:
    before = _source_state(INVOICING)
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            _select_invoice(app)
            await pilot.pause()

            table = app.query_one("#property-table", DataTable)
            label_key = _property_key(app, ("label",))
            label_row = list(app._property_rows).index(label_key)
            table.move_cursor(row=label_row)
            await pilot.pause()

            editor = app.query_one("#property-value", Input)
            assert not editor.disabled
            assert editor.value == "Invoices"
            editor.value = "Sales invoices"
            await pilot.click("#apply-property")
            await pilot.pause()

            assert app.state.valid
            assert app.state.dirty
            assert not app.query_one("#undo-edit", Button).disabled
            assert "-label: Invoices" in app.query_one("#source-preview", TextArea).text
            assert (
                "+label: Sales invoices"
                in app.query_one("#source-preview", TextArea).text
            )
            assert "Unsaved in-memory changes" in str(
                app.query_one("#studio-status", Static).content
            )

            await pilot.click("#show-source")
            await pilot.pause()
            assert (
                "label: Sales invoices"
                in app.query_one("#source-preview", TextArea).text
            )

            await pilot.click("#undo-edit")
            await pilot.pause()
            assert not app.state.dirty
            await pilot.click("#show-source")
            await pilot.pause()
            assert "label: Invoices" in app.query_one("#source-preview", TextArea).text
            assert not app.query_one("#redo-edit", Button).disabled

    asyncio.run(exercise())
    assert _source_state(INVOICING) == before


def test_textual_studio_keeps_invalid_edit_visible_with_diagnostics() -> None:
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            _select_invoice(app)
            await pilot.pause()

            table = app.query_one("#property-table", DataTable)
            display_key = _property_key(app, ("display",))
            table.move_cursor(row=list(app._property_rows).index(display_key))
            await pilot.pause()
            app.query_one("#property-value", Input).value = "missing"
            await pilot.click("#apply-property")
            await pilot.pause()

            assert not app.state.valid
            assert app.state.can_undo
            assert "Invalid in-memory candidate" in str(
                app.query_one("#studio-status", Static).content
            )
            await pilot.click("#show-diagnostics")
            await pilot.pause()
            diagnostics = app.query_one("#source-preview", TextArea).text
            assert "TIDE215" in diagnostics
            assert "has no field 'missing'" in diagnostics

    asyncio.run(exercise())


def test_textual_studio_uses_schema_choice_and_boolean_controls() -> None:
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            _select_invoice(app)
            await pilot.pause()

            table = app.query_one("#property-table", DataTable)
            type_key = _property_key(app, ("fields", "id", "type"))
            table.move_cursor(row=list(app._property_rows).index(type_key))
            await pilot.pause()

            selector = app.query_one("#property-choice", Select)
            assert selector.display
            assert selector.value == "integer"
            assert app.selected_property is not None
            assert app.selected_property.choices[-3:] == (
                "choice",
                "reference",
                "collection",
            )
            assert not app.query_one("#property-value", Input).display

            required_key = _property_key(
                app,
                ("fields", "invoice_date", "required"),
            )
            table.move_cursor(row=list(app._property_rows).index(required_key))
            await pilot.pause()
            assert selector.value == "true"
            selector.value = "false"
            await pilot.click("#apply-property")
            await pilot.pause()

            assert app.state.dirty
            await pilot.click("#show-source")
            await pilot.pause()
            assert (
                "    required: false" in app.query_one("#source-preview", TextArea).text
            )

    asyncio.run(exercise())


def test_textual_studio_colors_yaml_and_searches_current_preview() -> None:
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            _select_invoice(app)
            await pilot.pause()

            preview = app.query_one("#source-preview", TextArea)
            assert preview.language == "yaml"

            await pilot.press("ctrl+f")
            await pilot.pause()
            search = app.query_one("#source-search", Horizontal)
            assert search.display
            query = app.query_one("#source-search-query", Input)
            query.value = "readonly"
            await pilot.pause()

            assert len(app._search_matches) == 6
            assert preview.selected_text.casefold() == "readonly"
            assert "1 / 6" in str(
                app.query_one("#source-search-status", Static).content
            )

            await pilot.press("enter")
            await pilot.pause()
            assert "2 / 6" in str(
                app.query_one("#source-search-status", Static).content
            )
            await pilot.click("#search-previous")
            await pilot.pause()
            assert "1 / 6" in str(
                app.query_one("#source-search-status", Static).content
            )

            await pilot.click("#search-close")
            await pilot.pause()
            assert not search.display

    asyncio.run(exercise())


def test_textual_studio_expert_yaml_apply_cancel_and_undo_are_in_memory() -> None:
    before = _source_state(INVOICING)
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            _select_invoice(app)
            await pilot.pause()

            preview = app.query_one("#source-preview", TextArea)
            original = preview.text
            await pilot.click("#edit-source")
            await pilot.pause()

            assert app._source_editing
            assert not preview.read_only
            assert app.query_one("#studio-tree", Tree).disabled
            assert app.query_one("#apply-source", Button).display
            preview.load_text(
                original.replace("label: Invoices", "label: Expert invoices")
            )
            await pilot.press("ctrl+s")
            await pilot.pause()

            assert not app._source_editing
            assert preview.read_only
            assert app.state.valid
            assert app.state.dirty
            assert "+label: Expert invoices" in preview.text

            await pilot.click("#show-source")
            await pilot.pause()
            assert "label: Expert invoices" in preview.text

            await pilot.click("#undo-edit")
            await pilot.pause()
            assert not app.state.dirty
            await pilot.click("#show-source")
            await pilot.click("#edit-source")
            await pilot.pause()
            preview.load_text("entity: [\n")
            await pilot.click("#apply-source")
            await pilot.pause()
            assert app._source_editing
            assert not app.state.dirty

            await pilot.press("escape")
            await pilot.pause()
            assert not app._source_editing
            assert preview.read_only
            assert "entity: sales.Invoice" in preview.text

    asyncio.run(exercise())
    assert _source_state(INVOICING) == before


def test_tide_studio_command_launches_the_visual_adapter(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class StubApp:
        def __init__(self, service: StudioService) -> None:
            captured["workspace"] = service.workspace

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr("tide.tui.StudioApp", StubApp)

    result = main(["studio", str(INVOICING)])

    assert result == 0
    assert captured["workspace"].application == "TIDE Invoicing"
    assert captured["ran"] is True


def _source_state(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".py"}
    }


def _select_invoice(app: StudioApp) -> None:
    tree = app.query_one("#studio-tree", Tree)
    entity_group = tree.root.children[1]
    invoice_node = next(
        node for node in entity_group.children if node.label.plain == "sales.Invoice"
    )
    tree.select_node(invoice_node)


def _property_key(app: StudioApp, path: tuple[str | int, ...]) -> str:
    return next(key for key, item in app._property_rows.items() if item.path == path)
