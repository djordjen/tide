from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
from typing import Any

import pytest
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Input, Select, Static, TextArea, Tree

from tide.cli import main
from tide.development import DesignerDocumentReference, StudioError, StudioService
from tide.tui import StudioApp
from tide.tui.studio import (
    StudioGroupsScreen,
    StudioLayoutScreen,
    StudioPreviewScreen,
    StudioSaveScreen,
)


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


def test_studio_service_resolves_list_form_and_inline_view_structure() -> None:
    before = _source_state(INVOICING)
    service = StudioService(INVOICING)

    browse = service.view_structure(
        DesignerDocumentReference(kind="view", name="sales.Invoice.browse")
    )
    inline = service.view_structure(
        DesignerDocumentReference(
            kind="view",
            name="sales.InvoiceLine.inline_edit",
        )
    )
    form = service.view_structure(
        DesignerDocumentReference(kind="view", name="sales.Invoice.edit")
    )

    assert browse.kind == "browse"
    assert browse.tracks[0].label == "Table columns"
    assert tuple(field.name for field in browse.fields) == (
        "number",
        "invoice_date",
        "customer",
        "status",
        "total",
    )
    assert all(field.editable for field in browse.fields)
    assert [track.label for track in inline.tracks] == [
        "Table columns",
        "Line editor · left column",
        "Line editor · right column",
    ]
    inline_fields = {field.key: field for field in inline.fields}
    assert tuple(inline_fields[key].name for key in inline.tracks[1].fields) == (
        "line_number",
        "product",
        "description",
    )
    assert tuple(inline_fields[key].name for key in inline.tracks[2].fields) == (
        "unit_price",
        "quantity",
    )
    assert tuple(field.name for field in inline.available_fields) == (
        "id",
        "invoice",
    )
    assert inline_fields["layout-left:product"].can_move_right
    assert inline_fields["layout-right:quantity"].can_move_left
    assert not inline_fields["layout-left:description"].can_move_right
    assert [track.label for track in form.tracks] == [
        "Form · left column",
        "Form · right column",
    ]
    form_fields = {field.key: field for field in form.fields}
    assert form_fields["layout-left:status"].can_move_right
    assert not form_fields["layout-left:customer"].can_move_right
    assert [
        (group.label, group.position, group.field_count) for group in form.groups
    ] == [
        ("Invoice", 0, 5),
        ("Totals", 2, 1),
        ("Posting", 3, 3),
    ]
    assert not form.groups[0].can_move_down
    assert not form.groups[1].can_move_up
    assert form.groups[1].can_move_down
    assert form.groups[2].can_move_up
    assert form.can_create_group
    assert form.editable
    assert _source_state(INVOICING) == before


def test_studio_service_moves_view_fields_atomically_with_undo() -> None:
    before = _source_state(INVOICING)
    service = StudioService(INVOICING)
    browse = DesignerDocumentReference(kind="view", name="sales.Invoice.browse")
    inline = DesignerDocumentReference(
        kind="view",
        name="sales.InvoiceLine.inline_edit",
    )
    form = DesignerDocumentReference(kind="view", name="sales.Invoice.edit")

    changed = service.move_view_field(browse, "columns:customer", -1)

    assert changed.valid
    assert changed.can_undo
    assert changed.changed_files == ("views/sales/invoice-browse.yaml",)
    assert (
        "columns: [number, customer, invoice_date, status, total]"
        in service.document(browse).source
    )
    assert tuple(
        field.name
        for field in service.view_structure(browse).fields
        if field.track == "columns"
    ) == ("number", "customer", "invoice_date", "status", "total")
    assert not service.undo().dirty

    changed = service.move_view_field(inline, "layout-left:description", -1)

    assert changed.valid
    structure = service.view_structure(inline)
    by_key = {field.key: field for field in structure.fields}
    left = next(track for track in structure.tracks if track.key == "layout-left")
    assert tuple(by_key[key].name for key in left.fields) == (
        "line_number",
        "description",
        "product",
    )
    source = service.document(inline).source
    assert "      - [description, quantity]" in source
    assert "      - [product]" in source
    assert not service.undo().dirty

    changed = service.move_view_field(form, "layout-left:status", -1)

    assert changed.valid
    form_structure = service.view_structure(form)
    form_fields = {field.key: field for field in form_structure.fields}
    form_left = next(
        track for track in form_structure.tracks if track.key == "layout-left"
    )
    assert tuple(form_fields[key].name for key in form_left.fields)[:3] == (
        "status",
        "number",
        "customer",
    )
    assert (
        "      - [status, invoice_date, number, currency]"
        in service.document(form).source
    )
    assert not service.undo().dirty
    assert _source_state(INVOICING) == before


