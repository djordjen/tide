from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from tide import compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.compiler.normalized import immutable_mapping
from tide.cli import main
from tide.data import InMemoryRepository
from tide.runtime import Principal
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-development-token-that-is-long-enough"


def test_server_requires_bearer_auth_and_exposes_docs() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            live = await client.get("/health/live")
            docs = await client.get("/docs")
            missing = await client.get("/api/v1/invoices")
            session = await client.get(
                "/api/v1/_tide/session",
                headers=_authorization(),
            )
            incorrect = await client.get(
                "/api/v1/invoices",
                headers={"Authorization": "Bearer incorrect-token-value"},
            )

        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        assert docs.status_code == 200
        assert session.status_code == 200
        assert session.json()["reports"] == ["sales.invoice"]
        invoice_capabilities = session.json()["entities"]["sales.Invoice"]
        assert invoice_capabilities["operations"] == [
            "list",
            "get",
            "create",
            "update",
        ]
        assert set(invoice_capabilities["readable_fields"]) == {
            "id",
            "number",
            "invoice_date",
            "customer",
            "currency",
            "status",
            "lines",
            "posted_at",
            "version",
            "total",
        }
        assert set(invoice_capabilities["writable_fields"]) == {
            "invoice_date",
            "customer",
            "currency",
            "lines",
        }
        assert invoice_capabilities["actions"] == ["post"]
        for response in (missing, incorrect):
            assert response.status_code == 401
            assert response.json() == {
                "code": "unauthorized",
                "message": "authentication required",
            }
            assert response.headers["www-authenticate"] == "Bearer"
        assert missing.headers["cache-control"] == "no-store"
        assert missing.headers["x-content-type-options"] == "nosniff"

    asyncio.run(exercise())

    schema = app.openapi()
    assert schema["x-tide"] == {
        "runtime": True,
        "read_only": False,
        "wire_version": "0.1",
        "schema_version": "0.1",
        "authentication": "development-bearer",
    }
    assert schema["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "description": (
            "Bearer credentials are mapped to a Principal by server configuration; "
            "clients cannot choose their roles or permissions."
        ),
        "scheme": "bearer",
        "bearerFormat": "opaque",
    }
    assert set(schema["paths"]["/api/v1/invoices"]) == {"get", "post"}
    assert "/api/v1/invoices/{id}" in schema["paths"]
    assert set(schema["paths"]["/api/v1/invoices/{id}"]) == {"get", "patch"}
    assert set(schema["paths"]["/api/v1/invoices/_query"]) == {"post"}
    assert set(schema["paths"]["/api/v1/_tide/reference-selection"]) == {
        "post"
    }
    assert set(
        schema["paths"][
            "/api/v1/_tide/reports/{report_name}/records/{identity}"
        ]
    ) == {"get"}
    assert "/api/v1/invoices/{id}/actions/post" in schema["paths"]
    create_schema = schema["components"]["schemas"]["SalesInvoiceCreateInput"]
    update_schema = schema["components"]["schemas"]["SalesInvoiceUpdateInput"]
    nested_schema = schema["components"]["schemas"]["SalesInvoiceLineNestedInput"]
    assert set(create_schema["properties"]) == {
        "invoice_date",
        "currency",
        "customer",
        "lines",
    }
    assert create_schema["required"] == ["customer"]
    assert "required" not in update_schema
    assert set(nested_schema["required"]) == {
        "line_number",
        "description",
        "quantity",
        "unit_price",
        "product",
    }
    assert "invoice" not in nested_schema["properties"]
    action_parameters = schema["paths"][
        "/api/v1/invoices/{id}/actions/post"
    ]["post"]["parameters"]
    assert {parameter["name"] for parameter in action_parameters} == {
        "id",
        "If-Match",
        "Idempotency-Key",
    }


def test_server_lists_gets_and_pages_secured_records() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            first = await client.get(
                "/api/v1/invoices?limit=3",
                headers=_authorization(),
            )
            assert first.status_code == 200
            body = first.json()
            assert len(body["records"]) == 3
            assert body["records"][0]["number"] == "INV-2026-0001"
            assert body["records"][0]["invoice_date"] == "2026-07-01"
            assert body["records"][0]["total"] == "850.00"
            assert body["records"][0]["lines"][0]["quantity"] == "10"
            assert body["next_cursor"]

            second = await client.get(
                "/api/v1/invoices",
                params={"limit": 3, "cursor": body["next_cursor"]},
                headers=_authorization(),
            )
            record = await client.get(
                "/api/v1/invoices/1",
                headers=_authorization(),
            )
            missing = await client.get(
                "/api/v1/invoices/999",
                headers=_authorization(),
            )
            invalid = await client.get(
                "/api/v1/invoices/not-an-integer",
                headers=_authorization(),
            )
            invalid_limit = await client.get(
                "/api/v1/invoices?limit=0",
                headers=_authorization(),
            )

        assert second.status_code == 200
        assert second.json()["records"][0]["number"] == "INV-2026-0004"
        assert record.status_code == 200
        assert record.json()["customer"] == 1
        assert record.headers["etag"] == '"2"'
        assert missing.status_code == 404
        assert missing.json()["code"] == "not_found"
        assert invalid.status_code == 422
        assert invalid.json() == {
            "code": "invalid_request",
            "message": "request validation failed",
        }
        assert invalid_limit.status_code == 422
        assert invalid_limit.json() == {
            "code": "invalid_request",
            "message": "request validation failed",
        }

    asyncio.run(exercise())


