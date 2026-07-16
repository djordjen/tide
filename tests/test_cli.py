from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine

from tide import compile_project
from tide.cli import main
from tide.data import (
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemyRepository,
)

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
        "views": 9,
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


def test_tide_run_database_requires_configured_environment_variable(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("MISSING_TIDE_DATABASE_URL", raising=False)

    result = main(
        [
            "run",
            str(INVOICING),
            "--database-env",
            "MISSING_TIDE_DATABASE_URL",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "TUI database startup failed: environment variable "
        "'MISSING_TIDE_DATABASE_URL' is not set\n"
    )


def test_tide_run_create_schema_requires_database_selection(capsys) -> None:
    result = main(["run", str(INVOICING), "--create-schema"])

    assert result == 1
    assert capsys.readouterr().err == (
        "TUI startup failed: --create-schema requires --database-env\n"
    )


def test_tide_run_database_error_does_not_echo_environment_value(
    monkeypatch,
    capsys,
) -> None:
    secret_value = "invalid-database-url-containing-SUPERSECRET"
    monkeypatch.setenv("BROKEN_TIDE_DATABASE_URL", secret_value)

    result = main(
        [
            "run",
            str(INVOICING),
            "--database-env",
            "BROKEN_TIDE_DATABASE_URL",
        ]
    )

    error = capsys.readouterr().err
    assert result == 1
    assert error == (
        "TUI database startup failed via 'BROKEN_TIDE_DATABASE_URL': "
        "ArgumentError\n"
    )
    assert secret_value not in error


def test_db_seed_populates_empty_managed_database_deterministically(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    def seed_database(name: str) -> tuple[list[dict], list[dict], list[dict]]:
        database = tmp_path / f"{name}.db"
        url = f"sqlite+pysqlite:///{database.as_posix()}"
        model = compile_project(INVOICING)
        repository = SQLAlchemyRepository(model, url)
        repository.create_schema()
        SQLAlchemyCursorStore(repository.engine, mode="managed").create_schema()
        SQLAlchemyActionExecutionStore(
            repository.engine,
            mode="managed",
        ).create_schema()
        repository.dispose()
        monkeypatch.setenv("SEED_DATABASE_URL", url)

        result = main(
            [
                "db",
                "seed",
                str(INVOICING),
                "--database-env",
                "SEED_DATABASE_URL",
                "--customers",
                "4",
                "--products",
                "3",
                "--invoices",
                "6",
                "--random-seed",
                "12345",
            ]
        )
        assert result == 0
        assert "customers=4, products=3, invoices=6" in capsys.readouterr().out

        persisted = SQLAlchemyRepository(model, create_engine(url))
        try:
            return (
                persisted.all("crm.Customer"),
                persisted.all("catalog.Product"),
                persisted.all("sales.Invoice"),
            )
        finally:
            persisted.dispose()

    first = seed_database("first")
    second = seed_database("second")

    assert first == second
    assert tuple(len(records) for records in first) == (4, 3, 6)
    assert {invoice["status"] for invoice in first[2]} <= {"draft", "posted"}

    repeated = main(
        [
            "db",
            "seed",
            str(INVOICING),
            "--database-env",
            "SEED_DATABASE_URL",
        ]
    )
    assert repeated == 1
    assert "database is not empty" in capsys.readouterr().err
