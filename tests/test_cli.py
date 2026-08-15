from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from tide import compile_project
from tide.api.auth import OidcJwtAuthenticator
from tide.api.browser_auth import OidcBrowserAuth, OidcBrowserProviderInfo
from tide.cli import main
from tide.cli.database import _seed_counts
from tide.data import (
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemySessionStore,
    SQLAlchemyRepository,
)
from tide.development import (
    DesignerCommandBatch,
    DesignerSaveApproval,
    DesignerSaveService,
    DesignerService,
)
from tide.development import designer_save as designer_save_module

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


class _SimulatedCliProcessLoss(BaseException):
    pass


def test_designer_preview_json_prepares_save_without_writing(
    tmp_path: Path,
    capsys,
) -> None:
    project, changes = _write_designer_cli_fixture(tmp_path)
    before = (project / "models" / "item.yaml").read_bytes()

    result = main(["designer", "preview", str(project), str(changes), "--json"])
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["ready"] is True
    assert output["writes_performed"] is False
    assert output["changed_files"] == ["models/item.yaml"]
    assert output["approval_prompt"].startswith("SAVE tide-designer-approval-")
    assert (project / "models" / "item.yaml").read_bytes() == before
    assert not (project / ".tide").exists()


def test_designer_save_requires_exact_interactive_confirmation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project, changes = _write_designer_cli_fixture(tmp_path)
    before = (project / "models" / "item.yaml").read_bytes()
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")

    result = main(["designer", "save", str(project), str(changes)])

    captured = capsys.readouterr()
    assert result == 1
    assert "Exact candidate diff:" in captured.out
    assert "cancelled; no files were written" in captured.err
    assert (project / "models" / "item.yaml").read_bytes() == before


def test_designer_save_publishes_exact_approved_candidate(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project, changes = _write_designer_cli_fixture(tmp_path)
    preview_result = main(["designer", "preview", str(project), str(changes), "--json"])
    preview = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: preview["approval_prompt"],
    )

    result = main(["designer", "save", str(project), str(changes)])

    captured = capsys.readouterr()
    assert preview_result == 0
    assert result == 0
    assert "Saved 1 YAML source file(s)" in captured.out
    assert "Stock items" in (project / "models" / "item.yaml").read_text(
        encoding="utf-8"
    )
    receipt = project / preview["receipt_path"]
    assert receipt.is_file()
    assert compile_project(project).name == "Designer CLI Fixture"