def test_server_creates_and_patches_through_records_service() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            created_product = await client.post(
                "/api/v1/products",
                headers=_authorization(),
                json={
                    "code": "API-PRODUCT",
                    "name": "API product",
                    "unit_price": "19.95",
                    "active": True,
                },
            )
            rejected_system_field = await client.post(
                "/api/v1/products",
                headers=_authorization(),
                json={
                    "id": 500,
                    "code": "INVALID",
                    "name": "Invalid",
                    "unit_price": "10.00",
                },
            )
            created_invoice = await client.post(
                "/api/v1/invoices",
                headers=_authorization(),
                json={
                    "invoice_date": "2026-07-16",
                    "currency": "EUR",
                    "customer": 1,
                    "lines": [
                        {
                            "line_number": 1,
                            "description": "Created through the API",
                            "quantity": "2.000",
                            "unit_price": "85.00",
                            "product": 1,
                        }
                    ],
                },
            )
            missing_precondition = await client.patch(
                "/api/v1/invoices/2",
                headers=_authorization(),
                json={"currency": "USD"},
            )
            updated = await client.patch(
                "/api/v1/invoices/2",
                headers={**_authorization(), "If-Match": '"1"'},
                json={"currency": "USD"},
            )
            stale = await client.patch(
                "/api/v1/invoices/2",
                headers={**_authorization(), "If-Match": '"1"'},
                json={"currency": "GBP"},
            )
            protected_input = await client.patch(
                "/api/v1/invoices/2",
                headers={**_authorization(), "If-Match": '"2"'},
                json={"status": "posted"},
            )

        assert created_product.status_code == 201
        assert created_product.json()["id"] == 4
        assert created_product.json()["unit_price"] == "19.95"
        assert created_product.headers["location"] == "/api/v1/products/4"
        assert rejected_system_field.status_code == 422
        assert created_invoice.status_code == 201
        assert created_invoice.json()["number"] == "INV-2026-000009"
        assert created_invoice.json()["total"] == "170.00"
        assert created_invoice.headers["etag"] == '"1"'
        assert created_invoice.headers["location"] == "/api/v1/invoices/9"
        assert missing_precondition.status_code == 428
        assert missing_precondition.json()["code"] == "precondition_required"
        assert updated.status_code == 200
        assert updated.json()["currency"] == "USD"
        assert updated.json()["version"] == 2
        assert updated.headers["etag"] == '"2"'
        assert stale.status_code == 412
        assert stale.json()["code"] == "stale_version"
        assert protected_input.status_code == 422

    asyncio.run(exercise())


def test_server_posts_with_version_and_idempotency_preconditions() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            missing = await client.post(
                "/api/v1/invoices/2/actions/post",
                headers={**_authorization(), "If-Match": '"1"'},
                json={},
            )
            posted = await client.post(
                "/api/v1/invoices/2/actions/post",
                headers={
                    **_authorization(),
                    "If-Match": '"1"',
                    "Idempotency-Key": "test-post-invoice-2",
                },
                json={},
            )
            replay = await client.post(
                "/api/v1/invoices/2/actions/post",
                headers={
                    **_authorization(),
                    "If-Match": '"1"',
                    "Idempotency-Key": "test-post-invoice-2",
                },
                json={},
            )
            stale = await client.post(
                "/api/v1/invoices/8/actions/post",
                headers={
                    **_authorization(),
                    "If-Match": '"99"',
                    "Idempotency-Key": "test-stale-post",
                },
                json={},
            )

        assert missing.status_code == 428
        assert missing.json()["message"] == "Idempotency-Key header is required"
        assert posted.status_code == 200
        assert posted.json()["status"] == "posted"
        assert posted.json()["version"] == 2
        assert posted.headers["etag"] == '"2"'
        assert replay.status_code == 200
        assert replay.json()["version"] == 2
        assert stale.status_code == 412
        assert stale.json()["code"] == "stale_version"

    asyncio.run(exercise())


