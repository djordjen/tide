from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
import uvicorn

import tide.api.server as api_server
from tide import compile_project
from tide.api.auth import OidcJwtAuthenticator
from tide.api.browser_auth import OidcBrowserAuth
from tide.api.local_auth import LocalPasswordAuth, LocalUserStore
from tide.api.config import (
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
)
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.compiler.normalized import deep_thaw, immutable_mapping
from tide.cli import main
from tide.data import InMemoryRepository
from tide.runtime import Channel, Principal, RequestContext
from tide.runtime.application import configure_application_runtime
from tide.reporting import PdfDependencyMissing
from tide.reporting.xlsx import SPREADSHEET_AVAILABLE
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-development-token-that-is-long-enough"


def test_server_requires_bearer_auth_and_withholds_its_description() -> None:
    """The API description is not served unless a deployment asks for it.

    This used to assert an anonymous 200, which pinned the leak in place.
    See tests/test_api_docs_exposure.py for the whole contract.
    """

    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            live = await client.get("/health/live")
            ready = await client.get("/health/ready")
            docs = await client.get("/docs")
            missing = await client.get("/api/v1/invoices")
            session = await client.get(
                "/api/v1/_tide/session",
                headers=_authorization(),
            )
            presentation = await client.get(
                "/api/v1/_tide/presentation",
                headers=_authorization(),
            )
            anonymous_presentation = await client.get(
                "/api/v1/_tide/presentation",
            )
            incorrect = await client.get(
                "/api/v1/invoices",
                headers={"Authorization": "Bearer incorrect-token-value"},
            )

        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "application": "TIDE Invoicing",
            "version": "0.1.0",
        }
        assert docs.status_code == 404
        assert session.status_code == 200
        assert session.json()["authentication"] == "development-bearer"
        assert session.json()["reports"] == ["sales.invoice", "sales.summary"]
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
            "cancelled_at",
            "cancelled_by",
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
        assert invoice_capabilities["actions"] == ["post", "void"]
        assert invoice_capabilities["audit"] is False
        assert presentation.status_code == 200
        manifest = presentation.json()
        assert manifest["wire_version"] == "0.1"
        assert manifest["application"] == "TIDE Invoicing"
        assert manifest["application_version"] == "0.1.0"
        assert manifest["schema_version"] == "0.1"
        assert manifest["principal"] == "api:test"
        assert [
            (
                group["label"],
                [item["view"] for item in group["items"]],
            )
            for group in manifest["navigation"]
        ] == [
            ("Sales", ["sales.Invoice.browse"]),
            (
                "Master Data",
                ["crm.Customer.browse", "catalog.Product.browse"],
            ),
        ]
        assert set(manifest["views"]) == {
            "sales.Invoice.browse",
            "crm.Customer.browse",
            "catalog.Product.browse",
        }
        assert manifest["reports"] == {
            "sales.invoice": {
                "name": "sales.invoice",
                "title": "Invoice",
                "kind": "record",
                "entity": "sales.Invoice",
                "resource_path": "/api/v1/_tide/reports/sales.invoice",
                "export_formats": ["csv", "html", "pdf"],
                # A record report's identity parameter is bound from the URL,
                # so the browser has nothing to ask for.
                "parameters": [],
            },
            "sales.summary": {
                "name": "sales.summary",
                "title": "Posted Sales Summary",
                "kind": "summary",
                "entity": "sales.Invoice",
                "resource_path": "/api/v1/_tide/reports/sales.summary",
                "export_formats": ["csv", "html", "pdf"],
                "parameters": [
                    {
                        "name": "from_date",
                        "label": "From Date",
                        "type": "date",
                        "required": False,
                    },
                    {
                        "name": "to_date",
                        "label": "To Date",
                        "type": "date",
                        "required": False,
                    },
                ],
            },
        }
        assert set(manifest["forms"]) == {
            "sales.Invoice.edit",
            "crm.Customer.edit",
            "catalog.Product.edit",
        }
        invoice_view = manifest["views"]["sales.Invoice.browse"]
        assert invoice_view["resource_path"] == "/api/v1/invoices"
        assert invoice_view["query_path"] == "/api/v1/invoices/_query"
        assert invoice_view["identity_field"] == "id"
        assert invoice_view["search_field"] == "number"
        assert invoice_view["search_label"] == "Number"
        assert invoice_view["page_size"] == 25
        assert invoice_view["operations"] == [
            "list",
            "get",
            "create",
            "update",
        ]
        assert invoice_view["detail_view"] == "sales.Invoice.edit"
        assert [column["name"] for column in invoice_view["columns"]] == [
            "number",
            "invoice_date",
            "customer",
            "status",
            "total",
        ]
        assert invoice_view["summaries"] == [
            {"field": "number", "function": "count"},
            {"field": "total", "function": "sum"},
        ]
        # Every shown stored column can carry a value filter, references
        # included -- they filter by identity and enumerate with names.
        assert invoice_view["filterable_fields"] == [
            "number",
            "invoice_date",
            "customer",
            "status",
            "total",
        ]
        # The browse edit mode travels with the view: invoices keep the
        # form, the flat product catalogue offers editing in the row.
        assert invoice_view["edit"] == "form"
        assert manifest["views"]["catalog.Product.browse"]["edit"] == "inline"
        assert invoice_view["columns"][1]["format_options"] == {
            "decimal_places": None,
            "thousands_separator": False,
            "display": "%d.%m.%Y",
        }
        assert invoice_view["columns"][2]["target_entity"] == "crm.Customer"
        assert invoice_view["columns"][2]["reference"] == {
            "entity": "crm.Customer",
            "resource_path": "/api/v1/customers",
            "identity_field": "id",
            "display_template": "{code} - {name}",
        }
        assert invoice_view["columns"][4] == {
            "name": "total",
            "label": "Total",
            "field_type": "decimal",
            "alignment": "right",
            "format": "money",
            "format_options": {
                "decimal_places": 2,
                "thousands_separator": True,
                "display": None,
            },
            "target_entity": None,
            "reference": None,
            # A column carries its declared codes so a browse grid can show
            # what one stands for; a total has none.
            "values": [],
        }
        assert invoice_view["named_filters"] == [
            {
                "name": "drafts",
                "label": "Draft invoices",
                "conditions": [
                    {
                        "field": "status",
                        "operator": "eq",
                        "value": "draft",
                    }
                ],
            },
            {
                "name": "high_value",
                "label": "High-value invoices",
                "conditions": [
                    {
                        "field": "total",
                        "operator": "gte",
                        "value": 10000,
                    }
                ],
            },
        ]
        invoice_form = manifest["forms"]["sales.Invoice.edit"]
        assert invoice_form["entity"] == "sales.Invoice"
        assert invoice_form["label"] == "Invoice"
        assert invoice_form["display_template"] == "number"
        assert invoice_form["actions"] == [
            {
                "name": "post",
                "label": "Post",
                "idempotent": True,
            }
        ]
        assert list(invoice_form["fields"]) == [
            "number",
            "invoice_date",
            "status",
            "currency",
            "customer",
            "total",
            "posted_at",
            "version",
        ]
        assert invoice_form["sections"][0] == {
            "kind": "group",
            "label": "Invoice",
            "rows": [
                ["number", "invoice_date"],
                ["status", "currency"],
                ["customer", "total"],
                # `posted_by` is a field this principal cannot read, so the
                # row it shared with `posted_at` used to arrive half empty and
                # `version` came alone behind it. Ordering the authored row
                # `[posted_at, version, posted_by]` pairs the two fields that
                # are visible and leaves the invisible one to be dropped whole.
                ["posted_at", "version"],
            ],
            "tab": None,
        }
        line_section = invoice_form["sections"][1]
        assert line_section["kind"] == "collection"
        assert line_section["name"] == "lines"
        assert line_section["label"] == "Lines"
        # the form calls the collection "Lines", so one row is a "Line" --
        # not an "Invoice Line", which is what the entity label would give
        assert line_section["record_label"] == "Line"
        assert line_section["sequence_field"] == "line_number"
        assert line_section["entity"] == "sales.InvoiceLine"
        assert [column["name"] for column in line_section["columns"]] == [
            "line_number",
            "product",
            "description",
            "quantity",
            "unit_price",
            "total",
        ]
        assert {
            "view": line_section["view"],
            "identity_field": line_section["identity_field"],
            "actions": line_section["actions"],
            "draft_operations": line_section["draft_operations"],
            "writable": line_section["writable"],
        } == {
            "view": "sales.InvoiceLine.inline_edit",
            "identity_field": "id",
            "actions": ["add", "apply", "remove"],
            "draft_operations": ["create", "update"],
            "writable": True,
        }
        assert list(line_section["fields"]) == [
            "line_number",
            "unit_price",
            "product",
            "quantity",
            "description",
        ]
        assert line_section["groups"] == [
            {
                "kind": "group",
                "label": "Line details",
                "rows": [
                    ["line_number", "unit_price"],
                    ["product", "quantity"],
                    ["description"],
                ],
                "tab": None,
            }
        ]
        product_lookup = line_section["fields"]["product"]["lookup"]
        assert {
            "view": product_lookup["view"],
            "owner_entity": product_lookup["owner_entity"],
            "field": product_lookup["field"],
            "target_entity": product_lookup["target_entity"],
            "search_fields": product_lookup["search_fields"],
            "create_view": product_lookup["create_view"],
        } == {
            "view": "catalog.Product.lookup",
            "owner_entity": "sales.InvoiceLine",
            "field": "product",
            "target_entity": "catalog.Product",
            "search_fields": ["code", "name"],
            "create_view": "catalog.Product.edit",
        }
        customer_lookup = invoice_form["fields"]["customer"]["lookup"]
        assert customer_lookup == {
            "view": "crm.Customer.lookup",
            "title": "Select Customer",
            "owner_entity": "sales.Invoice",
            "field": "customer",
            "target_entity": "crm.Customer",
            "resource_path": "/api/v1/customers",
            "query_path": "/api/v1/customers/_query",
            "selection_path": "/api/v1/_tide/reference-selection",
            "identity_field": "id",
            "columns": [
                {
                    "name": "code",
                    "label": "Code",
                    "field_type": "string",
                    "alignment": "left",
                    "format": None,
                    "format_options": None,
                    "target_entity": None,
                    "reference": None,
                    "values": [],
                },
                {
                    "name": "name",
                    "label": "Name",
                    "field_type": "string",
                    "alignment": "left",
                    "format": None,
                    "format_options": None,
                    "target_entity": None,
                    "reference": None,
                    "values": [],
                },
                {
                    "name": "email",
                    "label": "Email",
                    "field_type": "string",
                    "alignment": "left",
                    "format": None,
                    "format_options": None,
                    "target_entity": None,
                    "reference": None,
                    "values": [],
                },
            ],
            "search_fields": ["code", "name", "email"],
            "page_size": 20,
            "operations": ["list", "get", "create", "update", "delete"],
            "create_view": "crm.Customer.edit",
        }
        product_fields = manifest["forms"]["catalog.Product.edit"]["fields"]
        assert {
            "writable": product_fields["code"]["writable"],
            "required": product_fields["code"]["required"],
            "max_length": product_fields["code"]["max_length"],
            "regex": product_fields["code"]["regex"],
        } == {
            "writable": True,
            "required": True,
            "max_length": 30,
            "regex": "[A-Z][A-Z0-9-]{0,29}",
        }
        assert {
            "required": product_fields["unit_price"]["required"],
            "numeric_mask": product_fields["unit_price"]["numeric_mask"],
            "precision": product_fields["unit_price"]["precision"],
            "scale": product_fields["unit_price"]["scale"],
            "minimum": product_fields["unit_price"]["minimum"],
        } == {
            "required": True,
            "numeric_mask": "0.00",
            "precision": 12,
            "scale": 2,
            "minimum": "0",
        }
        assert product_fields["active"]["has_default"] is True
        assert product_fields["active"]["default_value"] is True
        assert presentation.headers["cache-control"] == "no-store"
        assert anonymous_presentation.status_code == 401
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
        "browser_authentication": False,
        "max_request_body_bytes": DEFAULT_MAX_REQUEST_BODY_BYTES,
        "request_body_timeout_seconds": DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
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
    assert set(schema["paths"]["/health/ready"]["get"]["responses"]) == {
        "200",
        "503",
    }
    assert set(schema["paths"]["/api/v1/invoices"]) == {"get", "post"}
    assert "/api/v1/invoices/{id}" in schema["paths"]
    assert "/api/v1/invoices/{id}/_audit" in schema["paths"]
    assert "/api/v1/customers/{id}/_audit" in schema["paths"]
    assert "/api/v1/products/{id}/_audit" in schema["paths"]
    assert set(schema["paths"]["/api/v1/invoices/{id}"]) == {"get", "patch"}
    assert set(schema["paths"]["/api/v1/products/{id}"]) == {
        "get",
        "patch",
        "delete",
    }
    assert "delete" in schema["paths"]["/api/v1/customers/{id}"]
    assert "delete" in schema["paths"]["/api/v1/customers/{id}"]
    delete_operation = schema["paths"]["/api/v1/products/{id}"]["delete"]
    assert set(delete_operation["responses"]) == {
        "204",
        "400",
        "401",
        "403",
        "404",
        "409",
        "412",
        "422",
        "428",
    }
    assert set(schema["paths"]["/api/v1/invoices/_query"]) == {"post"}
    assert set(schema["paths"]["/api/v1/_tide/reference-selection"]) == {
        "post"
    }
    assert set(schema["paths"]["/api/v1/_tide/presentation"]) == {"get"}
    assert set(
        schema["paths"][
            "/api/v1/_tide/reports/{report_name}/records/{identity}"
        ]
    ) == {"get"}
    assert set(schema["paths"]["/api/v1/_tide/reports/{report_name}"]) == {
        "post"
    }
    assert set(
        schema["paths"][
            "/api/v1/_tide/reports/{report_name}/exports/{export_format}"
        ]
    ) == {"post"}
    assert set(
        schema["paths"][
            (
                "/api/v1/_tide/reports/{report_name}/records/"
                "{identity}/exports/{export_format}"
            )
        ]
    ) == {"get"}
    assert "/api/v1/invoices/{id}/actions/post" in schema["paths"]
    assert "413" in schema["paths"]["/api/v1/invoices"]["post"]["responses"]
    assert "408" in schema["paths"]["/api/v1/invoices"]["post"]["responses"]
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


