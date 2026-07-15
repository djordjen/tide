from __future__ import annotations

import json
from pathlib import Path

from tide.cli import main

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def test_model_validate_json(capsys) -> None:
    result = main(["model", "validate", str(INVOICING), "--json"])
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output == {
        "valid": True,
        "application": "TIDE Invoicing",
        "version": "0.1.0",
        "schema_version": "0.1",
        "entities": 4,
        "views": 3,
        "reports": 1,
        "warnings": [],
    }


def test_model_validate_json_accepts_explicitly_unrestricted_action(capsys) -> None:
    fixture = ROOT / "tests" / "fixtures" / "warning" / "permissionless-action"
    result = main(["model", "validate", str(fixture), "--json"])
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["valid"] is True
    assert output["warnings"] == []


def test_model_explain_reports_dependencies(capsys) -> None:
    result = main(
        [
            "model",
            "explain",
            "sales.Invoice.total",
            "--project",
            str(INVOICING),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["dependencies"] == ["lines.total"]


def test_schema_export_is_strict(capsys) -> None:
    result = main(["model", "schema", "project"])
    schema = json.loads(capsys.readouterr().out)

    assert result == 0
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "0.1"
    assert schema["properties"]["database"]["$ref"].endswith("/DatabaseSource")


def test_view_explain_includes_resolved_provenance(capsys) -> None:
    result = main(
        [
            "view",
            "explain",
            "sales.Invoice.edit",
            "--project",
            str(INVOICING),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["settings"]["label_width"] == 18
    assert output["provenance"]["settings.label_width"]["layer"] == "application defaults"


def test_api_export_openapi_emits_read_only_preview(capsys) -> None:
    result = main(["api", "export-openapi", str(INVOICING)])
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["openapi"] == "3.1.0"
    assert output["x-tide"]["read_only"] is True
    assert set(output["paths"]["/api/v1/invoices"]) == {"get"}
    assert "/api/v1/invoices/{id}" in output["paths"]


def test_api_export_openapi_writes_output_file(tmp_path) -> None:
    output_file = tmp_path / "openapi.json"

    result = main(
        [
            "api",
            "export-openapi",
            str(INVOICING),
            "--base-path",
            "/internal/v1",
            "--output",
            str(output_file),
        ]
    )
    output = json.loads(output_file.read_text(encoding="utf-8"))

    assert result == 0
    assert "/internal/v1/invoices" in output["paths"]


def test_api_export_openapi_reports_invalid_base_path(capsys) -> None:
    result = main(
        [
            "api",
            "export-openapi",
            str(INVOICING),
            "--base-path",
            "api/v1",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "OpenAPI preview failed: API base path must start with '/'\n"
    )
