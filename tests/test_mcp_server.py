from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tide import compile_project
from tide.api.config import DEFAULT_MAX_REQUEST_BODY_BYTES
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.mcp import RuntimeMcpService
from tide.mcp.server import (
    TideMcpTokenVerifier,
    _parameter_model,
    build_runtime_mcp_server,
    mount_runtime_mcp,
)
from tide.reporting import ReportService
from tide.runtime import (
    Channel,
    Principal,
    RequestContext,
    configure_application_runtime,
)
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
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "X-Correlation-ID": "mcp-workflow-123",
                },
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
                                "identity": 10,
                                "expected_version": 1,
                                "idempotency_key": "mcp-post-invoice-9",
                            },
                        )
                        audit_result = await session.read_resource(
                            "tide://runtime/tide_invoicing/entities/"
                            "sales.Invoice/records/10/audit"
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
            "void_sales_invoice",
            "report_sales_invoice",
            "report_sales_summary",
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
            "INV-2026-0010"
        )
        assert invoice_result.structuredContent["record"]["total"] == "39.90"
        assert post_result.isError is False
        assert post_result.structuredContent is not None
        assert post_result.structuredContent["operation"] == "action"
        assert post_result.structuredContent["action"] == "post"
        assert post_result.structuredContent["record"]["status"] == "posted"
        audit = json.loads(audit_result.contents[0].text)  # type: ignore[union-attr]
        assert audit["identity"] == 10
        assert {event["kind"] for event in audit["events"]} == {
            "action",
            "record",
        }
        assert all(event["channel"] == "mcp" for event in audit["events"])
        assert all(
            event["correlation_id"] == "mcp-workflow-123"
            for event in audit["events"]
        )

    asyncio.run(exercise())


def test_streamable_http_mcp_runs_reports_as_typed_documents() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE_URL,
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "X-Correlation-ID": "mcp-report-123",
                },
            ) as http:
                async with streamable_http_client(
                    MCP_URL,
                    http_client=http,
                ) as (read_stream, write_stream, _session_id):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        summary_result = await session.call_tool(
                            "report_sales_summary",
                            {"parameters": {"from_date": "2026-01-01"}},
                        )
                        invoice_result = await session.call_tool(
                            "report_sales_invoice",
                            {"parameters": {"invoice_id": 1}},
                        )
                        missing_identity = await session.call_tool(
                            "report_sales_invoice", {}
                        )

        by_name = {tool.name: tool for tool in tools.tools}
        assert {"report_sales_summary", "report_sales_invoice"} <= set(by_name)

        # A summary whose parameters are all optional may be called bare;
        # a record report's identity parameter is required in the schema too.
        summary_schema = by_name["report_sales_summary"].inputSchema
        assert "parameters" not in summary_schema.get("required", [])
        invoice_schema = by_name["report_sales_invoice"].inputSchema
        assert "parameters" in invoice_schema.get("required", [])

        assert summary_result.isError is False
        document = summary_result.structuredContent
        assert document is not None
        assert document["kind"] == "summary"
        assert document["groups"], "the demo seed posts grouped sales"
        names = [column["name"] for column in document["columns"]]
        total_index = names.index("total")
        assert document["columns"][total_index]["type"] == "decimal"
        first_row = document["rows"][document["groups"][0]["row_start"]]
        cell = first_row[total_index]
        # Exact value beside formatted text: the same number, one with
        # grouping for people and one exact string for programs.
        assert cell["value"] == cell["text"].replace(",", "")

        assert invoice_result.isError is False
        assert invoice_result.structuredContent is not None
        assert invoice_result.structuredContent["kind"] == "record"
        assert invoice_result.structuredContent["record_values"]

        assert missing_identity.isError is True

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


def test_hosted_mcp_uses_the_shared_http_request_body_limit() -> None:
    app = _app("sales_clerk", max_request_body_bytes=64)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=BASE_URL,
        ) as client:
            response = await client.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "X-Correlation-ID": "oversized-mcp",
                },
                json={"jsonrpc": "2.0", "method": "x" * 100},
            )

        assert response.status_code == 413
        assert response.json() == {
            "code": "request_too_large",
            "message": "request body exceeds the configured limit",
        }
        assert response.headers["x-correlation-id"] == "oversized-mcp"
        assert TOKEN not in response.text

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


def _app(
    role: str | tuple[str, ...],
    *,
    max_request_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES,
    prepare: object = None,
) -> object:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions) is True
    if prepare is not None:
        prepare(model, repository, records)
    audits = AuditHistoryService(
        model,
        actions.execution_store,
        records,
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
        max_request_body_bytes=max_request_body_bytes,
    )
    hosted = build_runtime_mcp_server(
        RuntimeMcpService(
            model,
            records,
            actions=actions,
            audits=audits,
            reports=ReportService(model, records),
        ),
        authenticator,
        issuer_url=BASE_URL,
        resource_url=MCP_URL,
    )
    mount_runtime_mcp(app, hosted)
    return app


