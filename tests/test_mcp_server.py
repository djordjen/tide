from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tide import compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.mcp import RuntimeMcpService
from tide.mcp.server import (
    TideMcpTokenVerifier,
    build_runtime_mcp_server,
    mount_runtime_mcp,
)
from tide.runtime import Principal, configure_application_runtime
from tide.services import ActionService, AuditHistoryService, RecordsService
from tide.tui import seed_demo_data


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-mcp-test-token-that-is-long-enough"
BASE_URL = "http://127.0.0.1:8000"
MCP_URL = f"{BASE_URL}/mcp"


def test_streamable_http_mcp_executes_secured_runtime_workflow() -> None:
    app = _app(("sales_clerk", "auditor"))

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE_URL,
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as http:
                async with streamable_http_client(
                    MCP_URL,
                    http_client=http,
                ) as (read_stream, write_stream, _session_id):
                    async with ClientSession(read_stream, write_stream) as session:
                        initialized = await session.initialize()
                        resources = await session.list_resources()
                        templates = await session.list_resource_templates()
                        tools = await session.list_tools()
                        schema_result = await session.read_resource(
                            "tide://runtime/tide_invoicing/entities/"
                            "catalog.Product/schema"
                        )
                        search_result = await session.call_tool(
                            "search_catalog_product",
                            {
                                "filters": [
                                    {
                                        "field": "unit_price",
                                        "operator": "gte",
                                        "value": "200.00",
                                    }
                                ],
                                "sort": [
                                    {"field": "unit_price", "descending": True}
                                ],
                                "limit": 2,
                            },
                        )
                        invalid_product_result = await session.call_tool(
                            "create_catalog_product",
                            {
                                "values": {
                                    "id": 99,
                                    "code": "BAD",
                                    "name": "Must not be created",
                                    "unit_price": "1.00",
                                }
                            },
                        )
                        product_result = await session.call_tool(
                            "create_catalog_product",
                            {
                                "values": {
                                    "code": "MCP",
                                    "name": "Created through MCP",
                                    "unit_price": "19.95",
                                }
                            },
                        )
                        invoice_result = await session.call_tool(
                            "create_sales_invoice",
                            {
                                "values": {
                                    "invoice_date": "2026-07-19",
                                    "customer": 1,
                                    "lines": [
                                        {
                                            "line_number": 1,
                                            "description": "MCP line",
                                            "quantity": "2.000",
                                            "unit_price": "19.95",
                                            "product": 4,
                                        }
                                    ],
                                }
                            },
                        )
                        post_result = await session.call_tool(
                            "post_sales_invoice",
                            {
                                "identity": 9,
                                "expected_version": 1,
                                "idempotency_key": "mcp-post-invoice-9",
                            },
                        )
                        audit_result = await session.read_resource(
                            "tide://runtime/tide_invoicing/entities/"
                            "sales.Invoice/records/9/audit"
                        )

        assert initialized.serverInfo.name == "TIDE Invoicing Runtime"
        assert [str(resource.uri) for resource in resources.resources] == [
            "tide://runtime/tide_invoicing/entities/catalog.Product/schema",
            "tide://runtime/tide_invoicing/entities/crm.Customer/schema",
            "tide://runtime/tide_invoicing/entities/sales.Invoice/schema",
        ]
        assert len(templates.resourceTemplates) == 6
        assert {tool.name for tool in tools.tools} == {
            "search_catalog_product",
            "create_catalog_product",
            "update_catalog_product",
            "delete_catalog_product",
            "search_crm_customer",
            "create_crm_customer",
            "update_crm_customer",
            "delete_crm_customer",
            "search_sales_invoice",
            "create_sales_invoice",
            "update_sales_invoice",
            "post_sales_invoice",
        }
        schema = json.loads(schema_result.contents[0].text)  # type: ignore[union-attr]
        assert schema["entity"] == "catalog.Product"
        assert {field["name"] for field in schema["fields"]} == {
            "active",
            "code",
            "id",
            "name",
            "unit_price",
        }
        assert search_result.isError is False
        assert search_result.structuredContent is not None
        assert search_result.structuredContent["entity"] == "catalog.Product"
        assert [
            record["code"]
            for record in search_result.structuredContent["records"]
        ] == ["LIC", "SUP"]
        assert search_result.structuredContent["records"][0]["unit_price"] == (
            "1200.00"
        )
        assert invalid_product_result.isError is True
        assert product_result.isError is False
        assert product_result.structuredContent is not None
        assert product_result.structuredContent["record"]["unit_price"] == "19.95"
        assert invoice_result.isError is False
        assert invoice_result.structuredContent is not None
        assert invoice_result.structuredContent["record"]["number"] == (
            "INV-2026-000009"
        )
        assert invoice_result.structuredContent["record"]["total"] == "39.90"
        assert post_result.isError is False
        assert post_result.structuredContent is not None
        assert post_result.structuredContent["operation"] == "action"
        assert post_result.structuredContent["action"] == "post"
        assert post_result.structuredContent["record"]["status"] == "posted"
        audit = json.loads(audit_result.contents[0].text)  # type: ignore[union-attr]
        assert audit["identity"] == 9
        assert {event["kind"] for event in audit["events"]} == {
            "action",
            "record",
        }
        assert all(event["channel"] == "mcp" for event in audit["events"])

    asyncio.run(exercise())


