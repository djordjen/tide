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


def test_model_validate_json_reports_warnings(capsys) -> None:
    fixture = ROOT / "tests" / "fixtures" / "warning" / "permissionless-action"
    result = main(["model", "validate", str(fixture), "--json"])
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["valid"] is True
    assert [warning["code"] for warning in output["warnings"]] == ["TIDE226"]
    assert [warning["severity"] for warning in output["warnings"]] == ["warning"]


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