def test_record_get_projects_server_evaluated_workflow_field_state() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            posted = await client.get(
                "/api/v1/invoices/1",
                headers=_authorization(),
            )
            draft = await client.get(
                "/api/v1/invoices/2",
                headers=_authorization(),
            )

        assert posted.status_code == 200
        assert (
            (posted.json().get("_tide") or {}).get("writable_fields")
            is None
        )
        assert posted.json()["_tide"]["actions"] == {
            "post": {"visible": True, "enabled": False},
            "void": {"visible": True, "enabled": False},
        }
        assert draft.status_code == 200
        assert draft.json()["_tide"] == {
            "protected_fields": ["posted_by"],
            "writable_fields": [
                "currency",
                "customer",
                "invoice_date",
                "lines",
            ],
            "actions": {
                # A draft can go either way, which is the point of declaring
                # both transitions out of it.
                "post": {"visible": True, "enabled": True},
                "void": {"visible": True, "enabled": True},
            },
            "references": {"customer": "MORA - Mora Trade"},
        }

    asyncio.run(exercise())


def test_server_can_host_a_built_web_renderer_without_shadowing_api(
    tmp_path: Path,
) -> None:
    web_root = tmp_path / "web"
    assets = web_root / "assets"
    assets.mkdir(parents=True)
    (web_root / "index.html").write_text(
        "<!doctype html><title>TIDE Web</title>",
        encoding="utf-8",
    )
    (assets / "app-hash.js").write_text(
        "console.log('tide')",
        encoding="utf-8",
    )
    app = _app("sales_clerk", web_root=web_root)

    async def exercise() -> None:
        async with _client(app) as client:
            index = await client.get("/")
            asset = await client.get("/assets/app-hash.js")
            session = await client.get(
                "/api/v1/_tide/session",
                headers=_authorization(),
            )

        assert index.status_code == 200
        assert "TIDE Web" in index.text
        assert index.headers["cache-control"] == "no-store"
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == (
            "public, max-age=31536000, immutable"
        )
        assert session.status_code == 200
        assert session.json()["application"] == "TIDE Invoicing"

    asyncio.run(exercise())


def test_server_rejects_incomplete_web_build(tmp_path: Path) -> None:
    model = compile_project(INVOICING)
    records = RecordsService(model, InMemoryRepository())

    with pytest.raises(ValueError, match="has no index.html"):
        build_fastapi_app(
            model,
            records,
            DevelopmentTokenAuthenticator(
                TOKEN,
                Principal("api:test", roles=frozenset({"sales_clerk"})),
            ),
            web_root=tmp_path,
        )


