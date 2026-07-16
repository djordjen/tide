from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from tide.cli import main
from tide.mcp.developer import build_developer_mcp_server


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
INVALID = ROOT / "tests" / "fixtures" / "invalid" / "unknown-field-type"


def test_developer_mcp_exposes_project_resources_and_read_only_tools() -> None:
    server = build_developer_mcp_server(INVOICING)

    async def exercise() -> tuple[Any, Any, Any, Any]:
        return (
            await server.list_resources(),
            await server.list_tools(),
            await server.read_resource("tide://developer/entities/sales.Invoice"),
            await server.call_tool("tide_list_entities", {}),
        )

    resources, tools, invoice_resource, entity_result = asyncio.run(exercise())

    uris = {str(resource.uri) for resource in resources}
    assert {
        "tide://developer/project",
        "tide://developer/application",
        "tide://developer/model",
        "tide://developer/entities/sales.Invoice",
        "tide://developer/views/sales.Invoice.edit",
    } <= uris
    names = {tool.name for tool in tools}
    assert names == {
        "tide_validate_project",
        "tide_list_entities",
        "tide_describe_entity",
        "tide_get_resolved_view",
        "tide_preview_openapi",
        "tide_propose_application",
        "tide_preview_application",
    }
    assert all("apply" not in name and "write" not in name for name in names)
    proposal_tool = next(
        tool for tool in tools if tool.name == "tide_propose_application"
    )
    proposal_schema = json.dumps(proposal_tool.inputSchema)
    assert '"discriminator"' in proposal_schema
    assert '"raw_python"' not in proposal_schema
    assert '"source_path"' not in proposal_schema
    assert '"execute"' not in proposal_schema
    preview_tool = next(
        tool for tool in tools if tool.name == "tide_preview_application"
    )
    preview_schema = json.dumps(preview_tool.inputSchema)
    assert '"discriminator"' in preview_schema
    assert '"raw_python"' not in preview_schema
    assert '"source_path"' not in preview_schema
    assert '"execute"' not in preview_schema
    invoice = json.loads(invoice_resource[0].content)
    assert invoice["name"] == "sales.Invoice"
    assert invoice["source_file"] == "models/sales/invoice.yaml"
    assert invoice["writes_performed"] is False
    structured = entity_result[1]
    assert structured["writes_performed"] is False
    assert [item["entity"] for item in structured["entities"]] == [
        "catalog.Product",
        "crm.Customer",
        "sales.Invoice",
        "sales.InvoiceLine",
    ]


def test_developer_mcp_proposes_structured_application_without_writing(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    server = build_developer_mcp_server(INVOICING)
    arguments = {
        "plan": {
            "operations": [
                {
                    "operation": "create_application",
                    "application_id": "simple-app",
                    "name": "Simple App",
                },
                {
                    "operation": "define_entity",
                    "entity": "core.Item",
                    "fields": [
                        {
                            "name": "id",
                            "type": "integer",
                            "primary_key": True,
                        }
                    ],
                },
            ]
        }
    }

    result = asyncio.run(server.call_tool("tide_propose_application", arguments))

    proposal = result[1]
    assert proposal["valid"] is True
    assert proposal["approval_required"] is True
    assert proposal["writes_performed"] is False
    assert proposal["proposal_id"].startswith("tide-plan-")
    assert marker.read_text(encoding="utf-8") == "keep"
    assert tuple(tmp_path.iterdir()) == (marker,)


def test_developer_mcp_previews_compiled_candidate_without_applying(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    server = build_developer_mcp_server(INVOICING)
    arguments = {
        "plan": {
            "operations": [
                {
                    "operation": "create_application",
                    "application_id": "simple-app",
                    "name": "Simple App",
                },
                {
                    "operation": "define_entity",
                    "entity": "core.Item",
                    "fields": [{"name": "id", "type": "integer", "primary_key": True}],
                },
            ]
        }
    }

    result = asyncio.run(server.call_tool("tide_preview_application", arguments))

    preview = result[1]
    assert preview["valid"] is True
    assert preview["workspace_writes_performed"] is False
    assert preview["candidate_persisted"] is False
    assert preview["external_commands_executed"] is False
    assert preview["application_database_accessed"] is False
    assert preview["fixed_template_code_executed"] is False
    assert preview["in_memory_runtime_checks_performed"] is False
    assert preview["temporary_candidate_deleted"] is True
    assert preview["candidate_fingerprint"].startswith("sha256:")
    assert "+++ b/applications/simple-app/tide.yaml" in preview["diff"]
    assert marker.read_text(encoding="utf-8") == "keep"
    assert tuple(tmp_path.iterdir()) == (marker,)


def test_invalid_project_advertises_diagnostics_and_proposals_only() -> None:
    server = build_developer_mcp_server(INVALID)

    async def exercise() -> tuple[Any, Any, Any]:
        return (
            await server.list_resources(),
            await server.list_tools(),
            await server.call_tool("tide_validate_project", {}),
        )

    resources, tools, validation = asyncio.run(exercise())

    assert [str(resource.uri) for resource in resources] == ["tide://developer/project"]
    assert {tool.name for tool in tools} == {
        "tide_validate_project",
        "tide_propose_application",
        "tide_preview_application",
    }
    assert validation[1]["valid"] is False
    assert validation[1]["writes_performed"] is False
    assert {item["code"] for item in validation[1]["diagnostics"]} == {"TIDE103"}


def test_tide_mcp_dev_runs_stdio_without_banner(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    launched: dict[str, Any] = {}

    def fake_run(
        self: FastMCP[Any],
        transport: str = "stdio",
        mount_path: str | None = None,
    ) -> None:
        launched["server"] = self
        launched["transport"] = transport
        launched["mount_path"] = mount_path

    monkeypatch.setattr(FastMCP, "run", fake_run)

    result = main(["mcp", "dev", str(INVOICING)])

    assert result == 0
    assert launched["transport"] == "stdio"
    assert launched["mount_path"] is None
    assert launched["server"].name == "TIDE Developer"
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
