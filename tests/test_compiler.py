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
    assert "sales.invoice.post" in model.permissions
    assert "sales.invoice.write" not in model.roles["auditor"]
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