def test_server_builds_only_authorized_renderer_neutral_reports() -> None:
    allowed_app = _app("sales_clerk")
    denied_app = _app("summary_viewer")

    async def exercise() -> None:
        async with _client(allowed_app) as client:
            generated = await client.get(
                "/api/v1/_tide/reports/sales.invoice/records/1",
                headers=_authorization(),
            )
            unknown = await client.get(
                "/api/v1/_tide/reports/missing.report/records/1",
                headers=_authorization(),
            )
        async with _client(denied_app) as client:
            session = await client.get(
                "/api/v1/_tide/session",
                headers=_authorization(),
            )
            denied = await client.get(
                "/api/v1/_tide/reports/sales.invoice/records/1",
                headers=_authorization(),
            )

        assert generated.status_code == 200
        document = generated.json()
        assert document["wire_version"] == "0.1"
        assert document["report"] == "sales.invoice"
        assert document["application"] == "TIDE Invoicing"
        assert document["suggested_filename"] == "invoice-INV-2026-0001"
        assert document["detail"]["rows"][0][-1] == {
            "text": "850.00",
            "alignment": "right",
        }
        assert generated.headers["cache-control"] == "no-store"
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "not_found"
        assert session.json()["reports"] == []
        assert denied.status_code == 403
        assert denied.json()["code"] == "forbidden"

    asyncio.run(exercise())


def test_report_rest_delivery_is_independently_deny_by_default() -> None:
    model = compile_project(INVOICING)
    report = dict(model.reports["sales.invoice"])
    report["expose"] = {"rest": False, "mcp": False}
    model = replace(
        model,
        reports=immutable_mapping({"sales.invoice": report}),
    )
    app = _app("sales_clerk", model=model)

    async def exercise() -> None:
        async with _client(app) as client:
            session = await client.get(
                "/api/v1/_tide/session",
                headers=_authorization(),
            )
            hidden = await client.get(
                "/api/v1/_tide/reports/sales.invoice/records/1",
                headers=_authorization(),
            )

        assert session.json()["reports"] == []
        assert hidden.status_code == 404
        assert hidden.json()["code"] == "not_found"

    asyncio.run(exercise())


def test_server_preserves_protected_field_metadata_and_permissions() -> None:
    summary_app = _app("summary_viewer")
    denied_app = _app(None)

    async def exercise() -> None:
        async with _client(summary_app) as client:
            protected = await client.get(
                "/api/v1/invoices/1",
                headers=_authorization(),
            )
        async with _client(denied_app) as client:
            denied = await client.get(
                "/api/v1/invoices",
                headers={**_authorization(), "X-Tide-Role": "sales_clerk"},
            )

        assert protected.status_code == 200
        assert protected.json()["lines"] is None
        assert protected.json()["total"] is None
        assert protected.json()["_tide"] == {
            "protected_fields": ["lines", "posted_by", "total"]
        }
        assert denied.status_code == 403
        assert denied.json()["code"] == "forbidden"

    asyncio.run(exercise())


def test_development_authenticator_rejects_short_tokens() -> None:
    try:
        DevelopmentTokenAuthenticator("short", Principal("test"))
    except ValueError as error:
        assert "at least 32" in str(error)
    else:  # pragma: no cover - defensive assertion.
        raise AssertionError("short development token was accepted")


def test_tide_serve_requires_token_without_echoing_secret(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("MISSING_TIDE_API_TOKEN", raising=False)
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--dev-token-env",
            "MISSING_TIDE_API_TOKEN",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: development bearer-token environment variable "
        "'MISSING_TIDE_API_TOKEN' is not set\n"
    )


def test_tide_serve_builds_local_app_with_server_assigned_role(
    monkeypatch,
    capsys,
) -> None:
    launched: dict[str, Any] = {}
    monkeypatch.setenv("TEST_TIDE_API_TOKEN", TOKEN)

    def fake_run(app: Any, **configuration: Any) -> None:
        launched["app"] = app
        launched["configuration"] = configuration

    monkeypatch.setattr(uvicorn, "run", fake_run)

    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--dev-token-env",
            "TEST_TIDE_API_TOKEN",
            "--role",
            "auditor",
            "--port",
            "8123",
        ]
    )

    assert result == 0
    assert launched["configuration"] == {
        "host": "127.0.0.1",
        "port": 8123,
        "log_level": "info",
    }
    runtime = launched["app"].state.tide
    assert runtime.authenticator.authenticate(TOKEN) == Principal(
        "development:api",
        roles=frozenset({"auditor"}),
    )
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert "development auth only" in output


def _app(role: str | None, *, model: Any | None = None) -> Any:
    model = model or compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    principal = Principal(
        "api:test",
        roles=frozenset({role}) if role else frozenset(),
    )
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(TOKEN, principal),
        actions=actions,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _authorization() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}