def test_designer_recovery_preview_reports_no_interruption(
    tmp_path: Path,
    capsys,
) -> None:
    project, _changes = _write_designer_cli_fixture(tmp_path)

    result = main(
        ["designer", "recover", str(project), "--preview", "--json"]
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["ready"] is False
    assert output["recovery_required"] is False
    assert output["writes_performed"] is False


def test_designer_recovery_preview_is_read_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project, changes = _write_designer_cli_fixture(tmp_path)
    _interrupt_designer_cli_save(project, changes, monkeypatch)
    interrupted = (project / "models" / "item.yaml").read_bytes()

    result = main(
        ["designer", "recover", str(project), "--preview", "--json"]
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["ready"] is True
    assert output["recovery_action"] == "rollback"
    assert output["approval_prompt"].startswith(
        "RECOVER tide-designer-recovery-"
    )
    assert (project / "models" / "item.yaml").read_bytes() == interrupted


def test_designer_recovery_requires_exact_interactive_confirmation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project, changes = _write_designer_cli_fixture(tmp_path)
    _interrupt_designer_cli_save(project, changes, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")

    result = main(["designer", "recover", str(project)])

    captured = capsys.readouterr()
    assert result == 1
    assert "cancelled; no files were changed" in captured.err
    assert (project / DesignerSaveService.lock_name).exists()


def test_designer_recovery_restores_after_exact_confirmation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project, changes = _write_designer_cli_fixture(tmp_path)
    _interrupt_designer_cli_save(project, changes, monkeypatch)
    preview_result = main(
        ["designer", "recover", str(project), "--preview", "--json"]
    )
    preview = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: preview["approval_prompt"],
    )

    result = main(["designer", "recover", str(project)])

    captured = capsys.readouterr()
    assert preview_result == 0
    assert result == 0
    assert "recovered by rollback" in captured.out
    assert 'label: "Items"' in (
        project / "models" / "item.yaml"
    ).read_text(encoding="utf-8")
    assert not (project / DesignerSaveService.lock_name).exists()


def test_app_preview_json_prepares_approval_without_writing(
    tmp_path: Path,
    capsys,
) -> None:
    plan = _write_generation_plan(tmp_path)

    result = main(
        [
            "app",
            "preview",
            str(plan),
            "--workspace",
            str(tmp_path),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["ready"] is True
    assert output["writes_performed"] is False
    assert output["target_path"] == "applications/generated-cli-app"
    assert output["approval_prompt"].startswith("APPLY tide-approval-")
    assert not (tmp_path / "applications").exists()


def test_app_apply_requires_exact_interactive_confirmation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    plan = _write_generation_plan(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")

    result = main(["app", "apply", str(plan), "--workspace", str(tmp_path)])

    captured = capsys.readouterr()
    assert result == 1
    assert "Exact candidate diff:" in captured.out
    assert "cancelled; no files were written" in captured.err
    assert not (tmp_path / "applications").exists()


def test_app_apply_publishes_after_exact_interactive_confirmation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    plan = _write_generation_plan(tmp_path)
    preview_result = main(
        [
            "app",
            "preview",
            str(plan),
            "--workspace",
            str(tmp_path),
            "--json",
        ]
    )
    preview = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: preview["approval_prompt"],
    )

    result = main(["app", "apply", str(plan), "--workspace", str(tmp_path)])

    captured = capsys.readouterr()
    target = tmp_path / "applications" / "generated-cli-app"
    assert preview_result == 0
    assert result == 0
    assert "Applied" in captured.out
    assert "applications/generated-cli-app/.tide-apply.json" in captured.out
    assert compile_project(target).name == "Generated CLI App"


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
        "reports": 2,
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


def _auth_commands_that_read_a_store() -> dict[str, frozenset[str]]:
    """`tide auth` subcommands taking a `--store` they do not create, and their flags.

    Derived from the parser rather than listed, so a sixth local-identity
    command is covered the day it is added -- which is the failure mode the
    remedy itself exists to answer, one level up. The flags come from the same
    place, because they differ per subcommand and a hand-written set here would
    drift exactly as the command list would.
    """

    import argparse

    from tide.cli.main import _create_parser

    def children(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return dict(action.choices)
        return {}

    authentication = children(_create_parser())["auth"]
    found = {}
    for name, command in children(authentication).items():
        options = frozenset(
            option
            for action in command._actions
            for option in action.option_strings
            if option.startswith("--")
        )
        # `create-user` is the one that initializes the store, so it is the
        # answer rather than a caller of it.
        if name != "create-user" and "--store" in options:
            found[name] = options
    return found


AUTH_COMMANDS_READING_A_STORE = _auth_commands_that_read_a_store()


def test_the_store_reading_auth_commands_are_found() -> None:
    """A scan that found nothing would make the parametrized test vacuous."""

    assert sorted(AUTH_COMMANDS_READING_A_STORE) == [
        "disable-user",
        "enable-user",
        "set-password",
        "set-roles",
    ]


@pytest.mark.parametrize("command", sorted(AUTH_COMMANDS_READING_A_STORE))
def test_a_local_command_names_the_command_that_creates_a_missing_store(
    command: str,
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    """The path is not the answer; one command is, and it went unwritten.

    Every local-identity command except `create-user` reads a store it will not
    create, and said so by printing the path it could not find. The remedy was
    recorded nowhere but `start.bat`, so a reader outside Windows had to infer
    a subcommand, a flag and a role from an error that mentioned none of them.
    """

    monkeypatch.setenv("TIDE_TEST_LOCAL_PASSWORD", "a-long-enough-password")
    missing = tmp_path / "absent-auth.sqlite3"
    options = AUTH_COMMANDS_READING_A_STORE[command]

    result = main(
        [
            "auth",
            command,
            str(INVOICING),
            "--store",
            str(missing),
            *(["--username", "admin"] if "--username" in options else []),
            *(
                ["--password-env", "TIDE_TEST_LOCAL_PASSWORD"]
                if "--password-env" in options
                else []
            ),
            *(["--role", "auditor"] if "--role" in options else []),
        ]
    )
    errors = capsys.readouterr().err

    assert result == 1
    assert "local identity store is not initialized" in errors
    assert "tide auth create-user" in errors
    assert f"--store {missing}" in errors
    # Choosing a role for someone is a security decision, so the remedy names
    # what the application defines and lets the reader pick.
    assert "auditor" in errors and "sales_clerk" in errors


def test_serve_names_the_command_that_creates_a_missing_store(
    capsys,
    tmp_path: Path,
) -> None:
    """The same wall, on the path a new reader actually walks into it."""

    missing = tmp_path / "absent-auth.sqlite3"

    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "local",
            "--local-auth-store",
            str(missing),
        ]
    )
    errors = capsys.readouterr().err

    assert result == 1
    assert "local identity store is not initialized" in errors
    assert "tide auth create-user" in errors
    assert f"--store {missing}" in errors


def test_an_existing_store_that_fails_for_another_reason_gets_no_remedy(
    capsys,
    tmp_path: Path,
) -> None:
    """"Create it" is wrong advice for a store that is already there."""

    present = tmp_path / "present-auth.sqlite3"
    present.write_text("not a SQLite database", encoding="utf-8")

    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "local",
            "--local-auth-store",
            str(present),
        ]
    )
    errors = capsys.readouterr().err

    assert result == 1
    assert "tide auth create-user" not in errors


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


def test_auth_check_oidc_reports_safe_provider_compatibility(
    monkeypatch,
    capsys,
) -> None:
    captured: dict[str, dict[str, object]] = {}
    secret = "provider-client-secret"
    monkeypatch.setenv("CHECK_WEB_CLIENT_SECRET", secret)

    class StubAuthenticator:
        pass

    def fake_authenticator_discovery(cls, **configuration):
        captured["authenticator"] = configuration
        return StubAuthenticator()

    def fake_browser_discovery(cls, **configuration):
        captured["browser"] = configuration
        return SimpleNamespace(
            provider_info=OidcBrowserProviderInfo(
                issuer="https://identity.example.test/tenant",
                authorization_endpoint=(
                    "https://identity.example.test/tenant/authorize"
                ),
                token_endpoint="https://identity.example.test/tenant/token",
                response_types=("code",),
                grant_types=("authorization_code", "refresh_token"),
                pkce_methods=("S256",),
                token_endpoint_auth_methods=("client_secret_basic",),
                scopes=("openid", "profile", "offline_access"),
                warnings=(),
            )
        )

    monkeypatch.setattr(
        OidcJwtAuthenticator,
        "from_discovery",
        classmethod(fake_authenticator_discovery),
    )
    monkeypatch.setattr(
        OidcBrowserAuth,
        "from_discovery",
        classmethod(fake_browser_discovery),
    )

    result = main(
        [
            "auth",
            "check-oidc",
            str(INVOICING),
            "--oidc-issuer",
            "https://identity.example.test/tenant",
            "--oidc-audience",
            "tide-api",
            "--oidc-role-map",
            "external-sales=sales_clerk",
            "--web-oidc-client-id",
            "tide-web",
            "--web-oidc-client-secret-env",
            "CHECK_WEB_CLIENT_SECRET",
            "--web-oidc-redirect-uri",
            "https://tide.example.test/api/v1/_tide/browser-auth/callback",
            "--web-oidc-scope",
            "openid",
            "--web-oidc-scope",
            "offline_access",
            "--json",
        ]
    )

    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert result == 0
    assert output["compatible"] is True
    assert output["provider"]["pkce_methods"] == ["S256"]
    assert output["provider"]["refresh_token_advertised"] is True
    assert output["browser_client"]["client_authentication"] == (
        "client_secret_basic"
    )
    assert output["role_map"] == {"external-sales": "sales_clerk"}
    assert captured["authenticator"]["audience"] == "tide-api"
    assert captured["browser"]["client_secret"] == secret
    assert secret not in output_text


def test_auth_check_oidc_requires_configured_client_secret(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("MISSING_WEB_CLIENT_SECRET", raising=False)

    result = main(
        [
            "auth",
            "check-oidc",
            str(INVOICING),
            "--oidc-issuer",
            "https://identity.example.test/tenant",
            "--oidc-audience",
            "tide-api",
            "--web-oidc-client-id",
            "tide-web",
            "--web-oidc-client-secret-env",
            "MISSING_WEB_CLIENT_SECRET",
            "--web-oidc-redirect-uri",
            "https://tide.example.test/api/v1/_tide/browser-auth/callback",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "OIDC preflight failed: browser client-secret environment variable "
        "'MISSING_WEB_CLIENT_SECRET' is not set\n"
    )


def test_auth_create_user_and_set_password_use_separate_local_store(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    from functools import partial

    from tide.api import local_auth as local_auth_module

    monkeypatch.setattr(
        local_auth_module,
        "LocalUserStore",
        partial(local_auth_module.LocalUserStore, password_iterations=1_000),
    )
    store = tmp_path / "local-auth.sqlite3"
    monkeypatch.setenv("TEST_LOCAL_PASSWORD", "correct horse battery staple")

    created = main(
        [
            "auth",
            "create-user",
            str(INVOICING),
            "--store",
            str(store),
            "--username",
            "Alice",
            "--display-name",
            "Alice Example",
            "--role",
            "sales_clerk",
            "--password-env",
            "TEST_LOCAL_PASSWORD",
        ]
    )
    created_output = capsys.readouterr()
    assert created == 0
    assert "Created local user 'alice'" in created_output.out
    assert "correct horse" not in created_output.out
    assert store.is_file()

    monkeypatch.setenv("TEST_LOCAL_PASSWORD", "replacement password value")
    updated = main(
        [
            "auth",
            "set-password",
            str(INVOICING),
            "--store",
            str(store),
            "--username",
            "alice",
            "--password-env",
            "TEST_LOCAL_PASSWORD",
        ]
    )
    assert updated == 0
    assert "Updated the password" in capsys.readouterr().out


def test_auth_create_user_rejects_unknown_application_role(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TEST_LOCAL_PASSWORD", "correct horse battery staple")

    result = main(
        [
            "auth",
            "create-user",
            str(INVOICING),
            "--store",
            str(tmp_path / "local-auth.sqlite3"),
            "--username",
            "alice",
            "--role",
            "not-a-role",
            "--password-env",
            "TEST_LOCAL_PASSWORD",
        ]
    )

    assert result == 1
    assert "unknown application role(s): not-a-role" in capsys.readouterr().err


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
        def __init__(self, client, session) -> None:
            captured["reports"] = (client, session)

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


def test_db_check_validates_managed_database_without_echoing_url(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "acceptance-SUPERSECRET.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(model, url)
    repository.create_schema()
    SQLAlchemyCursorStore(repository.engine, mode="managed").create_schema()
    SQLAlchemyActionExecutionStore(
        repository.engine,
        mode="managed",
    ).create_schema()
    SQLAlchemySessionStore(repository.engine, mode="managed").create_schema()
    repository.dispose()
    monkeypatch.setenv("CHECK_DATABASE_URL", url)

    result = main(
        [
            "db",
            "check",
            str(INVOICING),
            "--database-env",
            "CHECK_DATABASE_URL",
        ]
    )

    output = capsys.readouterr()
    assert result == 0
    assert output.err == ""
    assert output.out == (
        "Database check passed: TIDE Invoicing 0.1.0; dialect=sqlite; "
        "mode=managed; framework_state=durable.\n"
    )
    assert url not in output.out
    assert "SUPERSECRET" not in output.out


def test_db_check_requires_configured_environment_variable(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("MISSING_CHECK_DATABASE_URL", raising=False)

    result = main(
        [
            "db",
            "check",
            str(INVOICING),
            "--database-env",
            "MISSING_CHECK_DATABASE_URL",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "Read-only check database startup failed: environment variable "
        "'MISSING_CHECK_DATABASE_URL' is not set\n"
    )


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
        SQLAlchemySessionStore(repository.engine, mode="managed").create_schema()
        repository.dispose()
        monkeypatch.setenv("SEED_DATABASE_URL", url)

        result = main(
            [
                "db",
                "seed",
                str(INVOICING),
                "--database-env",
                "SEED_DATABASE_URL",
                "--role",
                "sales_clerk",
                "--count",
                "customers=4",
                "--count",
                "products=3",
                "--count",
                "invoices=6",
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
            "--role",
            "sales_clerk",
        ]
    )
    assert repeated == 1
    assert "database is not empty" in capsys.readouterr().err


def test_db_seed_requires_an_explicit_application_role(capsys) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "db",
                "seed",
                str(INVOICING),
                "--database-env",
                "SEED_DATABASE_URL",
            ]
        )

    assert stopped.value.code == 2
    assert "--role" in capsys.readouterr().err


def test_seed_counts_name_nothing_the_framework_knows_about() -> None:
    """`--customers`, `--products` and `--invoices` were the sample app's.

    A framework CLI that only knows how to count invoices is no use to an
    application counting inspections, and the 2026-07-16 decision had already
    put fake-data profiles in application-owned providers. The names are the
    provider's business now, so nothing here rejects one it does not
    recognise -- only shapes it cannot read.
    """

    assert _seed_counts([]) == {}
    assert _seed_counts(["inspections=3", "readings=12"]) == {
        "inspections": 3,
        "readings": 12,
    }
    assert _seed_counts(["anything-at-all=0"]) == {"anything-at-all": 0}


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (["customers"], "not NAME=NUMBER"),
        (["=4"], "not NAME=NUMBER"),
        (["customers=lots"], "is not a number"),
        (["customers=-1"], "must not be negative"),
        # Silently taking the last of two spellings is how a typo becomes a
        # database with the wrong number of rows in it.
        (["customers=4", "customers=5"], "more than once"),
    ],
)
def test_a_seed_count_that_cannot_be_read_is_refused(
    values: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _seed_counts(values)


def _write_generation_plan(tmp_path: Path) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "operation": "create_application",
                        "application_id": "generated-cli-app",
                        "name": "Generated CLI App",
                    },
                    {
                        "operation": "define_entity",
                        "entity": "core.Item",
                        "display": "{name}",
                        "fields": [
                            {
                                "name": "id",
                                "type": "integer",
                                "primary_key": True,
                            },
                            {
                                "name": "name",
                                "type": "string",
                                "required": True,
                            },
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_designer_cli_fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "designer-application"
    (project / "models").mkdir(parents=True)
    (project / "views").mkdir()
    (project / "security").mkdir()
    (project / "tide.yaml").write_text(
        """schema_version: "0.1"
application:
  name: Designer CLI Fixture
  version: 0.1.0
model:
  paths: [models]
views:
  paths: [views]
security:
  paths: [security]
""",
        encoding="utf-8",
    )
    (project / "models" / "item.yaml").write_text(
        """entity: core.Item
label: "Items"
display: "{name}"
expose:
  tui: true
permissions:
  list: core.item.read
  read: core.item.read
fields:
  id:
    type: integer
    primary_key: true
  name:
    type: string
    required: true
""",
        encoding="utf-8",
    )
    (project / "views" / "item-browse.yaml").write_text(
        """view: core.item.browse
entity: core.Item
kind: browse
columns: [id, name]
""",
        encoding="utf-8",
    )
    (project / "security" / "policies.yaml").write_text(
        """permissions:
  - core.item.read
roles:
  reader:
    grants: [core.item.read]
""",
        encoding="utf-8",
    )
    changes = tmp_path / "designer-changes.json"
    changes.write_text(
        json.dumps(
            {
                "label": "Rename the entity",
                "commands": [
                    {
                        "operation": "set_value",
                        "target": {"kind": "entity", "name": "core.Item"},
                        "path": ["label"],
                        "value": "Stock items",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project, changes


def _interrupt_designer_cli_save(
    project: Path,
    changes: Path,
    monkeypatch,
) -> None:
    batch = DesignerCommandBatch.model_validate_json(
        changes.read_text(encoding="utf-8")
    )
    session = DesignerService(project).open_session()
    session.execute_batch(batch)
    service = DesignerSaveService()
    preparation = service.prepare(session)

    def interrupt(name: str, _stage: Path) -> None:
        if name == "after_install:models/item.yaml":
            raise _SimulatedCliProcessLoss()

    with monkeypatch.context() as patch:
        patch.setattr(designer_save_module, "_save_checkpoint", interrupt)
        try:
            service.save(
                session,
                DesignerSaveApproval.from_preparation(preparation),
            )
        except _SimulatedCliProcessLoss:
            pass
        else:  # pragma: no cover - defensive test helper
            raise AssertionError("Designer save interruption was not reached")