def test_studio_service_swaps_view_columns_without_crossing_groups() -> None:
    before = _source_state(INVOICING)
    service = StudioService(INVOICING)
    inline = DesignerDocumentReference(
        kind="view",
        name="sales.InvoiceLine.inline_edit",
    )
    form = DesignerDocumentReference(kind="view", name="sales.Invoice.edit")

    changed = service.move_view_field_across(inline, "layout-left:product", 1)

    assert changed.valid
    inline_structure = service.view_structure(inline)
    inline_fields = {field.key: field for field in inline_structure.fields}
    left = next(
        track for track in inline_structure.tracks if track.key == "layout-left"
    )
    right = next(
        track for track in inline_structure.tracks if track.key == "layout-right"
    )
    assert tuple(inline_fields[key].name for key in left.fields) == (
        "line_number",
        "quantity",
        "description",
    )
    assert tuple(inline_fields[key].name for key in right.fields) == (
        "unit_price",
        "product",
    )
    assert "      - [quantity, product]" in service.document(inline).source
    assert not service.undo().dirty

    changed = service.move_view_field_across(form, "layout-left:status", 1)

    assert changed.valid
    assert (
        "      - [number, invoice_date, currency, status]"
        in service.document(form).source
    )
    assert not service.undo().dirty

    with pytest.raises(StudioError, match="same layout group"):
        service.move_view_field_across(form, "layout-left:customer", 1)
    with pytest.raises(StudioError, match="group boundary"):
        service.move_view_field(form, "layout-left:customer", 1)
    assert _source_state(INVOICING) == before


def test_studio_service_adds_and_removes_local_view_fields_atomically() -> None:
    before = _source_state(INVOICING)
    service = StudioService(INVOICING)
    browse = DesignerDocumentReference(kind="view", name="sales.Invoice.browse")
    inline = DesignerDocumentReference(
        kind="view",
        name="sales.InvoiceLine.inline_edit",
    )
    form = DesignerDocumentReference(kind="view", name="sales.Invoice.edit")

    changed = service.add_view_field(browse, "id")

    assert changed.valid
    assert "total, id]" in service.document(browse).source
    browse_structure = service.view_structure(browse)
    assert "id" not in {field.name for field in browse_structure.available_fields}
    assert next(
        field for field in browse_structure.fields if field.key == "columns:id"
    ).can_remove
    removed = service.remove_view_field(browse, "columns:id")
    assert removed.valid
    assert not removed.dirty

    changed = service.add_view_field(
        inline,
        "id",
        near_field_key="layout-left:description",
    )

    assert changed.valid
    source = service.document(inline).source
    assert (
        "columns: [line_number, product, description, quantity, unit_price, total, id]"
        in source
    )
    assert "      - [description, id]" in source
    inline_structure = service.view_structure(inline)
    assert next(
        field for field in inline_structure.fields if field.key == "columns:id"
    ).can_remove
    changed = service.add_view_field(
        inline,
        "invoice",
        near_field_key="layout-left:description",
    )
    assert changed.valid
    assert "      - [invoice]" in service.document(inline).source
    removed = service.remove_view_field(inline, "columns:invoice")
    assert removed.valid
    removed = service.remove_view_field(inline, "columns:id")
    assert removed.valid
    assert not removed.dirty

    changed = service.add_view_field(
        form,
        "id",
        near_field_key="layout-left:status",
    )

    assert changed.valid
    assert "      - [customer, id]" in service.document(form).source
    form_structure = service.view_structure(form)
    added = next(field for field in form_structure.fields if field.name == "id")
    assert added.source_group == "Invoice"
    removed = service.remove_view_field(form, added.key)
    assert removed.valid
    assert not removed.dirty
    assert _source_state(INVOICING) == before