def test_browser_oidc_session_authenticates_and_requires_csrf() -> None:
    class TestBrowserAuth:
        authentication_mode = "oidc"
        secure_cookie = False
        transaction_cookie_name = "tide_oidc_transaction"
        session_cookie_name = "tide_session"
        transaction_lifetime_seconds = 300
        session_lifetime_seconds = 3600

        def __init__(self) -> None:
            self.ended: list[str | None] = []

        def begin_login(self, *, return_to: str = "/") -> Any:
            assert return_to == "/invoices"
            return SimpleNamespace(
                authorization_url="https://identity.example.test/authorize",
                transaction_binding="browser-binding",
            )

        def complete_login(
            self,
            *,
            state: str,
            transaction_binding: str,
            code: str,
        ) -> Any:
            assert (state, transaction_binding, code) == (
                "provider-state",
                "browser-binding",
                "provider-code",
            )
            return SimpleNamespace(
                session_id="opaque-browser-session",
                return_to="/invoices",
            )

        def authenticate_session(self, session_id: str | None) -> Any:
            if session_id != "opaque-browser-session":
                return None
            return SimpleNamespace(
                principal=Principal(
                    "oidc:browser-user",
                    roles=frozenset({"sales_clerk"}),
                ),
                csrf_token="browser-csrf-token-that-is-long-enough",
            )

        def end_session(self, session_id: str | None) -> None:
            self.ended.append(session_id)

    browser_auth = TestBrowserAuth()
    app = _app("sales_clerk", browser_auth=browser_auth)

    async def exercise() -> None:
        async with _client(app) as client:
            discovery = await client.get("/api/v1/_tide/browser-auth")
            login = await client.get(
                "/api/v1/_tide/browser-auth/login",
                params={"return_to": "/invoices"},
            )
            assert login.status_code == 302
            assert login.headers["location"] == (
                "https://identity.example.test/authorize"
            )
            assert "HttpOnly" in login.headers["set-cookie"]

            callback = await client.get(
                "/api/v1/_tide/browser-auth/callback",
                params={"state": "provider-state", "code": "provider-code"},
            )
            assert callback.status_code == 303
            assert callback.headers["location"] == "/invoices"
            assert "HttpOnly" in callback.headers["set-cookie"]
            assert "SameSite=strict" in callback.headers["set-cookie"]

            browser_session = await client.get(
                "/api/v1/_tide/browser-auth/session"
            )
            app_session = await client.get("/api/v1/_tide/session")
            invoices = await client.get("/api/v1/invoices")
            confused = await client.get(
                "/api/v1/invoices",
                headers={"Authorization": "Bearer invalid-token"},
            )
            missing_mutation_csrf = await client.post(
                "/api/v1/products",
                json={
                    "code": "BROWSER-1",
                    "name": "Browser product",
                    "unit_price": "10.00",
                    "active": True,
                },
            )
            created = await client.post(
                "/api/v1/products",
                json={
                    "code": "BROWSER-1",
                    "name": "Browser product",
                    "unit_price": "10.00",
                    "active": True,
                },
                headers={
                    "X-TIDE-CSRF": "browser-csrf-token-that-is-long-enough"
                },
            )
            missing_csrf = await client.post(
                "/api/v1/_tide/browser-auth/logout"
            )
            logout = await client.post(
                "/api/v1/_tide/browser-auth/logout",
                headers={
                    "X-TIDE-CSRF": "browser-csrf-token-that-is-long-enough"
                },
            )

        assert discovery.json() == {
            "enabled": True,
            "mode": "oidc",
            "login_path": "/api/v1/_tide/browser-auth/login",
            "session_path": "/api/v1/_tide/browser-auth/session",
            "logout_path": "/api/v1/_tide/browser-auth/logout",
        }
        assert browser_session.status_code == 200
        assert browser_session.json() == {
            "csrf_token": "browser-csrf-token-that-is-long-enough"
        }
        assert app_session.status_code == 200
        assert app_session.json()["principal"] == "oidc:browser-user"
        assert invoices.status_code == 200
        assert invoices.headers["referrer-policy"] == "no-referrer"
        assert invoices.headers["x-frame-options"] == "DENY"
        assert confused.status_code == 401
        assert missing_mutation_csrf.status_code == 403
        assert created.status_code == 201
        assert created.json()["code"] == "BROWSER-1"
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["code"] == "csrf_failed"
        assert logout.status_code == 204
        assert browser_auth.ended == ["opaque-browser-session"]

    asyncio.run(exercise())
    assert app.openapi()["x-tide"]["browser_authentication"] is True


def test_local_password_browser_login_uses_server_owned_roles_and_csrf(
    tmp_path: Path,
) -> None:
    store = LocalUserStore(
        tmp_path / "local-auth.sqlite3",
        application="TIDE Invoicing",
        password_iterations=1_000,
    )
    store.initialize()
    store.create_user(
        "alice",
        "correct horse battery staple",
        roles=("sales_clerk",),
    )
    authentication = LocalPasswordAuth(
        store,
        allowed_roles=("sales_clerk", "auditor"),
        secure_cookie=False,
    )
    app = _app(
        "sales_clerk",
        authenticator=authentication,
        browser_auth=authentication,
    )

    async def exercise() -> None:
        async with _client(app) as client:
            discovery = await client.get("/api/v1/_tide/browser-auth")
            missing_proof = await client.post(
                "/api/v1/_tide/browser-auth/login",
                json={
                    "username": "alice",
                    "password": "correct horse battery staple",
                },
            )
            incorrect = await client.post(
                "/api/v1/_tide/browser-auth/login",
                json={"username": "alice", "password": "not the password"},
                headers={"X-TIDE-LOGIN": "password"},
            )
            login = await client.post(
                "/api/v1/_tide/browser-auth/login",
                json={
                    "username": "alice",
                    "password": "correct horse battery staple",
                },
                headers={"X-TIDE-LOGIN": "password"},
            )
            csrf_token = login.json()["csrf_token"]
            session = await client.get("/api/v1/_tide/session")
            denied = await client.post(
                "/api/v1/products",
                json={
                    "code": "LOCAL-1",
                    "name": "Local product",
                    "unit_price": "12.00",
                    "active": True,
                },
            )
            created = await client.post(
                "/api/v1/products",
                json={
                    "code": "LOCAL-1",
                    "name": "Local product",
                    "unit_price": "12.00",
                    "active": True,
                },
                headers={"X-TIDE-CSRF": csrf_token},
            )
            logout = await client.post(
                "/api/v1/_tide/browser-auth/logout",
                headers={"X-TIDE-CSRF": csrf_token},
            )
            ended = await client.get("/api/v1/_tide/session")

        assert discovery.json() == {
            "enabled": True,
            "mode": "password",
            "login_path": "/api/v1/_tide/browser-auth/login",
            "session_path": "/api/v1/_tide/browser-auth/session",
            "logout_path": "/api/v1/_tide/browser-auth/logout",
        }
        assert missing_proof.status_code == 400
        assert incorrect.status_code == 401
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]
        assert session.status_code == 200
        assert session.json()["authentication"] == "local-password"
        assert session.json()["principal"] == "local:alice"
        assert session.json()["roles"] == ["sales_clerk"]
        assert denied.status_code == 403
        assert created.status_code == 201
        assert logout.status_code == 204
        assert ended.status_code == 401

    asyncio.run(exercise())


def test_real_browser_oidc_adapter_completes_fastapi_acceptance_flow() -> None:
    token_requests: list[dict[str, list[str]]] = []

    class TestOidcAuthenticator:
        authentication_type = "oidc-jwt"
        production = True

        def authenticate(self, credential: str) -> Principal | None:
            if credential != "accepted-provider-access-token":
                return None
            return Principal(
                "oidc:accepted-user",
                roles=frozenset({"sales_clerk"}),
            )

    def provider(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "https://identity.example.test/token"
        assert request.headers["authorization"].startswith("Basic ")
        form = parse_qs(request.content.decode("ascii"))
        token_requests.append(form)
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": "accepted-provider-access-token",
                "refresh_token": "accepted-provider-refresh-token",
                "expires_in": 3600,
            },
        )

    authenticator = TestOidcAuthenticator()
    with httpx.Client(transport=httpx.MockTransport(provider)) as provider_client:
        browser_auth = OidcBrowserAuth(
            authenticator=authenticator,
            authorization_endpoint="https://identity.example.test/authorize",
            token_endpoint="https://identity.example.test/token",
            client_id="tide-web",
            client_secret="provider-client-secret",
            redirect_uri=(
                "http://127.0.0.1:8000/api/v1/_tide/browser-auth/callback"
            ),
            scopes=("openid", "profile", "offline_access"),
            http_client=provider_client,
        )
        app = _app(
            "sales_clerk",
            browser_auth=browser_auth,
            authenticator=authenticator,
        )

        async def exercise() -> None:
            async with _client(app) as client:
                login = await client.get(
                    "/api/v1/_tide/browser-auth/login",
                    params={"return_to": "/?view=sales.Invoice.browse"},
                )
                authorization = urlsplit(login.headers["location"])
                authorization_query = parse_qs(authorization.query)
                callback = await client.get(
                    "/api/v1/_tide/browser-auth/callback",
                    params={
                        "state": authorization_query["state"][0],
                        "code": "accepted-provider-code",
                    },
                )
                browser_session = await client.get(
                    "/api/v1/_tide/browser-auth/session"
                )
                session = await client.get("/api/v1/_tide/session")
                csrf_token = browser_session.json()["csrf_token"]
                missing_csrf = await client.post(
                    "/api/v1/products",
                    json={
                        "code": "OIDC-ACCEPTANCE",
                        "name": "OIDC acceptance product",
                        "unit_price": "12.50",
                        "active": True,
                    },
                )
                created = await client.post(
                    "/api/v1/products",
                    json={
                        "code": "OIDC-ACCEPTANCE",
                        "name": "OIDC acceptance product",
                        "unit_price": "12.50",
                        "active": True,
                    },
                    headers={"X-TIDE-CSRF": csrf_token},
                )
                logout = await client.post(
                    "/api/v1/_tide/browser-auth/logout",
                    headers={"X-TIDE-CSRF": csrf_token},
                )
                after_logout = await client.get("/api/v1/_tide/session")

            assert login.status_code == 302
            assert authorization.hostname == "identity.example.test"
            assert authorization_query["response_type"] == ["code"]
            assert authorization_query["code_challenge_method"] == ["S256"]
            assert callback.status_code == 303
            assert callback.headers["location"] == "/?view=sales.Invoice.browse"
            assert browser_session.status_code == 200
            assert session.status_code == 200
            assert session.json()["principal"] == "oidc:accepted-user"
            assert missing_csrf.status_code == 403
            assert created.status_code == 201
            assert logout.status_code == 204
            assert after_logout.status_code == 401

        asyncio.run(exercise())

    assert len(token_requests) == 1
    assert token_requests[0]["grant_type"] == ["authorization_code"]
    assert token_requests[0]["code"] == ["accepted-provider-code"]
    assert token_requests[0]["code_verifier"]


