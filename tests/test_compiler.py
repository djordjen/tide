from __future__ import annotations

from pathlib import Path

import pytest

from tide import CompilationFailed, compile_project
from tide.compiler.source import load_yaml_document

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def test_invoicing_fixture_compiles_to_immutable_model() -> None:
    model = compile_project(INVOICING)

    assert model.schema_version == "0.1"
    assert model.database == {"mode": "managed"}
    assert set(model.entities) == {
        "catalog.Product",
        "crm.Customer",
        "sales.Invoice",
        "sales.InvoiceLine",
    }
    assert len(model.views) == 9
    assert set(model.presets) == {"master_detail", "standard_browse", "standard_form"}
    assert model.formats["money"]["decimal_places"] == 2
    assert "sales.invoice.post" in model.permissions
    assert "sales.invoice.write" not in model.roles["auditor"]
    assert model.entity("sales.Invoice").metadata["permissions"]["audit"] == (
        "sales.invoice.audit"
    )
    assert model.entity("crm.Customer").metadata["permissions"]["audit"] == (
        "crm.customer.audit"
    )
    assert model.entity("sales.Invoice").field("invoice_date").metadata["audit"] == (
        "values"
    )
    assert model.entity("sales.Invoice").field("version").metadata["audit"] == "none"
    assert model.entity("sales.Invoice").field("total").dependencies == ("lines.total",)
    assert model.entity("sales.Invoice").field("version").metadata["concurrency_token"]
    assert model.entity("sales.Invoice").field("status").metadata["write"] == "action_only"
    assert (
        model.entity("sales.Invoice").field("invoice_date").metadata["default_factory"]
        == "today"
    )
    product_reference = model.entity("sales.InvoiceLine").field("product")
    assert product_reference.metadata["lookup_view"] == "catalog.Product.lookup"
    assert product_reference.metadata["on_select"]["assign"]["unit_price"] == {
        "from": "unit_price",
        "overwrite": "always",
    }
    assert model.entity("sales.InvoiceLine").field("quantity").metadata[
        "edit_mask"
    ] == "0.000"
    assert model.entity("catalog.Product").field("code").metadata["edit_mask"] == {
        "regex": "[A-Z][A-Z0-9-]{0,29}"
    }
    assert model.reports["sales.invoice"]["bands"]["record_header"][0]["field"] == (
        "number"
    )
    assert model.reports["sales.summary"]["kind"] == "summary"
    assert model.reports["sales.summary"]["aggregates"][1] == {
        "name": "sales_total",
        "function": "sum",
        "field": "total",
        "label": "Sales total",
        "format": "money",
    }
    assert model.diagnostics == ()
    resolved = model.views["sales.Invoice.edit"]
    assert resolved.data["settings"]["label_width"] == 18
    assert resolved.origins["settings.label_width"].layer == "application defaults"
    assert resolved.origins["settings.show_action_bar"].layer == "preset:standard_form"
    assert resolved.origins["surfaces.tui.minimum_width"].layer == "view overlay"
    assert resolved.data["fields"]["customer"] == {
        "editor": "lookup",
        "lookup_view": "crm.Customer.lookup",
        "allow_create": True,
        "create_view": "crm.Customer.edit",
    }
    assert resolved.data["actions"] == ("cancel", "save", "post")
    assert resolved.data["layout"][1]["actions"] == (
        "add",
        "apply",
        "remove",
    )
    lookup = model.views["catalog.Product.lookup"]
    assert lookup.kind == "lookup"
    assert lookup.data["columns"] == ("code", "name", "unit_price")
    inline = model.views["sales.InvoiceLine.inline_edit"]
    assert inline.data["fields"]["product"]["editor"] == "lookup"
    assert inline.data["fields"]["product"]["allow_create"] is True
    assert inline.data["fields"]["product"]["create_view"] == (
        "catalog.Product.edit"
    )
    assert inline.data["layout"][0]["rows"] == (
        ("line_number", "unit_price"),
        ("product", "quantity"),
        ("description",),
    )

    with pytest.raises(TypeError):
        model.entities["other.Entity"] = model.entity("sales.Invoice")  # type: ignore[index]
    with pytest.raises(TypeError):
        model.views["sales.Invoice.edit"]["fields"] = {}  # type: ignore[index]