def test_studio_service_manages_local_groups_and_explicit_add_targets() -> None:
    before = _source_state(INVOICING)
    service = StudioService(INVOICING)
    form = DesignerDocumentReference(kind="view", name="sales.Invoice.edit")

    with pytest.raises(StudioError, match="already exists"):
        service.create_view_group(form, "invoice")
    with pytest.raises(StudioError, match="collection or layout boundary"):
        service.move_view_group(form, "layout-group:2", -1)
    with pytest.raises(StudioError, match="only an empty"):
        service.remove_view_group(form, "layout-group:0")

    created = service.create_view_group(form, "Audit")

    assert created.valid
    structure = service.view_structure(form)
    audit = next(group for group in structure.groups if group.label == "Audit")
    assert audit.field_count == 0
    assert audit.can_remove
    assert audit.can_move_up
    assert "  - group: Audit" in service.document(form).source

    added = service.add_view_field(
        form,
        "id",
        destination_group_key=audit.key,
    )

    assert added.valid
    structure = service.view_structure(form)
    audit = next(group for group in structure.groups if group.label == "Audit")
    assert audit.field_count == 1
    assert not audit.can_remove
    id_field = next(field for field in structure.fields if field.name == "id")
    assert id_field.source_group == "Audit"
    assert "      - [id]" in service.document(form).source

    renamed = service.rename_view_group(form, audit.key, "Internal audit")

    assert renamed.valid
    structure = service.view_structure(form)
    audit = next(group for group in structure.groups if group.label == "Internal audit")
    id_field = next(field for field in structure.fields if field.name == "id")
    removed_field = service.remove_view_field(form, id_field.key)
    assert removed_field.valid
    structure = service.view_structure(form)
    audit = next(group for group in structure.groups if group.label == "Internal audit")
    assert audit.can_remove

    moved = service.move_view_group(form, audit.key, -1)

    assert moved.valid
    structure = service.view_structure(form)
    assert [group.label for group in structure.groups][-2:] == [
        "Internal audit",
        "Posting",
    ]
    audit = next(group for group in structure.groups if group.label == "Internal audit")
    removed_group = service.remove_view_group(form, audit.key)
    assert removed_group.valid
    assert not removed_group.dirty
    assert _source_state(INVOICING) == before


def test_studio_preserves_crlf_during_structural_and_expert_edits(
    tmp_path: Path,
) -> None:
    project = shutil.copytree(INVOICING, tmp_path / "crlf-invoicing")
    for path in project.rglob("*.yaml"):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(content.replace(b"\n", b"\r\n"))
    service = StudioService(project)
    browse = DesignerDocumentReference(kind="view", name="sales.Invoice.browse")
    form = DesignerDocumentReference(kind="view", name="sales.Invoice.edit")

    assert service.add_view_field(browse, "id").dirty
    assert not service.remove_view_field(browse, "columns:id").dirty

    created = service.create_view_group(form, "Temporary")
    temporary = next(
        group
        for group in service.view_structure(form).groups
        if group.label == "Temporary"
    )
    assert created.dirty
    assert not service.remove_view_group(form, temporary.key).dirty

    source = service.document(form).source.replace("\r\n", "\n")
    invalid = service.replace_document_source(
        form,
        source.replace("base: generated.edit", "base: missing.edit"),
    )
    assert not invalid.valid
    assert "\r\n" in service.document(form).source


def test_studio_service_manages_tabs_collections_and_action_bars_in_memory() -> None:
    before = _source_state(INVOICING)
    service = StudioService(INVOICING)
    form = DesignerDocumentReference(kind="view", name="sales.Invoice.edit")

    structure = service.view_structure(form)
    assert [(section.label, section.kind) for section in structure.sections] == [
        ("Invoice", "group"),
        ("Lines", "collection"),
        ("Totals", "group"),
        ("Posting", "group"),
    ]
    assert structure.record_actions == ("cancel", "save", "post")
    lines = next(
        section for section in structure.sections if section.collection == "lines"
    )
    assert lines.actions == ("add", "apply", "remove")
    assert not structure.available_collections

    changed = service.set_view_section_tab(form, "layout-section:0", "Details")
    assert changed.valid
    changed = service.set_view_section_tab(form, "layout-section:1", "Details")
    assert changed.valid
    changed = service.set_view_action_order(form, "record", ("post", "save", "cancel"))
    assert changed.valid
    changed = service.set_view_action_order(
        form,
        "layout-section:1",
        ("remove", "add", "apply"),
    )
    assert changed.valid

    structure = service.view_structure(form)
    assert [section.tab for section in structure.sections[:2]] == [
        "Details",
        "Details",
    ]
    assert structure.record_actions == ("post", "save", "cancel")
    lines = next(
        section for section in structure.sections if section.collection == "lines"
    )
    assert lines.actions == ("remove", "add", "apply")

    changed = service.move_view_section(form, lines.key, 1)
    assert changed.valid
    structure = service.view_structure(form)
    assert [section.label for section in structure.sections] == [
        "Invoice",
        "Totals",
        "Lines",
        "Posting",
    ]
    lines = next(
        section for section in structure.sections if section.collection == "lines"
    )
    changed = service.remove_view_collection(form, lines.key)
    assert changed.valid
    structure = service.view_structure(form)
    available = next(
        collection
        for collection in structure.available_collections
        if collection.name == "lines"
    )
    assert available.inline_views == ("sales.InvoiceLine.inline_edit",)
    changed = service.add_view_collection(
        form,
        "lines",
        "sales.InvoiceLine.inline_edit",
    )
    assert changed.valid

    with pytest.raises(StudioError, match="duplicate"):
        service.set_view_action_order(form, "record", ("save", "save"))
    with pytest.raises(StudioError, match="unknown"):
        service.set_view_action_order(form, "record", ("dance",))

    while service.state.can_undo:
        service.undo()
    assert not service.state.dirty
    assert _source_state(INVOICING) == before