def test_presentation_manifest_filters_inaccessible_navigation_groups() -> None:
    invoice_only_app = _app("summary_viewer")
    denied_app = _app(None)

    async def exercise() -> None:
        async with _client(invoice_only_app) as client:
            invoice_only = await client.get(
                "/api/v1/_tide/presentation",
                headers=_authorization(),
            )
        async with _client(denied_app) as client:
            denied = await client.get(
                "/api/v1/_tide/presentation",
                headers=_authorization(),
            )

        assert invoice_only.status_code == 200
        manifest = invoice_only.json()
        assert [
            group["label"] for group in manifest["navigation"]
        ] == ["Sales"]
        assert [
            item["view"]
            for item in manifest["navigation"][0]["items"]
        ] == ["sales.Invoice.browse"]
        assert set(manifest["views"]) == {"sales.Invoice.browse"}
        assert manifest["views"]["sales.Invoice.browse"]["operations"] == [
            "list",
            "get",
        ]
        assert (
            manifest["forms"]["sales.Invoice.edit"]["fields"]["customer"][
                "lookup"
            ]
            is None
        )
        assert not any(
            section["kind"] == "collection"
            for section in manifest["forms"]["sales.Invoice.edit"]["sections"]
        )
        assert manifest["reports"] == {}

        assert denied.status_code == 200
        assert denied.json()["navigation"] == []
        assert denied.json()["views"] == {}
        assert denied.json()["reports"] == {}

    asyncio.run(exercise())


def test_presentation_manifest_filters_field_protected_controls() -> None:
    model = compile_project(INVOICING)
    protected_total = immutable_mapping(
        {
            "entity": "sales.Invoice",
            "field": "total",
            "read": "sales.invoice.audit",
        }
    )
    app = _app(
        "summary_viewer",
        model=replace(
            model,
            field_policies=(*model.field_policies, protected_total),
        ),
    )

    async def exercise() -> None:
        async with _client(app) as client:
            response = await client.get(
                "/api/v1/_tide/presentation",
                headers=_authorization(),
            )

        assert response.status_code == 200
        invoice_view = response.json()["views"]["sales.Invoice.browse"]
        assert "total" not in {
            column["name"] for column in invoice_view["columns"]
        }
        assert "total" not in invoice_view["sortable_fields"]
        assert [
            named_filter["name"]
            for named_filter in invoice_view["named_filters"]
        ] == ["drafts"]
        invoice_form = response.json()["forms"]["sales.Invoice.edit"]
        assert "total" not in invoice_form["fields"]
        assert "total" not in {
            field_name
            for section in invoice_form["sections"]
            if section["kind"] == "group"
            for row in section["rows"]
            for field_name in row
        }

    asyncio.run(exercise())


def test_manifest_offers_required_parameter_summaries_with_their_metadata() -> None:
    # The old fence hid any summary `{}` could not build, because the browser
    # had no way to ask for values. Now the manifest carries what to ask for,
    # and its `required` flag means "the caller must supply this" -- a declared
    # default satisfies the report service on its own, so that parameter is
    # offered as optional even though the YAML says required.
    model = compile_project(INVOICING)
    report = dict(model.reports["sales.summary"])
    report["parameters"] = {
        "from_date": {"type": "date", "required": True, "default": None},
        "to_date": {"type": "date", "required": True, "default": "2026-12-31"},
    }
    app = _app(
        "sales_clerk",
        model=replace(
            model,
            reports=immutable_mapping(
                {**dict(model.reports), "sales.summary": report}
            ),
        ),
    )

    async def exercise() -> None:
        async with _client(app) as client:
            response = await client.get(
                "/api/v1/_tide/presentation",
                headers=_authorization(),
            )

        assert response.status_code == 200
        summary = response.json()["reports"]["sales.summary"]
        assert summary["parameters"] == [
            {
                "name": "from_date",
                "label": "From Date",
                "type": "date",
                "required": True,
            },
            {
                "name": "to_date",
                "label": "To Date",
                "type": "date",
                "required": False,
            },
        ]

    asyncio.run(exercise())


def test_readiness_fails_closed_without_leaking_dependency_errors() -> None:
    class UnavailableRepository(InMemoryRepository):
        def check_readiness(self) -> None:
            raise RuntimeError("database password=must-not-leak")

    model = compile_project(INVOICING)
    repository = UnavailableRepository()
    assert seed_demo_data(model, repository) == 15
    records = RecordsService(model, repository)
    logger, log_handler = _recording_logger()
    app = build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"sales_clerk"})),
        ),
        logger=logger,
    )

    async def exercise() -> None:
        async with _client(app) as client:
            live = await client.get("/health/live")
            ready = await client.get("/health/ready")

        assert live.status_code == 200
        assert ready.status_code == 503
        assert ready.json() == {
            "status": "not_ready",
            "application": "TIDE Invoicing",
            "version": "0.1.0",
        }
        assert "password" not in ready.text
        assert ready.headers["cache-control"] == "no-store"

    asyncio.run(exercise())
    readiness_failure = next(
        record
        for record in log_handler.records
        if record.msg == "readiness.failed"
    )
    assert readiness_failure.tide_fields == {
        "channel": "system",
        "correlation_id": readiness_failure.tide_fields["correlation_id"],
        "operation": (
            "test_readiness_fails_closed_without_leaking_dependency_errors."
            "<locals>.UnavailableRepository.check_readiness"
        ),
        "error_type": "RuntimeError",
    }
    assert "password" not in repr(readiness_failure.__dict__)


def test_http_correlation_is_returned_logged_and_shared_with_crud_audit() -> None:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    logger, log_handler = _recording_logger()
    app = build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"sales_clerk"})),
        ),
        actions=actions,
        logger=logger,
    )
    correlation_id = "client.create-product:123"

    async def exercise() -> None:
        async with _client(app) as client:
            created = await client.post(
                "/api/v1/products",
                headers={
                    **_authorization(),
                    "X-Correlation-ID": correlation_id,
                },
                json={
                    "code": "LOG-SECRET-CODE",
                    "name": "Correlation test",
                    "unit_price": "1.00",
                    "active": True,
                },
            )
            regenerated = await client.get(
                "/health/live",
                headers={"X-Correlation-ID": "invalid header value"},
            )

        assert created.status_code == 201
        assert created.headers["x-correlation-id"] == correlation_id
        assert regenerated.status_code == 200
        UUID(regenerated.headers["x-correlation-id"])

    asyncio.run(exercise())

    events = actions.execution_store.record_audit_events(
        correlation_id=correlation_id,
    )
    assert len(events) == 1
    assert events[0].entity == "catalog.Product"
    completed = next(
        record
        for record in log_handler.records
        if record.msg == "http.request.completed"
        and record.tide_fields.get("correlation_id") == correlation_id
    )
    assert completed.tide_fields == {
        "channel": "rest",
        "correlation_id": correlation_id,
        "operation": "createCatalogProduct",
        "method": "POST",
        "status_code": 201,
        "duration_ms": completed.tide_fields["duration_ms"],
    }
    assert TOKEN not in repr(completed.__dict__)
    assert "LOG-SECRET-CODE" not in repr(completed.__dict__)


def test_request_body_limit_rejects_declared_and_streamed_payloads_safely() -> None:
    logger, log_handler = _recording_logger()
    app = _app(
        "sales_clerk",
        logger=logger,
        max_request_body_bytes=96,
    )

    async def streamed_body() -> Any:
        yield b'{"code":"STREAMED",'
        yield b'"name":"' + (b"x" * 100) + b'"}'

    async def exercise() -> None:
        async with _client(app) as client:
            declared = await client.post(
                "/api/v1/products",
                headers={
                    **_authorization(),
                    "X-Correlation-ID": "declared-too-large",
                },
                json={"secret": "must-not-leak" * 20},
            )
            streamed = await client.post(
                "/api/v1/products",
                headers={
                    **_authorization(),
                    "Content-Type": "application/json",
                    "X-Correlation-ID": "streamed-too-large",
                },
                content=streamed_body(),
            )

        for response, correlation_id in (
            (declared, "declared-too-large"),
            (streamed, "streamed-too-large"),
        ):
            assert response.status_code == 413
            assert response.json() == {
                "code": "request_too_large",
                "message": "request body exceeds the configured limit",
            }
            assert response.headers["x-correlation-id"] == correlation_id
            assert response.headers["cache-control"] == "no-store"
            assert "must-not-leak" not in response.text

    asyncio.run(exercise())

    rejected = [
        record
        for record in log_handler.records
        if record.msg == "http.request.completed"
        and record.tide_fields.get("status_code") == 413
    ]
    assert len(rejected) == 2
    assert {record.tide_fields["correlation_id"] for record in rejected} == {
        "declared-too-large",
        "streamed-too-large",
    }
    assert "must-not-leak" not in repr([record.__dict__ for record in rejected])