def test_strict_yaml_does_not_coerce_legacy_boolean_words(tmp_path: Path) -> None:
    source = tmp_path / "strict.yaml"
    source.write_text("value: on\nboolean: true\n", encoding="utf-8")

    document = load_yaml_document(source)

    assert document.data == {"value": "on", "boolean": True}


@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("duplicate-key", "TIDE005"),
        ("unknown-property", "TIDE102"),
        ("unknown-field-type", "TIDE103"),
        ("permissionless-action", "TIDE226"),
        ("legacy-mapping", "TIDE228"),
        ("unknown-reference", "TIDE205"),
        ("unsafe-expression", "TIDE302"),
        ("computed-cycle", "TIDE214"),
        ("type-mismatch", "TIDE307"),
        ("missing-handler", "TIDE223"),
    ],
)
def test_invalid_fixtures_produce_stable_diagnostics(fixture: str, code: str) -> None:
    with pytest.raises(CompilationFailed) as caught:
        compile_project(ROOT / "tests" / "fixtures" / "invalid" / fixture)

    diagnostic_codes = {diagnostic.code for diagnostic in caught.value.diagnostics}
    assert code in diagnostic_codes
    assert all(diagnostic.location.line >= 1 for diagnostic in caught.value.diagnostics)


def test_explicitly_unrestricted_action_compiles_without_a_warning() -> None:
    model = compile_project(ROOT / "tests" / "fixtures" / "warning" / "permissionless-action")

    action = model.entity("demo.Thing").actions["touch"]
    assert action["unrestricted"] is True
    assert model.diagnostics == ()


def test_legacy_database_mapping_is_explicit_and_normalized() -> None:
    project = ROOT / "tests" / "fixtures" / "valid" / "legacy-database"

    model = compile_project(project)

    assert model.database == {"mode": "legacy"}
    customer = model.entity("legacy.Customer")
    assert customer.metadata["storage"] == {
        "table": "CUSTOMER_MASTER",
        "schema": "erp",
    }
    assert customer.field("id").metadata["column"] == "CUSTOMER_NO"
    assert customer.field("name").metadata["column"] == "DISPLAY_NAME"
    assert customer.field("account_manager").metadata["storage"] == "OWNER_EMPLOYEE_NO"
    assert customer.field("account_manager").target_entity == "legacy.Employee"


def test_legacy_database_requires_explicit_physical_field_mappings() -> None:
    project = ROOT / "tests" / "fixtures" / "invalid" / "legacy-mapping"

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    diagnostics = {diagnostic.code: diagnostic for diagnostic in caught.value.diagnostics}
    assert "TIDE228" in diagnostics
    assert "TIDE229" in diagnostics
    assert diagnostics["TIDE229"].path == ("fields", "id", "column")