def test_streamable_http_mcp_accepts_the_null_version_assertion() -> None:
    """`expected_version: "null"` asserts a token TIDE never wrote.

    It matches a row whose token is NULL -- the action's write heals it to
    version 1 -- refuses a row that has since been healed, and any other
    string never reaches the service.
    """

    from datetime import date
    from decimal import Decimal

    def legacy(identity: int) -> dict:
        return {
            "id": identity,
            "number": f"LEG-{identity}",
            "invoice_date": date(2026, 7, 20),
            "currency": "EUR",
            "status": "draft",
            "customer": 1,
            "total": Decimal("0.00"),
        }

    def prepare(model: object, repository: object, records: object) -> None:
        repository.seed("sales.Invoice", [legacy(90), legacy(91)])
        clerk = RequestContext(
            Principal("seed:clerk", roles=frozenset({"sales_clerk"})),
            channel=Channel.TUI,
        )
        healed = records.begin_edit("sales.Invoice", 91, clerk)
        healed.set("currency", "USD")
        records.commit(healed, clerk)

    app = _app("sales_clerk", prepare=prepare)

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
                        await session.initialize()
                        # The quoted form is the exact ETag the REST read
                        # answers; the SDK decodes a bare "null" string
                        # argument into an absent value before validation.
                        voided = await session.call_tool(
                            "void_sales_invoice",
                            {
                                "identity": 90,
                                "expected_version": '"null"',
                                "idempotency_key": "null-void-90",
                                "parameters": {"reason": "legacy duplicate"},
                            },
                        )
                        stale = await session.call_tool(
                            "void_sales_invoice",
                            {
                                "identity": 91,
                                "expected_version": '"null"',
                                "idempotency_key": "null-void-91",
                                "parameters": {"reason": "legacy duplicate"},
                            },
                        )
                        garbage = await session.call_tool(
                            "void_sales_invoice",
                            {
                                "identity": 91,
                                "expected_version": "someday",
                                "idempotency_key": "null-void-92",
                                "parameters": {"reason": "legacy duplicate"},
                            },
                        )

        assert voided.isError is not True
        assert voided.structuredContent["record"]["status"] == "cancelled"
        assert voided.structuredContent["record"]["version"] == 1
        assert stale.isError is True
        assert garbage.isError is True

    asyncio.run(exercise())


def test_action_parameters_are_the_tool_schema_and_land_on_the_record() -> None:
    """The declaration becomes the tool's own arguments model.

    A required parameter makes the `parameters` argument itself required,
    so a bare call refuses before it reaches the service; the values travel
    the same door the service types for every transport.
    """

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
                        await session.initialize()
                        tools = await session.list_tools()
                        by_name = {tool.name: tool for tool in tools.tools}
                        void_schema = by_name["void_sales_invoice"].inputSchema
                        assert "parameters" in void_schema.get("required", [])
                        assert "reason" in json.dumps(void_schema)
                        post_schema = by_name["post_sales_invoice"].inputSchema
                        assert "parameters" not in (
                            post_schema.get("required") or []
                        )

                        voided = await session.call_tool(
                            "void_sales_invoice",
                            {
                                "identity": 2,
                                "expected_version": 1,
                                "idempotency_key": "void-with-reason",
                                "parameters": {"reason": "Ordered twice"},
                            },
                        )
                        bare = await session.call_tool(
                            "void_sales_invoice",
                            {
                                "identity": 3,
                                "idempotency_key": "void-without-reason",
                            },
                        )

        assert voided.isError is not True
        record = voided.structuredContent["record"]
        assert record["status"] == "cancelled"
        assert record["cancelled_reason"] == "Ordered twice"
        assert bare.isError is True

    asyncio.run(exercise())


def test_parameter_models_refuse_unknown_parameters() -> None:
    """A typo'd parameter must refuse, not run the report unfiltered.

    The service's own unknown-parameter check never sees a key pydantic
    already dropped, so an extra-ignoring model turned "fromdate" into a
    whole-history report with no error -- while the identical body on REST
    answers ValidationFailed. Generated wire inputs forbid extras
    everywhere else; this model follows the same convention.
    """

    parameters = _parameter_model(
        "report_sales_summary",
        {"from_date": {"type": "date"}},
    )

    assert parameters is not None
    assert parameters.model_validate({"from_date": "2026-01-01"}).from_date == (
        "2026-01-01"
    )
    with pytest.raises(ValidationError):
        parameters.model_validate({"fromdate": "2026-01-01"})