def test_request_body_receive_timeout_is_safe_and_correlated() -> None:
    logger, log_handler = _recording_logger()
    app = _app(
        "sales_clerk",
        logger=logger,
        request_body_timeout_seconds=1,
    )

    async def slow_body() -> Any:
        await asyncio.sleep(1.1)
        yield b'{"code":"TOO-LATE"}'

    async def exercise() -> None:
        async with _client(app) as client:
            response = await client.post(
                "/api/v1/products",
                headers={
                    **_authorization(),
                    "Content-Type": "application/json",
                    "X-Correlation-ID": "slow-request-body",
                },
                content=slow_body(),
            )

        assert response.status_code == 408
        assert response.json() == {
            "code": "request_timeout",
            "message": (
                "request body was not received within the configured timeout"
            ),
        }
        assert response.headers["x-correlation-id"] == "slow-request-body"

    asyncio.run(exercise())

    completed = log_handler.records[-1]
    assert completed.msg == "http.request.completed"
    assert completed.tide_fields["status_code"] == 408
    assert completed.tide_fields["operation"] == "requestBodyTimeout"


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
        assert invalid.json()["code"] == "invalid_request"
        assert invalid.json()["message"] == "request validation failed"
        assert invalid.json()["issues"][0]["fields"] == []
        assert invalid_limit.status_code == 422
        assert invalid_limit.json()["code"] == "invalid_request"
        assert invalid_limit.json()["message"] == "request validation failed"
        assert invalid_limit.json()["issues"][0]["fields"] == []

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
            duplicate_product = await client.post(
                "/api/v1/products",
                headers=_authorization(),
                json={
                    "code": "API-PRODUCT",
                    "name": "Duplicate API product",
                    "unit_price": "9.95",
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
        assert duplicate_product.status_code == 422
        assert duplicate_product.json()["code"] == "validation_failed"
        assert duplicate_product.json()["issues"] == [
            {
                "rule": "unique",
                "message": "code must be unique",
                "fields": ["code"],
                "severity": "error",
            }
        ]
        assert rejected_system_field.status_code == 422
        assert rejected_system_field.json()["issues"][0]["fields"] == ["id"]
        assert created_invoice.status_code == 201
        assert created_invoice.json()["number"] == "INV-2026-0010"
        assert created_invoice.json()["total"] == "170.00"
        assert created_invoice.headers["etag"] == '"1"'
        assert created_invoice.headers["location"] == "/api/v1/invoices/10"
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


def test_server_deletes_only_explicitly_exposed_authorized_records() -> None:
    allowed_app = _app("sales_clerk")
    denied_app = _app("auditor")

    async def exercise() -> None:
        async with _client(allowed_app) as client:
            created = await client.post(
                "/api/v1/products",
                headers=_authorization(),
                json={
                    "code": "DELETE-ME",
                    "name": "Unused product",
                    "unit_price": "1.00",
                    "active": True,
                },
            )
            deleted = await client.delete(
                f"/api/v1/products/{created.json()['id']}",
                headers=_authorization(),
            )
            missing = await client.get(
                f"/api/v1/products/{created.json()['id']}",
                headers=_authorization(),
            )
            restricted = await client.delete(
                "/api/v1/products/1",
                headers=_authorization(),
            )
            invoice_route = await client.delete(
                "/api/v1/invoices/1",
                headers=_authorization(),
            )
        async with _client(denied_app) as client:
            forbidden = await client.delete(
                "/api/v1/products/1",
                headers=_authorization(),
            )

        assert created.status_code == 201
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert missing.status_code == 404
        assert restricted.status_code == 409
        assert restricted.json()["code"] == "delete_restricted"
        assert invoice_route.status_code == 405
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "forbidden"

    asyncio.run(exercise())


def test_server_requires_if_match_for_versioned_delete() -> None:
    app = _app("sales_clerk", model=_invoice_delete_model())

    async def exercise() -> None:
        async with _client(app) as client:
            missing = await client.delete(
                "/api/v1/invoices/8",
                headers=_authorization(),
            )
            stale = await client.delete(
                "/api/v1/invoices/8",
                headers={**_authorization(), "If-Match": '"99"'},
            )
            deleted = await client.delete(
                "/api/v1/invoices/8",
                headers={**_authorization(), "If-Match": '"1"'},
            )
            gone = await client.get(
                "/api/v1/invoices/8",
                headers=_authorization(),
            )

        assert missing.status_code == 428
        assert missing.json()["code"] == "precondition_required"
        assert stale.status_code == 412
        assert stale.json()["code"] == "stale_version"
        assert deleted.status_code == 204
        assert gone.status_code == 404

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
        assert posted.json()["_tide"]["actions"] == {
            "post": {"visible": True, "enabled": False},
            "void": {"visible": True, "enabled": False},
        }
        assert posted.headers["etag"] == '"2"'
        assert replay.status_code == 200
        assert replay.json()["version"] == 2
        assert stale.status_code == 412
        assert stale.json()["code"] == "stale_version"

    asyncio.run(exercise())


def test_server_returns_only_authorized_safe_record_audit_history() -> None:
    allowed_app = _app("auditor")
    denied_app = _app("sales_clerk")
    actor = RequestContext(
        Principal("api:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.REST,
        correlation_id="audit-post-first",
    )
    allowed_app.state.tide.actions.execute(
        "sales.Invoice",
        "post",
        2,
        {},
        actor,
        idempotency_key="audit-history-post-2",
    )
    allowed_app.state.tide.actions.execute(
        "sales.Invoice",
        "post",
        2,
        {},
        RequestContext(
            actor.principal,
            channel=Channel.REST,
            correlation_id="audit-post-replay",
        ),
        idempotency_key="audit-history-post-2",
    )

    async def exercise() -> None:
        async with _client(allowed_app) as client:
            session = await client.get(
                "/api/v1/_tide/session",
                headers=_authorization(),
            )
            history = await client.get(
                "/api/v1/invoices/2/_audit?limit=1",
                headers=_authorization(),
            )
            full_history = await client.get(
                "/api/v1/invoices/2/_audit?limit=10",
                headers=_authorization(),
            )
        async with _client(denied_app) as client:
            denied = await client.get(
                "/api/v1/invoices/2/_audit",
                headers=_authorization(),
            )

        assert session.json()["entities"]["sales.Invoice"]["audit"] is True
        assert history.status_code == 200
        body = history.json()
        assert body["entity"] == "sales.Invoice"
        assert body["identity"] == 2
        assert len(body["events"]) == 1
        event = body["events"][0]
        assert event["action"] == "post"
        assert event["outcome"] == "replayed"
        assert event["principal"] == "api:clerk"
        assert event["channel"] == "rest"
        assert event["correlation_id"] == "audit-post-replay"
        assert "idempotency_key_hash" not in event
        assert "audit-history-post-2" not in repr(body)
        complete = full_history.json()["events"]
        assert [item["kind"] for item in complete] == [
            "action",
            "action",
            "record",
        ]
        record_event = complete[-1]
        assert record_event["operation"] == "update"
        assert record_event["source"] == "action"
        assert record_event["outcome"] is None
        status = next(
            change for change in record_event["changes"] if change["field"] == "status"
        )
        assert status == {
            "field": "status",
            "before_present": True,
            "after_present": True,
            "value_mode": "recorded",
            "before": "draft",
            "after": "posted",
        }
        posted_by = next(
            change
            for change in record_event["changes"]
            if change["field"] == "posted_by"
        )
        assert posted_by["value_mode"] == "redacted"
        assert posted_by["before"] is posted_by["after"] is None
        assert denied.status_code == 403
        assert denied.json()["code"] == "forbidden"

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
            summary = await client.post(
                "/api/v1/_tide/reports/sales.summary",
                headers=_authorization(),
                json={},
            )
            pdf = await client.get(
                (
                    "/api/v1/_tide/reports/sales.invoice/records/1/"
                    "exports/pdf"
                ),
                headers=_authorization(),
            )
            summary_csv = await client.post(
                "/api/v1/_tide/reports/sales.summary/exports/csv",
                headers=_authorization(),
                json={},
            )
            summary_html = await client.post(
                "/api/v1/_tide/reports/sales.summary/exports/html",
                headers=_authorization(),
                json={},
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
            denied_summary = await client.post(
                "/api/v1/_tide/reports/sales.summary",
                headers=_authorization(),
                json={},
            )
            denied_export = await client.post(
                "/api/v1/_tide/reports/sales.summary/exports/csv",
                headers=_authorization(),
                json={},
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
        assert summary.status_code == 200
        # The summary is a grouped listing now: rows are the invoices, and
        # the group total rides in the group's own footer over the wire.
        assert summary.json()["detail"]["rows"][0][-1]["text"] == "850.00"
        summary_group = summary.json()["groups"][0]
        assert summary_group["row_count"] == 3
        assert summary_group["footer_values"][-1]["text"] == "4,610.00"
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF-")
        assert pdf.headers["content-type"] == "application/pdf"
        assert (
            'filename="invoice-INV-2026-0001.pdf"'
            in pdf.headers["content-disposition"]
        )
        assert summary_csv.status_code == 200
        assert summary_csv.content.startswith(b"\xef\xbb\xbf")
        assert b"Customer,Currency,Number,Invoice Date,Total" in summary_csv.content
        assert (
            b"ADRIA - Adria Consulting,EUR,INV-2026-0001" in summary_csv.content
        ), "the group values are repeated on every exported row"
        assert summary_csv.headers["content-type"] == "text/csv; charset=utf-8"
        assert summary_html.status_code == 200
        assert b"<!doctype html>" in summary_html.content
        assert b"Posted Sales Summary" in summary_html.content
        assert summary_html.headers["content-type"] == "text/html; charset=utf-8"
        assert session.json()["reports"] == []
        assert denied.status_code == 403
        assert denied.json()["code"] == "forbidden"
        assert denied_summary.status_code == 403
        assert denied_summary.json()["code"] == "forbidden"
        assert denied_export.status_code == 403
        assert denied_export.json()["code"] == "forbidden"

    asyncio.run(exercise())


def test_report_pdf_export_fails_closed_without_optional_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_pdf(_document: Any) -> bytes:
        raise PdfDependencyMissing

    monkeypatch.setattr(api_server, "render_pdf", missing_pdf)
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            response = await client.get(
                (
                    "/api/v1/_tide/reports/sales.invoice/records/1/"
                    "exports/pdf"
                ),
                headers=_authorization(),
            )

        assert response.status_code == 503
        assert response.json() == {
            "code": "report_format_unavailable",
            "message": (
                "PDF export requires the 'report' extra: "
                "pip install tide-framework[report]"
            ),
        }

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
            "--log-level",
            "warning",
            "--max-request-body-bytes",
            "2048",
            "--max-concurrent-requests",
            "7",
            "--request-body-timeout",
            "13",
            "--keep-alive-timeout",
            "11",
            "--graceful-shutdown-timeout",
            "12",
        ]
    )

    assert result == 0
    assert launched["configuration"] == {
        "host": "127.0.0.1",
        "port": 8123,
        "log_level": "warning",
        "access_log": False,
        "proxy_headers": False,
        "server_header": False,
        "limit_concurrency": 7,
        "timeout_keep_alive": 11,
        "timeout_graceful_shutdown": 12,
    }
    runtime = launched["app"].state.tide
    assert runtime.authenticator.authenticate(TOKEN) == Principal(
        "development:api",
        roles=frozenset({"auditor"}),
    )
    assert runtime.max_request_body_bytes == 2048
    assert runtime.request_body_timeout_seconds == 13
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert "development auth only" in output


def test_tide_serve_rejects_development_authentication_off_loopback(
    capsys,
) -> None:
    result = main(
        ["serve", str(INVOICING), "--demo", "--host", "0.0.0.0"]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: development authentication may listen only on a "
        "loopback interface\n"
    )


def test_tide_serve_requires_initialized_local_identity_store(capsys) -> None:
    result = main(
        ["serve", str(INVOICING), "--demo", "--auth", "local"]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: local authentication requires --local-auth-store\n"
    )


def test_tide_serve_builds_local_password_browser_app(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    launched: dict[str, Any] = {}
    store = LocalUserStore(
        tmp_path / "local-auth.sqlite3",
        application="TIDE Invoicing",
        password_iterations=1_000,
    )
    store.initialize()
    store.create_user(
        "alice",
        "correct horse battery staple",
        roles=("sales_clerk",),
    )

    def fake_run(app: Any, **configuration: Any) -> None:
        launched["app"] = app
        launched["configuration"] = configuration

    monkeypatch.setattr(uvicorn, "run", fake_run)
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "local",
            "--local-auth-store",
            str(store.path),
        ]
    )

    assert result == 0
    runtime = launched["app"].state.tide
    assert runtime.authenticator.authentication_type == "local-password"
    assert runtime.browser_auth is runtime.authenticator
    assert launched["configuration"]["host"] == "127.0.0.1"
    output = capsys.readouterr().out
    assert "local username/password" in output
    assert "browser login enabled" in output


def test_tide_serve_rejects_invalid_operational_limits(capsys) -> None:
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--max-request-body-bytes",
            "0",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: maximum request body size must be a positive integer\n"
    )


def test_tide_serve_requires_direct_tls_for_non_loopback_oidc(capsys) -> None:
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "oidc",
            "--host",
            "0.0.0.0",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: non-loopback serving requires --ssl-certfile and "
        "--ssl-keyfile, or --behind-tls-proxy\n"
    )