def test_studio_service_previews_roles_and_terminal_constraints_without_data() -> None:
    before = _source_state(INVOICING)
    service = StudioService(INVOICING)
    form = DesignerDocumentReference(kind="view", name="sales.Invoice.edit")

    clerk = service.preview_view(
        form,
        role="sales_clerk",
        width=100,
        height=30,
    )

    assert clerk.fit == "fits"
    assert clerk.minimum_width == 100
    assert clerk.minimum_height == 26
    assert clerk.available_roles == (
        "auditor",
        "invoice_poster",
        "sales_clerk",
        "summary_viewer",
    )
    access = {item.operation: item.allowed for item in clerk.access}
    assert access == {
        "list": True,
        "read": True,
        "create": True,
        "update": True,
        "delete": False,
    }
    fields = {field.name: field for field in clerk.fields}
    assert fields["customer"].status == "conditional"
    assert fields["posted_by"].status == "protected"
    assert fields["lines"].status == "conditional"
    actions = {(action.bar, action.name): action for action in clerk.actions}
    assert actions[("record", "save")].enabled
    assert actions[("record", "post")].enabled
    assert actions[("record", "post")].runtime_condition
    assert actions[("layout-section:1", "add")].enabled
    assert not clerk.writes_performed
    assert not clerk.application_database_accessed
    assert not clerk.application_code_executed

    auditor = service.preview_view(
        form,
        role="auditor",
        width=100,
        height=30,
    )
    auditor_fields = {field.name: field for field in auditor.fields}
    auditor_actions = {action.name: action for action in auditor.actions}
    assert auditor.fit == "fits"
    assert auditor_fields["posted_by"].status == "read_only"
    assert auditor_fields["lines"].status == "read_only"
    assert not auditor_actions["save"].enabled
    assert not auditor_actions["post"].enabled

    summary = service.preview_view(
        form,
        role="summary_viewer",
        width=80,
        height=24,
    )
    summary_fields = {field.name: field for field in summary.fields}
    assert summary.fit == "constrained"
    assert summary_fields["lines"].status == "protected"
    assert summary_fields["posted_by"].status == "protected"
    assert any("declared minimum" in warning for warning in summary.warnings)
    assert any("estimated minimum" in warning for warning in summary.warnings)

    blocked = service.preview_view(form, role=None, width=100, height=30)
    assert blocked.fit == "blocked"
    assert any("cannot open" in warning for warning in blocked.warnings)
    customer_browse = DesignerDocumentReference(
        kind="view",
        name="crm.Customer.browse",
    )
    customer = service.preview_view(
        customer_browse,
        role="auditor",
        width=80,
        height=24,
    )
    assert customer.fit == "fits"
    assert customer.content_width > customer.width
    assert any("horizontal scrolling" in warning for warning in customer.warnings)
    assert any("row policy" in warning for warning in customer.warnings)
    assert all(field.status == "read_only" for field in customer.fields)
    denied_customer = service.preview_view(
        customer_browse,
        role="summary_viewer",
        width=80,
        height=24,
    )
    assert denied_customer.fit == "blocked"
    with pytest.raises(StudioError, match="unknown Studio preview role"):
        service.preview_view(form, role="missing", width=100, height=30)
    assert not service.state.dirty
    assert _source_state(INVOICING) == before


def test_studio_group_change_uses_the_same_save_boundary(tmp_path: Path) -> None:
    project = shutil.copytree(INVOICING, tmp_path / "invoicing")
    view_file = project / "views" / "sales" / "invoice-edit.yaml"
    service = StudioService(project)
    form = DesignerDocumentReference(kind="view", name="sales.Invoice.edit")
    service.create_view_group(form, "Audit")

    review = service.prepare_save()
    assert review.preparation.ready
    assert review.preparation.approval_prompt is not None
    result = service.save(review, review.preparation.approval_prompt)

    assert not result.state.dirty
    assert "  - group: Audit" in view_file.read_text(encoding="utf-8")
    assert (project / result.receipt_path).is_file()


def test_studio_structural_view_change_uses_the_same_save_boundary(
    tmp_path: Path,
) -> None:
    project = shutil.copytree(INVOICING, tmp_path / "invoicing")
    view_file = project / "views" / "sales" / "invoice-browse.yaml"
    service = StudioService(project)
    browse = DesignerDocumentReference(kind="view", name="sales.Invoice.browse")
    service.move_view_field(browse, "columns:customer", -1)

    review = service.prepare_save()
    assert review.preparation.ready
    assert review.preparation.approval_prompt is not None
    result = service.save(review, review.preparation.approval_prompt)

    assert not result.state.dirty
    assert (
        "columns: [number, customer, invoice_date, status, total]"
        in view_file.read_text(encoding="utf-8")
    )
    assert (project / result.receipt_path).is_file()


