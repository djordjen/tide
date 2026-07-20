from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tomllib

from tide import compile_project
from tide.cli import main
from tide.development import (
    ApplicationApplyApproval,
    ApplicationApplyService,
    ApplicationGenerationPlan,
)
from tide.mcp.developer import build_developer_mcp_server


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
EXAMPLE = ROOT / "examples" / "ai_generation"
PLAN_PATH = EXAMPLE / "xy_invoicing_plan.json"
CONFIG_PATH = EXAMPLE / "codex-mcp-config.toml"


def test_ai_tutorial_plan_proposes_and_previews_through_developer_mcp() -> None:
    plan = _plan()
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    server_config = config["mcp_servers"]["tide_developer"]
    assert server_config["command"] == "uv"
    assert server_config["args"] == [
        "run",
        "--extra",
        "mcp",
        "tide",
        "mcp",
        "dev",
        "applications/invoicing",
    ]
    assert "tide_propose_application" in server_config["enabled_tools"]
    assert "tide_preview_application" in server_config["enabled_tools"]

    server = build_developer_mcp_server(INVOICING)
    arguments = {"plan": plan.model_dump(mode="json")}

    async def exercise() -> tuple[object, object]:
        return (
            await server.call_tool("tide_propose_application", arguments),
            await server.call_tool("tide_preview_application", arguments),
        )

    proposal_result, preview_result = asyncio.run(exercise())
    proposal = proposal_result[1]
    preview = preview_result[1]

    assert proposal["valid"] is True
    assert proposal["writes_performed"] is False
    assert proposal["application_id"] == "xy-invoicing"
    assert proposal["summary"] == (
        "4 entities, 1 workflows, 1 reports, and 2 roles; 0 semantic errors"
    )
    assert preview["valid"] is True
    assert preview["workspace_writes_performed"] is False
    assert preview["candidate_persisted"] is False
    assert preview["temporary_candidate_deleted"] is True
    assert preview["application_database_accessed"] is False
    assert preview["external_commands_executed"] is False
    assert preview["target_path"] == "applications/xy-invoicing"
    assert len(preview["artifacts"]) == 22
    assert not [check for check in preview["checks"] if check["status"] == "failed"]
    assert "+++ b/applications/xy-invoicing/tide.yaml" in preview["diff"]
    assert not (ROOT / "applications" / "xy-invoicing").exists()


def test_ai_tutorial_plan_applies_only_after_bound_approval(tmp_path: Path) -> None:
    plan = _plan()
    service = ApplicationApplyService(tmp_path)
    preparation = service.prepare(plan)

    assert preparation.ready is True
    assert preparation.writes_performed is False
    assert preparation.approval_prompt is not None
    assert not (tmp_path / "applications").exists()

    result = service.apply(
        plan,
        ApplicationApplyApproval.from_preparation(preparation),
    )
    target = tmp_path / "applications" / "xy-invoicing"
    model = compile_project(target)

    assert result.applied is True
    assert result.receipt_path == "applications/xy-invoicing/.tide-apply.json"
    assert set(model.entities) == {
        "catalog.Product",
        "crm.Company",
        "sales.Invoice",
        "sales.InvoiceLine",
    }
    assert set(model.roles) == {"invoice_creator", "invoice_poster"}
    assert set(model.reports) == {"sales.invoice"}
    assert model.entity("sales.Invoice").actions["post"]["idempotent"] is True
    receipt = json.loads(
        (target / ".tide-apply.json").read_text(encoding="utf-8")
    )
    assert receipt["approval_id"] == preparation.approval_id
    assert receipt["candidate_fingerprint"] == preparation.candidate_fingerprint


def test_ai_tutorial_documented_preview_command_is_no_write(
    tmp_path: Path,
    capsys,
) -> None:
    result = main(
        [
            "app",
            "preview",
            str(PLAN_PATH),
            "--workspace",
            str(tmp_path),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["ready"] is True
    assert output["writes_performed"] is False
    assert output["target_path"] == "applications/xy-invoicing"
    assert output["artifact_count"] == 22
    assert output["approval_prompt"].startswith("APPLY tide-approval-")
    assert not (tmp_path / "applications").exists()


def _plan() -> ApplicationGenerationPlan:
    return ApplicationGenerationPlan.model_validate_json(
        PLAN_PATH.read_text(encoding="utf-8")
    )
