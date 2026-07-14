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
    assert set(model.entities) == {
        "catalog.Product",
        "crm.Customer",
        "sales.Invoice",
        "sales.InvoiceLine",
    }
    assert len(model.views) == 3
    assert set(model.presets) == {"master_detail", "standard_browse", "standard_form"}
    assert "sales.invoice.post" in model.permissions
    assert "sales.invoice.write" not in model.roles["auditor"]
    assert model.entity("sales.Invoice").field("total").dependencies == ("lines.total",)
    assert model.entity("sales.Invoice").field("version").metadata["concurrency_token"]
    assert model.entity("sales.Invoice").field("status").metadata["write"] == "action_only"
    assert model.diagnostics == ()
    resolved = model.views["sales.Invoice.edit"]
    assert resolved.data["settings"]["label_width"] == 18
    assert resolved.origins["settings.label_width"].layer == "application defaults"
    assert resolved.origins["settings.show_action_bar"].layer == "preset:standard_form"
    assert resolved.origins["surfaces.tui.minimum_width"].layer == "view overlay"

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


def test_permissionless_action_compiles_with_a_warning() -> None:
    model = compile_project(ROOT / "tests" / "fixtures" / "warning" / "permissionless-action")

    warnings = {(diagnostic.code, diagnostic.severity.value) for diagnostic in model.diagnostics}
    assert ("TIDE226", "warning") in warnings


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