def test_action_permission_and_unrestricted_access_are_mutually_exclusive(
    tmp_path: Path,
) -> None:
    project = tmp_path / "conflicting-action-access"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "tide.yaml").write_text(
        '\n'.join(
            [
                'schema_version: "0.1"',
                'application: {name: Conflicting Action Access, version: 0.1.0}',
                'model: {paths: [models]}',
            ]
        ),
        encoding="utf-8",
    )
    (project / "handlers.py").write_text(
        "def touch(record, context, payload):\n    return record\n",
        encoding="utf-8",
    )
    (models / "entity.yaml").write_text(
        '\n'.join(
            [
                'entity: demo.Thing',
                'fields: {id: {type: integer, primary_key: true}}',
                'actions:',
                '  touch:',
                '    label: Touch',
                '    permission: demo.thing.touch',
                '    unrestricted: true',
                '    execute: handlers.touch',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    assert "TIDE227" in {diagnostic.code for diagnostic in caught.value.diagnostics}


def test_today_default_factory_is_typed_and_exclusive(tmp_path: Path) -> None:
    project = tmp_path / "invalid-default-factory"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "tide.yaml").write_text(
        '\n'.join(
            [
                'schema_version: "0.1"',
                'application: {name: Invalid Defaults, version: 0.1.0}',
                'model: {paths: [models]}',
            ]
        ),
        encoding="utf-8",
    )
    (models / "entity.yaml").write_text(
        '\n'.join(
            [
                'entity: demo.Thing',
                'fields:',
                '  id: {type: integer, primary_key: true}',
                '  name: {type: string, default_factory: today}',
                '  occurred_on:',
                '    type: date',
                '    default: "2026-07-15"',
                '    default_factory: today',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    codes = {diagnostic.code for diagnostic in caught.value.diagnostics}
    assert {"TIDE217", "TIDE218"} <= codes


def test_lookup_editor_and_selection_assignments_are_validated(tmp_path: Path) -> None:
    project = tmp_path / "invalid-lookup"
    models = project / "models"
    views = project / "views"
    models.mkdir(parents=True)
    views.mkdir()
    (project / "tide.yaml").write_text(
        '\n'.join(
            [
                'schema_version: "0.1"',
                'application: {name: Invalid Lookup, version: 0.1.0}',
                'model: {paths: [models]}',
                'views: {paths: [views]}',
            ]
        ),
        encoding="utf-8",
    )
    (models / "product.yaml").write_text(
        '\n'.join(
            [
                'entity: demo.Product',
                'fields:',
                '  id: {type: integer, primary_key: true}',
                '  name: {type: string}',
                '  unit_price: {type: decimal}',
            ]
        ),
        encoding="utf-8",
    )
    (models / "line.yaml").write_text(
        '\n'.join(
            [
                'entity: demo.Line',
                'fields:',
                '  id: {type: integer, primary_key: true}',
                '  quantity: {type: decimal}',
                '  unit_price: {type: decimal}',
                '  product:',
                '    type: reference',
                '    target: demo.Product',
                '    lookup_view: demo.Product.browse',
                '    on_select:',
                '      assign:',
                '        unit_price: {from: name}',
            ]
        ),
        encoding="utf-8",
    )
    (views / "product.yaml").write_text(
        '\n'.join(
            [
                'view: demo.Product.browse',
                'entity: demo.Product',
                'kind: browse',
                'columns: [name]',
            ]
        ),
        encoding="utf-8",
    )
    (views / "line.yaml").write_text(
        '\n'.join(
            [
                'view: demo.Line.inline_edit',
                'entity: demo.Line',
                'kind: inline_edit',
                'columns: [product, quantity, unit_price]',
                'layout:',
                '  - rows:',
                '      - [product, product, unit_price]',
                'fields:',
                '  product: {editor: grid, allow_create: true}',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    codes = {diagnostic.code for diagnostic in caught.value.diagnostics}
    assert {"TIDE219", "TIDE238", "TIDE239", "TIDE241", "TIDE242"} <= codes
    layout_messages = {
        diagnostic.message
        for diagnostic in caught.value.diagnostics
        if diagnostic.code == "TIDE241"
    }
    assert {
        "inline editor rows support at most two fields",
        "inline editor layout repeats fields: product",
        "inline editor layout omits editable fields: quantity",
    } <= layout_messages


def test_edit_masks_are_compiler_validated(tmp_path: Path) -> None:
    project = tmp_path / "invalid-masks"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Invalid Masks, version: 0.1.0}",
                "model: {paths: [models]}",
            ]
        ),
        encoding="utf-8",
    )
    (models / "thing.yaml").write_text(
        "\n".join(
            [
                "entity: demo.Thing",
                "fields:",
                "  id: {type: integer, primary_key: true}",
                '  amount: {type: decimal, precision: 6, scale: 2, edit_mask: "0.000"}',
                '  count: {type: integer, edit_mask: "0.00"}',
                '  name: {type: string, edit_mask: {regex: "["}}',
                '  occurred_on: {type: date, edit_mask: "0.00"}',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    mask_messages = {
        diagnostic.message
        for diagnostic in caught.value.diagnostics
        if diagnostic.code == "TIDE243"
    }
    assert any("scale is 2" in message for message in mask_messages)
    assert "integer edit masks cannot contain fractional digits" in mask_messages
    assert any("invalid edit-mask regular expression" in message for message in mask_messages)
    assert any("typed edit masks" in message for message in mask_messages)


def test_view_tabs_collections_and_action_bars_are_compiler_validated(
    tmp_path: Path,
) -> None:
    project = tmp_path / "invalid-view-presentation"
    models = project / "models"
    views = project / "views"
    models.mkdir(parents=True)
    views.mkdir()
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Invalid Presentation, version: 0.1.0}",
                "model: {paths: [models]}",
                "views: {paths: [views]}",
            ]
        ),
        encoding="utf-8",
    )
    (models / "root.yaml").write_text(
        "\n".join(
            [
                "entity: demo.Root",
                "fields:",
                "  id: {type: integer, primary_key: true}",
                "  lines: {type: collection, target: demo.Line, inverse: root}",
            ]
        ),
        encoding="utf-8",
    )
    (models / "line.yaml").write_text(
        "\n".join(
            [
                "entity: demo.Line",
                "fields:",
                "  id: {type: integer, primary_key: true}",
                "  root: {type: reference, target: demo.Root}",
            ]
        ),
        encoding="utf-8",
    )
    (views / "root.yaml").write_text(
        "\n".join(
            [
                "view: demo.Root.edit",
                "entity: demo.Root",
                "kind: form",
                "actions: [save, save, dance]",
                "layout:",
                '  - {group: Root, tab: "", rows: [[id]]}',
                "  - collection: lines",
                "    view: demo.Line.browse",
                "    actions: [add, add, dance]",
            ]
        ),
        encoding="utf-8",
    )
    (views / "line.yaml").write_text(
        "\n".join(
            [
                "view: demo.Line.browse",
                "entity: demo.Line",
                "kind: browse",
                "columns: [id]",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    messages = {
        diagnostic.message
        for diagnostic in caught.value.diagnostics
        if diagnostic.code == "TIDE244"
    }
    assert any("layout tab must be" in message for message in messages)
    assert "view action bar repeats actions: save" in messages
    assert "view action bar contains unknown actions: dance" in messages
    assert "collection action bar repeats actions: add" in messages
    assert "collection action bar contains unknown actions: dance" in messages
    assert any("must be an inline_edit view" in message for message in messages)


def test_record_report_contract_is_compiler_validated(tmp_path: Path) -> None:
    project = tmp_path / "invalid-report"
    (project / "models").mkdir(parents=True)
    (project / "reports").mkdir()
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Invalid Report, version: 0.1.0}",
                "model: {paths: [models]}",
                "reports: {paths: [reports]}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "models" / "root.yaml").write_text(
        "\n".join(
            [
                "entity: demo.Root",
                "fields:",
                "  id: {type: integer, primary_key: true}",
                "  name: {type: string}",
                "  lines: {type: collection, target: demo.Line, inverse: root}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "models" / "line.yaml").write_text(
        "\n".join(
            [
                "entity: demo.Line",
                "fields:",
                "  id: {type: integer, primary_key: true}",
                "  root: {type: reference, target: demo.Root, storage: root_id, inverse: lines}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "reports" / "broken.yaml").write_text(
        "\n".join(
            [
                "report: demo.broken",
                "title: Broken",
                "entity: demo.Root",
                "parameters:",
                "  identity: {type: integer, required: false}",
                'query: {criteria: "name == $identity"}',
                "bands:",
                "  record_header:",
                "    - {field: missing, format: missing_format}",
                "    - {field: lines}",
                "  detail: {source: name, columns: [missing]}",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    codes = {diagnostic.code for diagnostic in caught.value.diagnostics}
    assert {"TIDE252", "TIDE253", "TIDE254", "TIDE255", "TIDE256"} <= codes


def test_summary_report_contract_is_compiler_validated(tmp_path: Path) -> None:
    project = tmp_path / "invalid-summary"
    (project / "models").mkdir(parents=True)
    (project / "reports").mkdir()
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Invalid Summary, version: 0.1.0}",
                "model: {paths: [models]}",
                "reports: {paths: [reports]}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "models" / "item.yaml").write_text(
        "\n".join(
            [
                "entity: demo.Item",
                "fields:",
                "  id: {type: integer, primary_key: true}",
                "  name: {type: string}",
                "  amount: {type: decimal, precision: 12, scale: 2}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "reports" / "broken.yaml").write_text(
        "\n".join(
            [
                "report: demo.summary",
                "title: Broken Summary",
                "entity: demo.Item",
                "kind: summary",
                "unrestricted: true",
                "query:",
                '  criteria: "name == \'A\' or name == \'B\'"',
                "  sort: [missing]",
                "group_by: [{field: missing}]",
                "aggregates:",
                "  - {name: invalid_total, function: sum, field: name}",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    codes = {diagnostic.code for diagnostic in caught.value.diagnostics}
    assert {"TIDE254", "TIDE257"} <= codes


def test_project_discovery_cannot_escape_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "tide.yaml").write_text(
        '\n'.join(
            [
                'schema_version: "0.1"',
                'application: {name: Confined, version: 0.1.0}',
                'model: {paths: [../outside]}',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    assert {diagnostic.code for diagnostic in caught.value.diagnostics} == {"TIDE012"}