def test_studio_service_reviews_and_saves_through_the_approved_boundary(
    tmp_path: Path,
) -> None:
    project = shutil.copytree(INVOICING, tmp_path / "invoicing")
    invoice_file = project / "models" / "sales" / "invoice.yaml"
    original = invoice_file.read_text(encoding="utf-8")
    service = StudioService(project)
    invoice = DesignerDocumentReference(kind="entity", name="sales.Invoice")
    service.set_property(invoice, ("label",), "Saved invoices")

    review = service.prepare_save()

    assert review.writes_performed is False
    assert review.application_database_accessed is False
    assert review.preparation.ready
    assert review.preparation.base_state == "current"
    assert review.preparation.changed_files == ("models/sales/invoice.yaml",)
    assert review.preparation.approval_prompt is not None
    assert "+label: Saved invoices" in review.preparation.diff
    assert invoice_file.read_text(encoding="utf-8") == original

    with pytest.raises(StudioError, match="approval phrase does not match"):
        service.save(review, "SAVE something-else")
    assert invoice_file.read_text(encoding="utf-8") == original

    result = service.save(review, review.preparation.approval_prompt)

    assert result.writes_performed is True
    assert result.application_database_accessed is False
    assert result.changed_files == ("models/sales/invoice.yaml",)
    assert result.state.valid
    assert not result.state.dirty
    assert not result.state.can_undo
    assert not result.state.can_redo
    assert "label: Saved invoices" in invoice_file.read_text(encoding="utf-8")
    assert (project / result.receipt_path).is_file()
    assert service.state == result.state


def test_studio_save_review_detects_stale_sources_without_writing(
    tmp_path: Path,
) -> None:
    project = shutil.copytree(INVOICING, tmp_path / "invoicing")
    invoice_file = project / "models" / "sales" / "invoice.yaml"
    service = StudioService(project)
    invoice = DesignerDocumentReference(kind="entity", name="sales.Invoice")
    service.set_property(invoice, ("label",), "Candidate invoices")
    external = invoice_file.read_text(encoding="utf-8").replace(
        "label: Invoices",
        "label: Externally changed invoices",
    )
    invoice_file.write_text(external, encoding="utf-8")

    review = service.prepare_save()

    assert not review.preparation.ready
    assert review.preparation.base_state == "stale"
    assert review.preparation.blockers[0].code == "TIDEDSAVE003"
    assert review.writes_performed is False
    assert "Externally changed invoices" in invoice_file.read_text(encoding="utf-8")
    assert service.state.dirty


def test_studio_save_review_exposes_recovery_preview_guidance(
    tmp_path: Path,
) -> None:
    project = shutil.copytree(INVOICING, tmp_path / "invoicing")
    service = StudioService(project)
    invoice = DesignerDocumentReference(kind="entity", name="sales.Invoice")
    service.set_property(invoice, ("label",), "Candidate invoices")
    lock = project / ".tide-designer-save.lock"
    lock.write_text("legacy-approval-id\n", encoding="utf-8")

    review = service.prepare_save()

    assert not review.preparation.ready
    assert any(
        blocker.code == "TIDEDSAVE006" for blocker in review.preparation.blockers
    )
    assert review.recovery is not None
    assert review.recovery.recovery_required
    assert review.recovery_command is not None
    assert "tide designer recover" in review.recovery_command
    assert "--preview" in review.recovery_command
    assert lock.is_file()


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
            expected_target = DesignerDocumentReference(
                kind="entity",
                name="sales.Invoice",
            )
            tree.select_node(invoice_node)
            for _attempt in range(50):
                await pilot.pause()
                if app.selected_target == expected_target:
                    break

            assert app.selected_target == expected_target
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


def test_textual_studio_keeps_invalid_view_structure_explained_and_safe() -> None:
    before = _source_state(INVOICING)
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            _select_view(app, "sales.Invoice.edit")
            await pilot.pause()

            source = app.query_one("#source-preview", TextArea)
            original = source.text
            await pilot.click("#edit-source")
            await pilot.pause()
            source.load_text(
                original.replace("base: generated.edit", "base: missing.edit")
            )
            await pilot.click("#apply-source")
            await pilot.pause()

            assert not app.state.valid
            assert app.view_structure is None
            assert app.query_one("#view-structure", Horizontal).display
            explanation = str(
                app.query_one("#view-structure-preview", Static).content
            )
            assert "unknown base view 'missing.edit'" in explanation
            assert app.query_one("#preview-view", Button).disabled
            assert app.query_one("#manage-view-layout", Button).disabled

            await pilot.press("ctrl+z")
            await pilot.pause()
            assert app.state.valid
            assert app.view_structure is not None
            assert not app.query_one("#preview-view", Button).disabled

    asyncio.run(exercise())
    assert _source_state(INVOICING) == before


