from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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


def test_api_check_server_requires_token_environment(monkeypatch, capsys) -> None:
    monkeypatch.delenv("MISSING_TIDE_API_TOKEN", raising=False)

    result = main(
        [
            "api",
            "check-server",
            str(INVOICING),
            "--token-env",
            "MISSING_TIDE_API_TOKEN",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API check failed: bearer-token environment variable "
        "'MISSING_TIDE_API_TOKEN' is not set\n"
    )


def test_api_check_server_reports_compatible_authenticated_session(
    monkeypatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}
    token = "check-server-secret-token"
    monkeypatch.setenv("CHECK_TIDE_API_TOKEN", token)

    class StubClient:
        def __init__(self, model, url, received_token, *, base_path) -> None:
            captured.update(
                model=model,
                url=url,
                token=received_token,
                base_path=base_path,
            )

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def connect(self):
            return SimpleNamespace(
                application="TIDE Invoicing",
                application_version="0.1.0",
                principal="api:test",
                entities={
                    "sales.Invoice": SimpleNamespace(
                        operations=("list", "get", "create", "update"),
                        actions=("post",),
                    )
                },
            )

    monkeypatch.setattr("tide.api.client.TideApiClient", StubClient)

    result = main(
        [
            "api",
            "check-server",
            str(INVOICING),
            "--url",
            "https://tide.example.test",
            "--token-env",
            "CHECK_TIDE_API_TOKEN",
        ]
    )

    output = capsys.readouterr().out
    assert result == 0
    assert output == (
        "Connected to TIDE Invoicing 0.1.0 as api:test "
        "(4 operation(s), 1 action(s)).\n"
    )
    assert captured["url"] == "https://tide.example.test"
    assert captured["token"] == token
    assert captured["base_path"] == "/api/v1"
    assert token not in output


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


def test_tide_run_remote_requires_token_without_echoing_url_secrets(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("MISSING_REMOTE_TOKEN", raising=False)

    result = main(
        [
            "run",
            str(INVOICING),
            "--api-url",
            "http://127.0.0.1:8000",
            "--api-token-env",
            "MISSING_REMOTE_TOKEN",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "TUI remote startup failed: bearer-token environment variable "
        "'MISSING_REMOTE_TOKEN' is not set\n"
    )


def test_tide_run_remote_builds_tui_without_local_storage(
    monkeypatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}
    token = "remote-tui-secret-token"
    monkeypatch.setenv("REMOTE_TIDE_TOKEN", token)

    class StubClient:
        def __init__(
            self,
            model,
            url,
            received_token,
            *,
            base_path,
            timeout,
        ) -> None:
            captured.update(
                model=model,
                url=url,
                token=received_token,
                base_path=base_path,
                timeout=timeout,
            )

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def connect(self):
            return SimpleNamespace(
                principal="api:remote-user",
                roles=("sales_clerk",),
            )

    class StubRecords:
        def __init__(self, model, client, session) -> None:
            captured["records"] = (model, client, session)

    class StubActions:
        def __init__(self, client) -> None:
            captured["actions"] = client

    class StubReports:
        pass

    class StubApp:
        def __init__(self, model, records, context, **configuration) -> None:
            captured["app"] = (model, records, context, configuration)

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr("tide.api.client.TideApiClient", StubClient)
    monkeypatch.setattr("tide.api.remote.RemoteRecordsService", StubRecords)
    monkeypatch.setattr("tide.api.remote.RemoteActionService", StubActions)
    monkeypatch.setattr("tide.api.remote.RemoteReportService", StubReports)
    monkeypatch.setattr("tide.tui.TideApp", StubApp)

    result = main(
        [
            "run",
            str(INVOICING),
            "--api-url",
            "http://127.0.0.1:8767",
            "--api-token-env",
            "REMOTE_TIDE_TOKEN",
            "--api-timeout",
            "7.5",
            "--page-size",
            "4",
        ]
    )

    assert result == 0
    assert captured["url"] == "http://127.0.0.1:8767"
    assert captured["token"] == token
    assert captured["base_path"] == "/api/v1"
    assert captured["timeout"] == 7.5
    assert captured["ran"] is True
    _model, _records, context, configuration = captured["app"]
    assert context.principal.identifier == "api:remote-user"
    assert context.principal.roles == frozenset({"sales_clerk"})
    assert configuration["source_label"] == (
        "remote API http://127.0.0.1:8767"
    )
    assert configuration["page_size"] == 4
    assert token not in capsys.readouterr().out


def test_tide_run_remote_rejects_schema_creation(capsys) -> None:
    result = main(
        [
            "run",
            str(INVOICING),
            "--api-url",
            "http://127.0.0.1:8000",
            "--create-schema",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "TUI remote startup failed: --create-schema cannot be used with "
        "--api-url\n"
    )


def test_tide_run_remote_rejects_client_selected_identity(capsys) -> None:
    result = main(
        [
            "run",
            str(INVOICING),
            "--api-url",
            "http://127.0.0.1:8000",
            "--role",
            "sales_clerk",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "TUI remote startup failed: --role and --principal are assigned by the "
        "API server\n"
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