def test_mcp_http_authentication_and_protected_resource_metadata_fail_closed() -> None:
    app = _app("sales_clerk")
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE_URL,
            ) as client:
                missing = await client.post("/mcp", json=initialize)
                incorrect = await client.post(
                    "/mcp",
                    json=initialize,
                    headers={"Authorization": "Bearer incorrect"},
                )
                metadata = await client.get(
                    "/.well-known/oauth-protected-resource/mcp"
                )

        for response in (missing, incorrect):
            assert response.status_code == 401
            challenge = response.headers["www-authenticate"]
            assert challenge.startswith("Bearer")
            assert "resource_metadata=" in challenge
            assert TOKEN not in response.text
        assert metadata.status_code == 200
        assert metadata.json() == {
            "resource": MCP_URL,
            "authorization_servers": [BASE_URL + "/"],
            "scopes_supported": [],
            "bearer_methods_supported": ["header"],
        }

    asyncio.run(exercise())


def test_mcp_token_verifier_preserves_server_controlled_principal_identity() -> None:
    principal = Principal(
        "oidc:user-123",
        roles=frozenset({"sales_clerk"}),
        permissions=frozenset({"test.direct"}),
    )
    verifier = TideMcpTokenVerifier(
        DevelopmentTokenAuthenticator(TOKEN, principal),
        MCP_URL,
        BASE_URL,
    )

    verified = asyncio.run(verifier.verify_token(TOKEN))

    assert verified is not None
    assert verified.client_id == principal.identifier
    assert verified.subject == principal.identifier
    assert verified.resource == MCP_URL
    assert verified.claims == {
        "iss": BASE_URL,
        "tide_roles": ["sales_clerk"],
        "tide_permissions": ["test.direct"],
    }
    assert asyncio.run(verifier.verify_token("incorrect")) is None


def _app(role: str | tuple[str, ...]) -> object:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions) is True
    audits = AuditHistoryService(
        model,
        actions.execution_store,
        records.security,
    )
    roles = (role,) if isinstance(role, str) else role
    authenticator = DevelopmentTokenAuthenticator(
        TOKEN,
        Principal("mcp:test", roles=frozenset(roles)),
    )
    app = build_fastapi_app(
        model,
        records,
        authenticator,
        actions=actions,
        audits=audits,
    )
    hosted = build_runtime_mcp_server(
        RuntimeMcpService(
            model,
            records,
            actions=actions,
            audits=audits,
        ),
        authenticator,
        issuer_url=BASE_URL,
        resource_url=MCP_URL,
    )
    mount_runtime_mcp(app, hosted)
    return app