def test_textual_studio_reviews_and_explicitly_saves_a_candidate(
    tmp_path: Path,
) -> None:
    project = shutil.copytree(INVOICING, tmp_path / "invoicing")
    invoice_file = project / "models" / "sales" / "invoice.yaml"
    app = StudioApp(StudioService(project))

    async def exercise() -> None:
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            _select_invoice(app)
            await pilot.pause()

            table = app.query_one("#property-table", DataTable)
            label_key = _property_key(app, ("label",))
            table.move_cursor(row=list(app._property_rows).index(label_key))
            await pilot.pause()
            app.query_one("#property-value", Input).value = "Studio saved invoices"
            await pilot.click("#apply-property")
            await pilot.pause()

            assert not app.query_one("#save-candidate", Button).disabled
            await pilot.press("ctrl+s")
            await pilot.pause()

            assert isinstance(app.screen, StudioSaveScreen)
            screen = app.screen
            diff = screen.query_one("#studio-save-diff", TextArea)
            assert "+label: Studio saved invoices" in diff.text
            approval = screen.query_one("#studio-save-approval", Input)
            expected = screen.review.preparation.approval_prompt
            assert expected is not None
            approval.value = expected
            await pilot.pause()
            assert not screen.query_one("#confirm-save", Button).disabled
            await pilot.click("#confirm-save")
            await pilot.pause()

            assert not isinstance(app.screen, StudioSaveScreen)
            assert app.state.valid
            assert not app.state.dirty
            assert app.query_one("#save-candidate", Button).disabled
            assert "label: Studio saved invoices" in invoice_file.read_text(
                encoding="utf-8"
            )
            receipts = tuple((project / ".tide" / "designer").glob("*.json"))
            assert len(receipts) == 1

    asyncio.run(exercise())


def test_textual_studio_moves_and_previews_resolved_view_fields_without_writes() -> (
    None
):
    before = _source_state(INVOICING)
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            _select_view(app, "sales.InvoiceLine.inline_edit")
            await pilot.pause()

            panel = app.query_one("#view-structure", Horizontal)
            assert panel.display
            assert app.view_structure is not None
            assert "Line editor · left column" in str(
                app.query_one("#view-structure-preview", Static).content
            )
            table = app.query_one("#view-field-table", DataTable)
            description_key = _view_field_key(app, "layout-left:description")
            table.move_cursor(row=list(app._view_field_rows).index(description_key))
            await pilot.pause()

            assert app.selected_view_field is not None
            assert app.selected_view_field.name == "description"
            assert not app.query_one("#move-view-field-up", Button).disabled
            await pilot.click("#move-view-field-up")
            await pilot.pause()

            assert app.state.valid
            assert app.state.dirty
            assert app.view_structure is not None
            fields = {field.key: field for field in app.view_structure.fields}
            left = next(
                track
                for track in app.view_structure.tracks
                if track.key == "layout-left"
            )
            assert tuple(fields[key].name for key in left.fields) == (
                "line_number",
                "description",
                "product",
            )
            assert (
                "+      - [description, quantity]"
                in app.query_one("#source-preview", TextArea).text
            )

            await pilot.click("#undo-edit")
            await pilot.pause()
            assert not app.state.dirty

    asyncio.run(exercise())
    assert _source_state(INVOICING) == before


def test_textual_studio_swaps_adds_and_removes_view_fields_in_memory() -> None:
    before = _source_state(INVOICING)
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(150, 48)) as pilot:
            await pilot.pause()
            _select_view(app, "sales.InvoiceLine.inline_edit")
            await pilot.pause()

            table = app.query_one("#view-field-table", DataTable)
            product_key = _view_field_key(app, "layout-left:product")
            table.move_cursor(row=list(app._view_field_rows).index(product_key))
            await pilot.pause()
            assert not app.query_one("#move-view-field-right", Button).disabled
            await pilot.click("#move-view-field-right")
            await pilot.pause()

            assert app.state.valid
            assert app.selected_view_field is not None
            assert app.selected_view_field.key == "layout-right:product"
            assert (
                "      - [quantity, product]"
                in app.query_one("#source-preview", TextArea).text
            )

            selector = app.query_one("#view-field-add-choice", Select)
            selector.value = "id"
            await pilot.pause()
            assert not app.query_one("#add-view-field", Button).disabled
            await pilot.click("#add-view-field")
            await pilot.pause()

            assert app.state.valid
            assert app.selected_view_field is not None
            assert app.selected_view_field.key == "columns:id"
            assert not app.query_one("#remove-view-field", Button).disabled
            assert (
                "      - [description, id]"
                in app.query_one("#source-preview", TextArea).text
            )

            await pilot.click("#remove-view-field")
            await pilot.pause()
            assert app.state.valid
            await pilot.click("#show-source")
            await pilot.pause()
            assert (
                "columns: [line_number, product, description, quantity, unit_price, total]"
                in app.query_one("#source-preview", TextArea).text
            )

            await pilot.click("#undo-edit")
            await pilot.pause()
            await pilot.click("#undo-edit")
            await pilot.pause()
            await pilot.click("#undo-edit")
            await pilot.pause()
            assert not app.state.dirty

    asyncio.run(exercise())
    assert _source_state(INVOICING) == before