def test_tide_serve_rejects_unknown_oidc_role_mapping(capsys) -> None:
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "oidc",
            "--oidc-issuer",
            "https://identity.example.test/tenant",
            "--oidc-audience",
            "tide-api",
            "--oidc-role-map",
            "external-sales=not-an-application-role",
        ]
    )

    assert result == 1
    assert "unknown application role 'not-an-application-role'" in (
        capsys.readouterr().err
    )


def test_tide_serve_rejects_browser_login_with_development_auth(capsys) -> None:
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--web-oidc-client-id",
            "tide-web",
            "--web-oidc-redirect-uri",
            "http://127.0.0.1:8000/api/v1/_tide/browser-auth/callback",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: browser OIDC login requires --auth oidc\n"
    )


def test_tide_serve_builds_non_loopback_oidc_app_with_direct_tls(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    launched: dict[str, Any] = {}
    discovery: dict[str, Any] = {}
    browser_discovery: dict[str, Any] = {}
    certfile = tmp_path / "server-cert.pem"
    keyfile = tmp_path / "server-key.pem"
    certfile.write_text("test certificate", encoding="utf-8")
    keyfile.write_text("test key", encoding="utf-8")

    class TestOidcAuthenticator:
        authentication_type = "oidc-jwt"
        production = True

        def authenticate(self, credential: str) -> Principal | None:
            if credential == "valid-token":
                return Principal(
                    "oidc:test-user",
                    roles=frozenset({"sales_clerk"}),
                )
            return None

    class TestBrowserAuth:
        authentication_mode = "oidc"
        secure_cookie = True
        transaction_cookie_name = "__Host-tide_oidc_transaction"
        session_cookie_name = "__Host-tide_session"
        transaction_lifetime_seconds = 300
        session_lifetime_seconds = 3600

        def authenticate_session(self, session_id: str | None) -> None:
            return None

    def fake_discovery(cls: Any, **configuration: Any) -> Any:
        discovery.update(configuration)
        return TestOidcAuthenticator()

    def fake_run(app: Any, **configuration: Any) -> None:
        launched["app"] = app
        launched["configuration"] = configuration

    def fake_browser_discovery(cls: Any, **configuration: Any) -> Any:
        browser_discovery.update(configuration)
        return TestBrowserAuth()

    monkeypatch.setattr(
        OidcJwtAuthenticator,
        "from_discovery",
        classmethod(fake_discovery),
    )
    monkeypatch.setattr(
        OidcBrowserAuth,
        "from_discovery",
        classmethod(fake_browser_discovery),
    )
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("TIDE_WEB_CLIENT_SECRET", "browser-client-secret")

    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "oidc",
            "--host",
            "0.0.0.0",
            "--port",
            "8443",
            "--ssl-certfile",
            str(certfile),
            "--ssl-keyfile",
            str(keyfile),
            "--oidc-issuer",
            "https://identity.example.test/tenant",
            "--oidc-audience",
            "tide-api",
            "--oidc-role-map",
            "external-sales=sales_clerk",
            "--web-oidc-client-id",
            "tide-web",
            "--web-oidc-client-secret-env",
            "TIDE_WEB_CLIENT_SECRET",
            "--web-oidc-redirect-uri",
            "https://tide.example.test:8443/api/v1/_tide/browser-auth/callback",
            "--web-oidc-scope",
            "openid",
            "--web-oidc-scope",
            "offline_access",
            "--web-session-lifetime",
            "3600",
            "--mcp",
            "--mcp-resource-url",
            "https://tide.example.test:8443/mcp",
        ]
    )

    assert result == 0
    assert discovery["role_map"] == {"external-sales": "sales_clerk"}
    assert discovery["algorithms"] == ("RS256",)
    assert browser_discovery["client_id"] == "tide-web"
    assert browser_discovery["client_secret"] == "browser-client-secret"
    assert browser_discovery["scopes"] == ("openid", "offline_access")
    assert browser_discovery["session_lifetime_seconds"] == 3600
    assert launched["configuration"] == {
        "host": "0.0.0.0",
        "port": 8443,
        "log_level": "info",
        "access_log": False,
        "proxy_headers": False,
        "server_header": False,
        "limit_concurrency": 100,
        "timeout_keep_alive": 5,
        "timeout_graceful_shutdown": 30,
        "ssl_certfile": str(certfile),
        "ssl_keyfile": str(keyfile),
    }
    schema = launched["app"].openapi()
    assert schema["x-tide"]["authentication"] == "oidc-jwt"
    assert schema["x-tide"]["browser_authentication"] is True
    assert schema["components"]["securitySchemes"]["bearerAuth"][
        "bearerFormat"
    ] == "JWT"
    hosted_mcp = launched["app"].state.tide_mcp
    assert hosted_mcp.resource_url == "https://tide.example.test:8443/mcp"
    assert hosted_mcp.issuer_url == "https://identity.example.test/tenant"
    output = capsys.readouterr().out
    assert "https://0.0.0.0:8443" in output
    assert "OIDC issuer https://identity.example.test/tenant" in output
    assert "browser login enabled" in output
    assert "MCP: https://tide.example.test:8443/mcp" in output


