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
from tide.runtime import Principal
from tide.services import RecordsService
from tide.tui import seed_demo_data


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-mcp-test-token-that-is-long-enough"
BASE_URL = "http://127.0.0.1:8000"
MCP_URL = f"{BASE_URL}/mcp"


def test_streamable_http_mcp_lists_and_executes_secured_read_capabilities() -> None:
    app = _app("sales_clerk")

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

        assert initialized.serverInfo.name == "TIDE Invoicing Runtime"
        assert [str(resource.uri) for resource in resources.resources] == [
            "tide://runtime/tide_invoicing/entities/catalog.Product/schema",
            "tide://runtime/tide_invoicing/entities/crm.Customer/schema",
            "tide://runtime/tide_invoicing/entities/sales.Invoice/schema",
        ]
        assert len(templates.resourceTemplates) == 3
        assert {tool.name for tool in tools.tools} == {
            "search_catalog_product",
            "search_crm_customer",
            "search_sales_invoice",
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


def _app(role: str) -> object:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    authenticator = DevelopmentTokenAuthenticator(
        TOKEN,
        Principal("mcp:test", roles=frozenset({role})),
    )
    app = build_fastapi_app(model, records, authenticator)
    hosted = build_runtime_mcp_server(
        RuntimeMcpService(model, records),
        authenticator,
        issuer_url=BASE_URL,
        resource_url=MCP_URL,
    )
    mount_runtime_mcp(app, hosted)
    return app