def test_textual_studio_targets_and_manages_view_groups_in_memory() -> None:
    before = _source_state(INVOICING)
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(150, 52)) as pilot:
            await pilot.pause()
            _select_view(app, "sales.Invoice.edit")
            await pilot.pause()

            assert app.view_structure is not None
            totals = next(
                group for group in app.view_structure.groups if group.label == "Totals"
            )
            add_selector = app.query_one("#view-field-add-choice", Select)
            group_selector = app.query_one("#view-field-group-choice", Select)
            add_selector.value = "id"
            group_selector.value = totals.key
            await pilot.pause()
            assert not app.query_one("#add-view-field", Button).disabled

            await pilot.click("#add-view-field")
            await pilot.pause()

            assert app.state.valid
            assert app.selected_view_field is not None
            assert app.selected_view_field.name == "id"
            assert app.selected_view_field.source_group == "Totals"
            assert (
                "      - [total, id]" in app.query_one("#source-preview", TextArea).text
            )

            await pilot.click("#undo-edit")
            await pilot.pause()
            assert not app.state.dirty

            await pilot.click("#manage-view-groups")
            await pilot.pause()
            assert isinstance(app.screen, StudioGroupsScreen)
            groups_screen = app.screen
            groups_screen.query_one("#studio-group-name", Input).value = "Audit"
            await pilot.pause()
            assert not groups_screen.query_one("#studio-group-create", Button).disabled
            await pilot.click("#studio-group-create")
            await pilot.pause()

            assert not isinstance(app.screen, StudioGroupsScreen)
            assert app.state.valid
            assert app.view_structure is not None
            audit = next(
                group for group in app.view_structure.groups if group.label == "Audit"
            )
            assert audit.can_remove

            await pilot.click("#manage-view-groups")
            await pilot.pause()
            groups_screen = app.screen
            assert isinstance(groups_screen, StudioGroupsScreen)
            groups_screen.query_one("#studio-group-select", Select).value = audit.key
            await pilot.pause()
            groups_screen.query_one("#studio-group-name", Input).value = "Review"
            await pilot.pause()
            await pilot.click("#studio-group-rename")
            await pilot.pause()

            assert app.view_structure is not None
            review = next(
                group for group in app.view_structure.groups if group.label == "Review"
            )
            await pilot.click("#manage-view-groups")
            await pilot.pause()
            groups_screen = app.screen
            assert isinstance(groups_screen, StudioGroupsScreen)
            groups_screen.query_one("#studio-group-select", Select).value = review.key
            await pilot.pause()
            assert not groups_screen.query_one("#studio-group-up", Button).disabled
            await pilot.click("#studio-group-up")
            await pilot.pause()

            assert app.view_structure is not None
            review = next(
                group for group in app.view_structure.groups if group.label == "Review"
            )
            await pilot.click("#manage-view-groups")
            await pilot.pause()
            groups_screen = app.screen
            assert isinstance(groups_screen, StudioGroupsScreen)
            groups_screen.query_one("#studio-group-select", Select).value = review.key
            await pilot.pause()
            assert not groups_screen.query_one("#studio-group-remove", Button).disabled
            await pilot.click("#studio-group-remove")
            await pilot.pause()

            assert not isinstance(app.screen, StudioGroupsScreen)
            assert app.state.valid
            assert not app.state.dirty

    asyncio.run(exercise())
    assert _source_state(INVOICING) == before