def test_tide_serve_mounts_read_only_mcp_on_the_local_api(
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
            "sales_clerk",
            "--port",
            "8124",
            "--mcp",
        ]
    )

    assert result == 0
    hosted = launched["app"].state.tide_mcp
    assert hosted.path == "/mcp"
    assert hosted.resource_url == "http://127.0.0.1:8124/mcp"
    assert hosted.issuer_url == "http://127.0.0.1:8124"
    assert set(hosted.service.exposures) == {
        "catalog.Product",
        "crm.Customer",
        "sales.Invoice",
    }
    assert launched["configuration"] == {
        "host": "127.0.0.1",
        "port": 8124,
        "log_level": "info",
        "access_log": False,
        "proxy_headers": False,
        "server_header": False,
        "limit_concurrency": 100,
        "timeout_keep_alive": 5,
        "timeout_graceful_shutdown": 30,
    }
    assert "MCP: http://127.0.0.1:8124/mcp" in capsys.readouterr().out


def test_tide_serve_requires_canonical_resource_url_for_network_mcp(
    capsys,
    tmp_path: Path,
) -> None:
    certfile = tmp_path / "server-cert.pem"
    keyfile = tmp_path / "server-key.pem"
    certfile.write_text("test certificate", encoding="utf-8")
    keyfile.write_text("test key", encoding="utf-8")

    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "oidc",
            "--host",
            "0.0.0.0",
            "--ssl-certfile",
            str(certfile),
            "--ssl-keyfile",
            str(keyfile),
            "--mcp",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: non-loopback MCP serving requires "
        "--mcp-resource-url\n"
    )


def test_tide_serve_requires_mcp_resource_path_to_match_endpoint(capsys) -> None:
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--mcp",
            "--mcp-resource-url",
            "http://127.0.0.1:8000/not-mcp",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: MCP resource URL must be an absolute HTTP or HTTPS "
        "URL whose path exactly matches --mcp-path\n"
    )



def test_a_listed_record_carries_the_name_of_what_it_points_at() -> None:
    """The grid can draw `customer` without asking who customer 1 is.

    Eight invoices name three customers. Before this the client bought
    those three names with three more authorized round trips, renewed as
    the reader scrolled; now they arrive with the page that needs them.
    """

    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            listed = await client.get(
                "/api/v1/invoices",
                headers=_authorization(),
            )

        assert listed.status_code == 200
        records = listed.json()["records"]
        assert [record["_tide"]["references"]["customer"] for record in records] == [
            "ADRIA - Adria Consulting",
            "MORA - Mora Trade",
            "LOV - Lovćen Studio",
            "ADRIA - Adria Consulting",
            "MORA - Mora Trade",
            "LOV - Lovćen Studio",
            "ADRIA - Adria Consulting",
            "MORA - Mora Trade",
            # The empty draft the appearance rule exists for.
            "LOV - Lovćen Studio",
        ]

    asyncio.run(exercise())


def test_a_fetched_record_names_what_its_children_point_at_too() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            fetched = await client.get(
                "/api/v1/invoices/1",
                headers=_authorization(),
            )

        assert fetched.status_code == 200
        invoice = fetched.json()
        assert invoice["_tide"]["references"] == {
            "customer": "ADRIA - Adria Consulting",
        }
        # A collection grid draws references of its own, and the child rows
        # travel inside this response rather than being fetched separately.
        assert [line["_tide"]["references"] for line in invoice["lines"]] == [
            {"product": "CONS - Consulting hour"},
        ]

    asyncio.run(exercise())


def test_a_reference_the_caller_cannot_read_carries_no_name() -> None:
    app = _app("summary_viewer")

    async def exercise() -> None:
        async with _client(app) as client:
            listed = await client.get(
                "/api/v1/invoices",
                headers=_authorization(),
            )

        assert listed.status_code == 200
        records = listed.json()["records"]
        assert records
        # `summary_viewer` may read invoices and nothing else. The identity
        # is still there -- it is a value of this record -- but nothing on
        # the wire says what it names.
        assert all(record["customer"] for record in records)
        assert all(
            "references" not in record.get("_tide", {}) for record in records
        )

    asyncio.run(exercise())


def test_a_queried_page_names_its_references_the_same_way_a_listed_one_does() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            queried = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={"filters": [{"field": "customer", "operator": "eq", "value": 2}]},
            )

        assert queried.status_code == 200
        records = queried.json()["records"]
        assert len(records) == 3
        assert {record["_tide"]["references"]["customer"] for record in records} == {
            "MORA - Mora Trade",
        }

    asyncio.run(exercise())

def test_manifest_summaries_are_filtered_with_their_columns() -> None:
    app = _app("summary_viewer")

    async def exercise() -> None:
        async with _client(app) as client:
            presentation = await client.get(
                "/api/v1/_tide/presentation",
                headers=_authorization(),
            )

        assert presentation.status_code == 200
        invoice_view = presentation.json()["views"]["sales.Invoice.browse"]
        # `summary_viewer` cannot read `total`, so the column is filtered
        # from its manifest -- and a summary the grid could never request
        # must leave with it, or the first page load answers 403.
        assert "total" not in [
            column["name"] for column in invoice_view["columns"]
        ]
        assert invoice_view["summaries"] == [
            {"field": "number", "function": "count"},
        ]
        assert "total" not in invoice_view["filterable_fields"]

    asyncio.run(exercise())


def test_a_queried_page_can_carry_summaries_for_its_whole_set() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            filters = [{"field": "customer", "operator": "eq", "value": 2}]
            queried = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={
                    "filters": filters,
                    "limit": 1,
                    "summaries": [
                        {"field": "total", "function": "sum"},
                        {"field": "number", "function": "count"},
                    ],
                },
            )
            assert queried.status_code == 200
            body = queried.json()
            assert len(body["records"]) == 1

            everything = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={"filters": filters, "limit": 100},
            )
            walked = everything.json()["records"]
            assert len(walked) > 1
            # The summary answers for the whole filtered set while the page
            # shows one record -- the walk over the same filter is the
            # yardstick, so no demo value is pinned here.
            assert body["summaries"] == [
                {
                    "field": "total",
                    "function": "sum",
                    "value": str(sum(Decimal(record["total"]) for record in walked)),
                },
                {"field": "number", "function": "count", "value": len(walked)},
            ]
            # A page that did not ask carries no answers.
            assert everything.json()["summaries"] is None

    asyncio.run(exercise())


def test_a_query_can_ask_for_membership_and_blanks() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            drafts_or_posted = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={
                    "filters": [
                        {
                            "field": "status",
                            "operator": "in",
                            "value": ["draft", "posted"],
                        }
                    ],
                    "limit": 100,
                },
            )
            assert drafts_or_posted.status_code == 200
            statuses = {
                record["status"]
                for record in drafts_or_posted.json()["records"]
            }
            assert statuses == {"draft", "posted"}

            # The blank element: rows where the column is empty count as
            # chosen. Every seeded draft has no posted_by.
            blanks = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={
                    "filters": [
                        {
                            "field": "posted_at",
                            "operator": "in",
                            "value": [None],
                        }
                    ],
                    "limit": 100,
                },
            )
            assert blanks.status_code == 200
            assert all(
                record["posted_at"] is None
                for record in blanks.json()["records"]
            )
            assert len(blanks.json()["records"]) > 0

            # A reference column takes the target's identities, typed.
            by_customer = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={
                    "filters": [
                        {"field": "customer", "operator": "in", "value": [1, 3]}
                    ],
                    "limit": 100,
                },
            )
            assert by_customer.status_code == 200
            assert {
                record["customer"]
                for record in by_customer.json()["records"]
            } == {1, 3}

            empty = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={
                    "filters": [
                        {"field": "status", "operator": "in", "value": []}
                    ]
                },
            )
            assert empty.status_code == 400

            not_a_list = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={
                    "filters": [
                        {"field": "status", "operator": "in", "value": "draft"}
                    ]
                },
            )
            assert not_a_list.status_code == 400

    asyncio.run(exercise())


def test_a_column_can_be_enumerated_for_its_filter_list() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            statuses = await client.post(
                "/api/v1/invoices/_distinct",
                headers=_authorization(),
                json={"field": "status"},
            )
            assert statuses.status_code == 200
            body = statuses.json()
            assert body["field"] == "status"
            assert body["truncated"] is False
            walked = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={"limit": 100},
            )
            # The list is the walk's own set of values, ordered ascending --
            # the yardstick is the same filtered set, so nothing is pinned.
            assert [item["value"] for item in body["values"]] == sorted(
                {record["status"] for record in walked.json()["records"]}
            )
            assert all(item["display"] is None for item in body["values"])

            # Under a condition, only that slice's values answer.
            drafts = await client.post(
                "/api/v1/invoices/_distinct",
                headers=_authorization(),
                json={
                    "field": "status",
                    "filters": [
                        {"field": "status", "operator": "eq", "value": "draft"}
                    ],
                },
            )
            assert [item["value"] for item in drafts.json()["values"]] == [
                "draft"
            ]

            # A reference column answers identities beside their names.
            customers = await client.post(
                "/api/v1/invoices/_distinct",
                headers=_authorization(),
                json={"field": "customer"},
            )
            assert customers.status_code == 200
            names = {
                item["value"]: item["display"]
                for item in customers.json()["values"]
            }
            assert names[1] == "ADRIA - Adria Consulting"
            assert all(display is not None for display in names.values())

            unknown = await client.post(
                "/api/v1/invoices/_distinct",
                headers=_authorization(),
                json={"field": "ghost"},
            )
            assert unknown.status_code == 400

            unreadable = await client.post(
                "/api/v1/invoices/_distinct",
                headers=_authorization(),
                json={"field": "posted_by"},
            )
            assert unreadable.status_code == 403

    asyncio.run(exercise())


def test_summary_requests_are_refused_like_any_bad_query() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            unknown_field = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={"summaries": [{"field": "ghost", "function": "count"}]},
            )
            assert unknown_field.status_code == 400

            wrong_type = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={"summaries": [{"field": "number", "function": "sum"}]},
            )
            assert wrong_type.status_code == 400

            # The closed set is the wire schema itself.
            unknown_function = await client.post(
                "/api/v1/invoices/_query",
                headers=_authorization(),
                json={"summaries": [{"field": "total", "function": "median"}]},
            )
            assert unknown_function.status_code == 422

    asyncio.run(exercise())


def _app(
    role: str | None,
    *,
    model: Any | None = None,
    logger: logging.Logger | None = None,
    max_request_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES,
    request_body_timeout_seconds: int = DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
    web_root: Path | None = None,
    browser_auth: Any = None,
    authenticator: Any = None,
) -> Any:
    model = model or compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
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
        authenticator or DevelopmentTokenAuthenticator(TOKEN, principal),
        actions=actions,
        logger=logger,
        max_request_body_bytes=max_request_body_bytes,
        request_body_timeout_seconds=request_body_timeout_seconds,
        web_root=web_root,
        browser_auth=browser_auth,
    )


def _invoice_delete_model() -> Any:
    model = compile_project(INVOICING)
    invoice = model.entity("sales.Invoice")
    metadata = deep_thaw(invoice.metadata)
    metadata["permissions"]["delete"] = "sales.invoice.write"
    operations = metadata["expose"]["rest"]["operations"]
    metadata["expose"]["rest"]["operations"] = [*operations, "delete"]
    entities = dict(model.entities)
    entities[invoice.name] = replace(
        invoice,
        metadata=immutable_mapping(metadata),
    )
    return replace(model, entities=immutable_mapping(entities))


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _authorization() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _recording_logger() -> tuple[logging.Logger, _RecordingHandler]:
    logger = logging.Logger("tide.test.runtime", level=logging.DEBUG)
    handler = _RecordingHandler()
    logger.addHandler(handler)
    return logger, handler


def test_the_manifest_offers_only_the_export_a_principal_and_server_can_do() -> None:
    """Never present a download the server would refuse.

    Two filters, not one: the principal has to hold `tide.records.export`,
    and the process has to have the writer installed. A grid that offered
    XLSX from a server without the extra would be offering a 503.
    """

    async def exercise() -> None:
        async with _client(_app("sales_clerk")) as client:
            permitted = await client.get(
                "/api/v1/_tide/presentation",
                headers=_authorization(),
            )
        async with _client(_app("auditor")) as client:
            withheld = await client.get(
                "/api/v1/_tide/presentation",
                headers=_authorization(),
            )

        assert permitted.status_code == 200
        expected = ["csv", "xlsx"] if SPREADSHEET_AVAILABLE else ["csv"]
        offered = {
            name: view["export_formats"]
            for name, view in permitted.json()["views"].items()
        }
        assert offered
        assert all(formats == expected for formats in offered.values()), offered

        # The auditor reads every grid the clerk does and may not carry one
        # off, which is the whole point of the capability being separate.
        assert withheld.status_code == 200
        assert withheld.json()["views"]
        assert all(
            view["export_formats"] == []
            for view in withheld.json()["views"].values()
        )

    asyncio.run(exercise())


def test_a_clerk_can_take_the_filtered_grid_away() -> None:
    """The file answers for the query, not for the page the grid had."""

    async def exercise() -> None:
        async with _client(_app("sales_clerk")) as client:
            response = await client.post(
                "/api/v1/invoices/_export/csv",
                json={
                    "view": "sales.Invoice.browse",
                    "filters": [
                        {"field": "status", "operator": "eq", "value": "draft"}
                    ],
                    "sort": [{"field": "id", "descending": False}],
                },
                headers=_authorization(),
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        assert ".csv" in response.headers["content-disposition"]
        assert response.headers["X-Tide-Export-Rows"] == "5"
        assert response.headers["X-Tide-Export-Total"] == "5"
        body = response.text
        # The byte-order mark is what makes Excel read a UTF-8 CSV as UTF-8,
        # so it is carried deliberately rather than tolerated.
        assert body.startswith("﻿")
        assert body.splitlines()[0] == (
            "﻿Number,Invoice Date,Customer,Status,Total"
        )
        assert len(body.strip().splitlines()) == 6

    asyncio.run(exercise())


def test_taking_the_grid_away_is_refused_without_the_capability() -> None:
    async def exercise() -> None:
        async with _client(_app("auditor")) as client:
            response = await client.post(
                "/api/v1/invoices/_export/csv",
                json={"view": "sales.Invoice.browse", "filters": [], "sort": []},
                headers=_authorization(),
            )

        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    asyncio.run(exercise())


def test_an_unexportable_view_is_a_missing_thing_not_a_bad_request() -> None:
    async def exercise() -> None:
        async with _client(_app("sales_clerk")) as client:
            unknown = await client.post(
                "/api/v1/invoices/_export/csv",
                json={"view": "sales.NotAView", "filters": [], "sort": []},
                headers=_authorization(),
            )
            form = await client.post(
                "/api/v1/invoices/_export/csv",
                json={"view": "sales.Invoice.edit", "filters": [], "sort": []},
                headers=_authorization(),
            )

        assert unknown.status_code == 404
        assert form.status_code == 404
        # A route that does not exist also answers 404, with FastAPI's own
        # `{"detail": "Not Found"}`. Asserting the code is what tells a
        # refusal apart from an absence -- without it this test passes
        # against a server that has no export route at all.
        assert unknown.json()["code"] == "not_found"
        assert form.json()["code"] == "not_found"

    asyncio.run(exercise())


def test_an_unknown_export_format_never_reaches_the_handler() -> None:
    """The path segment is a closed set, so a PDF of a grid is a 422."""

    async def exercise() -> None:
        async with _client(_app("sales_clerk")) as client:
            response = await client.post(
                "/api/v1/invoices/_export/pdf",
                json={"view": "sales.Invoice.browse", "filters": [], "sort": []},
                headers=_authorization(),
            )

        assert response.status_code == 422

    asyncio.run(exercise())


def test_an_export_is_written_down() -> None:
    """An export is the one read worth finding in a log a year later."""

    logger, handler = _recording_logger()

    async def exercise() -> None:
        async with _client(_app("sales_clerk", logger=logger)) as client:
            response = await client.post(
                "/api/v1/invoices/_export/csv",
                json={"view": "sales.Invoice.browse", "filters": [], "sort": []},
                headers=_authorization(),
            )
        assert response.status_code == 200

        # The attribute is `tide_event`, not `event`.
        written = [
            record
            for record in handler.records
            if getattr(record, "tide_event", None) == "records.export"
        ]
        assert written, [
            getattr(record, "tide_event", None) for record in handler.records
        ]
        fields = written[0].tide_fields
        assert fields["subject"] == "sales.Invoice.browse"
        assert fields["operation"] == "csv"
        assert fields["principal"] == "api:test"
        assert fields["rows"] == 9
        assert fields["total"] == 9

    asyncio.run(exercise())


@pytest.mark.skipif(not SPREADSHEET_AVAILABLE, reason="spreadsheet extra absent")
def test_the_workbook_arrives_as_a_workbook() -> None:
    async def exercise() -> None:
        async with _client(_app("sales_clerk")) as client:
            response = await client.post(
                "/api/v1/invoices/_export/xlsx",
                json={"view": "sales.Invoice.browse", "filters": [], "sort": []},
                headers=_authorization(),
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert ".xlsx" in response.headers["content-disposition"]
        # A real zip container, not an error body with a hopeful header.
        assert response.content[:2] == b"PK"

    asyncio.run(exercise())