def test_textual_studio_manages_tabs_and_action_order_in_memory() -> None:
    before = _source_state(INVOICING)
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(150, 52)) as pilot:
            await pilot.pause()
            _select_view(app, "sales.Invoice.edit")
            await pilot.pause()

            assert not app.query_one("#manage-view-layout", Button).disabled
            await pilot.click("#manage-view-layout")
            await pilot.pause()
            assert isinstance(app.screen, StudioLayoutScreen)
            layout = app.screen
            layout.query_one(
                "#studio-layout-section", Select
            ).value = "layout-section:2"
            await pilot.pause()
            layout.query_one("#studio-layout-tab", Input).value = "Summary"
            await pilot.pause()
            assert not layout.query_one("#studio-layout-apply-tab", Button).disabled
            await pilot.click("#studio-layout-apply-tab")
            await pilot.pause()

            assert app.state.valid
            assert "    tab: Summary" in app.query_one("#source-preview", TextArea).text

            await pilot.click("#manage-view-layout")
            await pilot.pause()
            layout = app.screen
            assert isinstance(layout, StudioLayoutScreen)
            assert layout.query_one("#studio-layout-action-bar", Select).value == (
                "record"
            )
            assert (
                layout.query_one("#studio-layout-current-action", Select).value
                == "cancel"
            )
            assert not layout.query_one("#studio-layout-action-down", Button).disabled
            await pilot.click("#studio-layout-action-down")
            await pilot.pause()

            assert app.view_structure is not None
            assert app.view_structure.record_actions == ("save", "cancel", "post")
            action_diff = app.query_one("#source-preview", TextArea).text
            assert "+  - save" in action_diff
            assert "+  - cancel" in action_diff
            assert "+  - post" in action_diff

            await pilot.click("#undo-edit")
            await pilot.pause()
            await pilot.click("#undo-edit")
            await pilot.pause()
            assert not app.state.dirty

    asyncio.run(exercise())
    assert _source_state(INVOICING) == before


def test_textual_studio_previews_selected_role_and_terminal_size() -> None:
    before = _source_state(INVOICING)
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(150, 52)) as pilot:
            await pilot.pause()
            _select_view(app, "sales.Invoice.edit")
            await pilot.pause()

            assert not app.query_one("#preview-view", Button).disabled
            await pilot.click("#preview-view")
            await pilot.pause()

            assert isinstance(app.screen, StudioPreviewScreen)
            preview = app.screen
            assert preview.preview.role == "auditor"
            assert preview.preview.fit == "fits"
            canvas = preview.query_one("#studio-preview-canvas", TextArea)
            assert "sales.Invoice.edit · auditor · fits" in canvas.text
            assert "update:no" in canvas.text
            assert "post:off?" in canvas.text
            assert len(canvas.text.splitlines()[0]) == 100

            preview.query_one("#studio-preview-role", Select).value = "summary_viewer"
            preview.query_one("#studio-preview-size", Select).value = "80x24"
            await pilot.pause()

            assert preview.preview.role == "summary_viewer"
            assert preview.preview.fit == "constrained"
            assert "declared minimum of 100" in canvas.text
            assert "[P] Collection · Lines / Lines" in canvas.text
            assert len(canvas.text.splitlines()[0]) == 80

            preview.query_one("#studio-preview-role", Select).value = "sales_clerk"
            preview.query_one("#studio-preview-size", Select).value = "140x40"
            await pilot.pause()

            assert preview.preview.fit == "fits"
            assert "post:on?" in canvas.text
            assert len(canvas.text.splitlines()[0]) == 140
            await pilot.click("#studio-preview-close")
            await pilot.pause()
            assert not isinstance(app.screen, StudioPreviewScreen)
            assert not app.state.dirty

    asyncio.run(exercise())
    assert _source_state(INVOICING) == before


def test_textual_studio_keeps_lower_tools_reachable_on_compact_terminal() -> None:
    before = _source_state(INVOICING)
    app = StudioApp(StudioService(INVOICING))

    async def exercise() -> None:
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            _select_view(app, "sales.Invoice.edit")
            await pilot.pause()

            details = app.query_one("#studio-details")
            source = app.query_one("#source-preview", TextArea)
            assert details.show_vertical_scrollbar
            assert details.max_scroll_y > 0
            assert source.region.height == 10

            details.scroll_end(animate=False)
            await pilot.pause()
            preview_button = app.query_one("#preview-view", Button)
            assert 0 <= preview_button.region.y < app.size.height
            assert 0 < source.region.bottom <= app.size.height

            await pilot.click("#preview-view")
            await pilot.pause()
            assert isinstance(app.screen, StudioPreviewScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, StudioPreviewScreen)
            assert not app.state.dirty

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


def _select_view(app: StudioApp, name: str) -> None:
    tree = app.query_one("#studio-tree", Tree)
    view_group = tree.root.children[2]
    view_node = next(node for node in view_group.children if node.label.plain == name)
    tree.select_node(view_node)


def _property_key(app: StudioApp, path: tuple[str | int, ...]) -> str:
    return next(key for key, item in app._property_rows.items() if item.path == path)


def _view_field_key(app: StudioApp, field_key: str) -> str:
    return next(
        key for key, item in app._view_field_rows.items() if item.key == field_key
    )
