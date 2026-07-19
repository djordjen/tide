from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
import yaml

import tide.development.materialization as materialization
from tide.development import (
    ApplicationGenerationPlan,
    ApplicationGenerationService,
    ApplicationMaterializationService,
)
from tide.reporting import PdfDependencyMissing


def test_invoicing_request_becomes_a_valid_structured_no_write_proposal() -> None:
    service = ApplicationGenerationService()
    plan = _invoicing_plan()

    proposal = service.propose(plan)

    assert proposal.valid is True
    assert proposal.application_id == "xy-invoicing"
    assert proposal.approval_required is True
    assert proposal.writes_performed is False
    assert proposal.summary == (
        "4 entities, 1 workflows, 1 reports, and 2 roles; 0 semantic errors"
    )
    assert proposal.permissions == (
        "catalog.product.read",
        "catalog.product.write",
        "crm.company.audit",
        "crm.company.delete",
        "crm.company.read",
        "crm.company.write",
        "sales.invoice.create",
        "sales.invoice.post",
        "sales.invoice.read",
        "sales.invoice.report",
    )
    assert proposal.issues == ()


def test_generation_proposals_are_deterministic_and_do_not_touch_workspace(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "existing.txt"
    marker.write_text("unchanged", encoding="utf-8")
    before = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))
    service = ApplicationGenerationService()

    first = service.propose(_invoicing_plan())
    second = service.propose(_invoicing_plan())

    after = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))
    assert first.proposal_id == second.proposal_id
    assert first.proposal_id.startswith("tide-plan-")
    assert before == after == (Path("existing.txt"),)
    assert marker.read_text(encoding="utf-8") == "unchanged"


def test_generation_semantics_fail_closed_for_unknown_targets_and_grants() -> None:
    raw = _invoicing_plan().model_dump(mode="json")
    company = raw["operations"][1]
    company["fields"][4]["target"] = "missing.Invoice"
    report = raw["operations"][6]
    report["detail_columns"].append("missing_total")
    creator = raw["operations"][7]
    creator["grants"].append("system.administrator")

    proposal = ApplicationGenerationService().propose(
        ApplicationGenerationPlan.model_validate(raw)
    )

    assert proposal.valid is False
    codes = {issue.code for issue in proposal.issues}
    assert {"TIDEGEN006", "TIDEGEN007", "TIDEGEN016"} <= codes
    assert proposal.writes_performed is False
    assert proposal.approval_required is True


def test_valid_proposal_materializes_compiles_and_is_deleted() -> None:
    preview = ApplicationMaterializationService().preview(_invoicing_plan())

    assert preview.valid is True
    assert preview.workspace_writes_performed is False
    assert preview.candidate_persisted is False
    assert preview.external_commands_executed is False
    assert preview.application_database_accessed is False
    assert preview.fixed_template_code_executed is True
    assert preview.in_memory_runtime_checks_performed is True
    assert preview.temporary_candidate_used is True
    assert preview.temporary_candidate_deleted is True
    assert preview.target_path == "applications/xy-invoicing"
    assert preview.base_fingerprint is not None
    assert preview.candidate_fingerprint is not None
    assert preview.candidate_id is not None
    assert preview.candidate_id.startswith("tide-candidate-")
    assert preview.diagnostics == ()
    paths = {artifact.path for artifact in preview.artifacts}
    assert {
        "actions.py",
        "models/catalog/product.yaml",
        "models/crm/company.yaml",
        "models/sales/invoice.yaml",
        "models/sales/invoice_line.yaml",
        "reports/sales/invoice.yaml",
        "runtime.py",
        "security/policies.yaml",
        "tide.yaml",
        "views/sales/invoice-browse.yaml",
        "views/sales/invoice-edit.yaml",
        "views/sales/invoice_line-inline-edit.yaml",
    } <= paths
    assert len(paths) == 22
    assert not [check for check in preview.checks if check.status == "failed"]
    assert {check.name for check in preview.checks} == {
        "proposal_semantics",
        "candidate_paths",
        "compiler",
        "model_shape",
        "presentation_contract",
        "security_contract",
        "workflow_contract",
        "generator_contract",
        "report_contract",
        "runtime_registration",
        "persistence_integration",
        "crud_integration",
        "action_integration",
        "report_document_integration",
        "html_renderer_integration",
        "pdf_renderer_integration",
        "temporary_cleanup",
    }
    assert all(check.status == "passed" for check in preview.checks)
    assert "--- /dev/null" in preview.diff
    assert "+++ b/applications/xy-invoicing/tide.yaml" in preview.diff
    assert "execute: actions.transition_sales_invoice_post" in preview.diff
    assert "generated_by: actions.generate_sales_invoice_number" in preview.diff
    assert "view: sales.Invoice.browse" in preview.diff


def test_candidate_preview_is_deterministic_and_does_not_touch_workspace(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    service = ApplicationMaterializationService()

    first = service.preview(_invoicing_plan())
    second = service.preview(_invoicing_plan())

    assert first.candidate_id == second.candidate_id
    assert first.candidate_fingerprint == second.candidate_fingerprint
    assert first.base_fingerprint == second.base_fingerprint
    assert first.artifacts == second.artifacts
    assert first.diff == second.diff
    assert marker.read_text(encoding="utf-8") == "keep"
    assert tuple(tmp_path.iterdir()) == (marker,)


def test_invalid_proposal_is_not_materialized() -> None:
    raw = _invoicing_plan().model_dump(mode="json")
    raw["operations"][6]["detail_columns"].append("missing")

    preview = ApplicationMaterializationService().preview(
        ApplicationGenerationPlan.model_validate(raw)
    )

    assert preview.valid is False
    assert preview.temporary_candidate_used is False
    assert preview.temporary_candidate_deleted is True
    assert preview.artifacts == ()
    assert preview.diff == ""
    assert preview.candidate_fingerprint is None
    assert preview.checks[0].name == "proposal_semantics"
    assert preview.checks[0].status == "failed"
    assert "TIDEGEN016" in {issue.code for issue in preview.issues}


def test_candidate_paths_fail_closed_on_case_insensitive_collision() -> None:
    plan = ApplicationGenerationPlan.model_validate(
        {
            "operations": [
                {
                    "operation": "create_application",
                    "application_id": "collision-app",
                    "name": "Collision App",
                },
                {
                    "operation": "define_entity",
                    "entity": "crm.Company",
                    "fields": [{"name": "id", "type": "integer", "primary_key": True}],
                },
                {
                    "operation": "define_entity",
                    "entity": "Crm.Company",
                    "fields": [{"name": "id", "type": "integer", "primary_key": True}],
                },
            ]
        }
    )

    preview = ApplicationMaterializationService().preview(plan)

    assert preview.valid is False
    assert preview.temporary_candidate_used is False
    assert preview.artifacts == ()
    assert preview.checks[-1].name == "candidate_paths"
    assert preview.checks[-1].status == "failed"
    assert "TIDECAND001" in {issue.code for issue in preview.issues}


def test_candidate_returns_relative_compiler_diagnostics_and_skips_contracts() -> None:
    plan = ApplicationGenerationPlan.model_validate(
        {
            "operations": [
                {
                    "operation": "create_application",
                    "application_id": "invalid-display",
                    "name": "Invalid Display",
                },
                {
                    "operation": "define_entity",
                    "entity": "core.Item",
                    "display": "{missing}",
                    "fields": [{"name": "id", "type": "integer", "primary_key": True}],
                },
            ]
        }
    )

    preview = ApplicationMaterializationService().preview(plan)

    assert preview.valid is False
    assert preview.temporary_candidate_deleted is True
    assert {item["code"] for item in preview.diagnostics} == {"TIDE215"}
    assert preview.diagnostics[0]["file"] == "models/core/item.yaml"
    statuses = {check.name: check.status for check in preview.checks}
    assert statuses["compiler"] == "failed"
    assert statuses["model_shape"] == "skipped"
    assert statuses["temporary_cleanup"] == "passed"


def test_candidate_rejects_non_portable_reserved_device_paths() -> None:
    plan = ApplicationGenerationPlan.model_validate(
        {
            "operations": [
                {
                    "operation": "create_application",
                    "application_id": "portable-app",
                    "name": "Portable App",
                },
                {
                    "operation": "define_entity",
                    "entity": "con.Item",
                    "fields": [{"name": "id", "type": "integer", "primary_key": True}],
                },
            ]
        }
    )

    preview = ApplicationMaterializationService().preview(plan)

    assert preview.valid is False
    assert preview.temporary_candidate_used is False
    assert "reserved device name" in preview.checks[-1].message


def test_sequence_template_requires_local_date_and_rejects_source_injection() -> None:
    raw = _invoicing_plan().model_dump(mode="json")
    invoice = raw["operations"][3]
    invoice["fields"][1]["sequence"]["date_field"] = "status"

    proposal = ApplicationGenerationService().propose(
        ApplicationGenerationPlan.model_validate(raw)
    )

    assert proposal.valid is False
    assert "TIDEGEN017" in {issue.code for issue in proposal.issues}

    raw["operations"][3]["fields"][1]["sequence"]["prefix"] = "INV\nraise"
    with pytest.raises(ValidationError, match="control characters"):
        ApplicationGenerationPlan.model_validate(raw)


def test_runnable_preview_uses_only_fixed_generated_templates() -> None:
    preview = ApplicationMaterializationService().preview(_invoicing_plan())
    artifacts = {artifact.path: artifact.content for artifact in preview.artifacts}

    assert "def generate_sales_invoice_number(" in artifacts["actions.py"]
    assert "datetime.now(timezone.utc)" in artifacts["actions.py"]
    assert "payload.get('occurred_at')" not in artifacts["actions.py"]
    assert "records.register_generator(" in artifacts["runtime.py"]
    assert "actions.register(" in artifacts["runtime.py"]
    checks = {check.name: check for check in preview.checks}
    assert checks["persistence_integration"].status == "passed"
    assert "authorization denial" in checks["persistence_integration"].message
    assert checks["action_integration"].status == "passed"
    assert "idempotency" in checks["action_integration"].message
    assert checks["report_document_integration"].status == "passed"
    assert checks["html_renderer_integration"].status == "passed"
    assert checks["pdf_renderer_integration"].status == "passed"

    inline = yaml.safe_load(artifacts["views/sales/invoice_line-inline-edit.yaml"])
    assert inline["columns"] == [
        "product",
        "description",
        "quantity",
        "unit_price",
        "total",
    ]
    assert inline["layout"][0]["rows"] == [
        ["product", "quantity"],
        ["description", "unit_price"],
    ]
    browse = yaml.safe_load(artifacts["views/sales/invoice-browse.yaml"])
    assert browse["settings"]["default"] is True


def test_missing_optional_pdf_renderer_is_a_visible_skip(
    monkeypatch: Any,
) -> None:
    def missing_pdf(_document: Any) -> bytes:
        raise PdfDependencyMissing

    monkeypatch.setattr(materialization, "render_pdf", missing_pdf)

    preview = ApplicationMaterializationService().preview(_invoicing_plan())

    checks = {check.name: check for check in preview.checks}
    assert preview.valid is True
    assert checks["pdf_renderer_integration"].status == "skipped"
    assert "optional report extra" in checks["pdf_renderer_integration"].message


def test_large_candidate_runtime_smoke_is_bounded_and_visible() -> None:
    operations: list[dict[str, Any]] = [
        {
            "operation": "create_application",
            "application_id": "bounded-app",
            "name": "Bounded App",
        }
    ]
    operations.extend(
        {
            "operation": "define_entity",
            "entity": f"core.Item{index}",
            "fields": [
                {"name": "id", "type": "integer", "primary_key": True},
                {"name": "name", "type": "string", "required": True},
            ],
            "list_permission": "core.record.access",
            "read_permission": "core.record.access",
            "create_permission": "core.record.access",
            "update_permission": "core.record.access",
        }
        for index in range(26)
    )

    preview = ApplicationMaterializationService().preview(
        ApplicationGenerationPlan.model_validate({"operations": operations})
    )

    checks = {check.name: check for check in preview.checks}
    assert preview.valid is True
    assert preview.in_memory_runtime_checks_performed is False
    assert checks["persistence_integration"].status == "skipped"
    assert "bounded preview limit of 25" in checks["persistence_integration"].message
    assert "skipped checks" in preview.summary


def _invoicing_plan() -> ApplicationGenerationPlan:
    return ApplicationGenerationPlan.model_validate(
        {
            "operations": [
                {
                    "operation": "create_application",
                    "application_id": "xy-invoicing",
                    "name": "XY Company Invoicing",
                },
                {
                    "operation": "define_entity",
                    "entity": "crm.Company",
                    "label": "Companies",
                    "display": "{code} - {name}",
                    "fields": [
                        {"name": "id", "type": "integer", "primary_key": True},
                        {
                            "name": "code",
                            "type": "string",
                            "required": True,
                            "unique": True,
                            "length": 20,
                        },
                        {
                            "name": "name",
                            "type": "string",
                            "required": True,
                            "length": 120,
                        },
                        {"name": "email", "type": "string", "length": 254},
                        {
                            "name": "invoices",
                            "type": "collection",
                            "target": "sales.Invoice",
                            "inverse": "company",
                        },
                    ],
                    "list_permission": "crm.company.read",
                    "read_permission": "crm.company.read",
                    "create_permission": "crm.company.write",
                    "update_permission": "crm.company.write",
                    "delete_permission": "crm.company.delete",
                    "audit_permission": "crm.company.audit",
                    "expose_rest": ["list", "get", "create", "update", "delete"],
                    "expose_mcp": [
                        "schema",
                        "record",
                        "audit",
                        "search",
                        "create",
                        "update",
                        "delete",
                    ],
                },
                {
                    "operation": "define_entity",
                    "entity": "catalog.Product",
                    "label": "Products",
                    "display": "{code} - {name}",
                    "fields": [
                        {"name": "id", "type": "integer", "primary_key": True},
                        {
                            "name": "code",
                            "type": "string",
                            "required": True,
                            "unique": True,
                            "length": 30,
                        },
                        {
                            "name": "name",
                            "type": "string",
                            "required": True,
                            "length": 120,
                        },
                        {
                            "name": "unit_price",
                            "type": "decimal",
                            "required": True,
                            "precision": 12,
                            "scale": 2,
                        },
                        {
                            "name": "invoice_lines",
                            "type": "collection",
                            "target": "sales.InvoiceLine",
                            "inverse": "product",
                        },
                    ],
                    "list_permission": "catalog.product.read",
                    "read_permission": "catalog.product.read",
                    "create_permission": "catalog.product.write",
                    "update_permission": "catalog.product.write",
                    "expose_rest": ["list", "get", "create", "update"],
                    "expose_mcp": [
                        "schema",
                        "record",
                        "search",
                        "create",
                        "update",
                    ],
                },
                {
                    "operation": "define_entity",
                    "entity": "sales.Invoice",
                    "label": "Invoices",
                    "display": "number",
                    "fields": [
                        {"name": "id", "type": "integer", "primary_key": True},
                        {
                            "name": "number",
                            "type": "string",
                            "required": True,
                            "unique": True,
                            "readonly": True,
                            "length": 30,
                            "sequence": {
                                "prefix": "INV",
                                "date_field": "invoice_date",
                                "width": 6,
                            },
                        },
                        {
                            "name": "invoice_date",
                            "type": "date",
                            "required": True,
                            "default_factory": "today",
                        },
                        {
                            "name": "company",
                            "type": "reference",
                            "target": "crm.Company",
                            "inverse": "invoices",
                            "required": True,
                            "on_delete": "restrict",
                        },
                        {
                            "name": "status",
                            "type": "choice",
                            "choices": ["draft", "posted"],
                            "default": "draft",
                            "readonly": True,
                        },
                        {"name": "posted_at", "type": "datetime", "readonly": True},
                        {
                            "name": "posted_by",
                            "type": "string",
                            "readonly": True,
                            "length": 120,
                        },
                        {
                            "name": "lines",
                            "type": "collection",
                            "target": "sales.InvoiceLine",
                            "inverse": "invoice",
                            "cascade": ["create", "update"],
                            "orphan_delete": True,
                        },
                        {
                            "name": "total",
                            "type": "decimal",
                            "precision": 12,
                            "scale": 2,
                            "readonly": True,
                            "computed_expression": "sum(lines.total)",
                        },
                    ],
                    "list_permission": "sales.invoice.read",
                    "read_permission": "sales.invoice.read",
                    "create_permission": "sales.invoice.create",
                    "expose_rest": ["list", "get", "create"],
                    "expose_mcp": ["schema", "record", "search", "create"],
                },
                {
                    "operation": "define_entity",
                    "entity": "sales.InvoiceLine",
                    "label": "Invoice Lines",
                    "fields": [
                        {"name": "id", "type": "integer", "primary_key": True},
                        {
                            "name": "invoice",
                            "type": "reference",
                            "target": "sales.Invoice",
                            "inverse": "lines",
                            "required": True,
                            "on_delete": "cascade",
                        },
                        {
                            "name": "product",
                            "type": "reference",
                            "target": "catalog.Product",
                            "inverse": "invoice_lines",
                            "required": True,
                            "on_delete": "restrict",
                        },
                        {
                            "name": "description",
                            "type": "string",
                            "required": True,
                            "length": 200,
                        },
                        {
                            "name": "quantity",
                            "type": "decimal",
                            "required": True,
                            "precision": 12,
                            "scale": 3,
                        },
                        {
                            "name": "unit_price",
                            "type": "decimal",
                            "required": True,
                            "precision": 12,
                            "scale": 2,
                        },
                        {
                            "name": "total",
                            "type": "decimal",
                            "precision": 12,
                            "scale": 2,
                            "readonly": True,
                            "computed_expression": "round(quantity * unit_price, 2)",
                        },
                    ],
                    "list_permission": "sales.invoice.read",
                    "read_permission": "sales.invoice.read",
                },
                {
                    "operation": "define_state_transition",
                    "entity": "sales.Invoice",
                    "action": "post",
                    "label": "Post invoice",
                    "state_field": "status",
                    "from_values": ["draft"],
                    "to_value": "posted",
                    "permission": "sales.invoice.post",
                    "requires_collection": "lines",
                    "stamp_datetime_field": "posted_at",
                    "stamp_principal_field": "posted_by",
                    "expose_rest": True,
                    "expose_mcp": True,
                },
                {
                    "operation": "define_record_report",
                    "report": "sales.invoice",
                    "title": "Invoice",
                    "entity": "sales.Invoice",
                    "permission": "sales.invoice.report",
                    "header_fields": ["number", "invoice_date", "company", "status"],
                    "detail_collection": "lines",
                    "detail_columns": [
                        "product",
                        "description",
                        "quantity",
                        "unit_price",
                        "total",
                    ],
                    "footer_fields": ["total"],
                    "expose_rest": True,
                    "pdf_enabled": True,
                },
                {
                    "operation": "define_role",
                    "role": "invoice_creator",
                    "grants": [
                        "crm.company.read",
                        "crm.company.write",
                        "crm.company.delete",
                        "crm.company.audit",
                        "catalog.product.read",
                        "catalog.product.write",
                        "sales.invoice.read",
                        "sales.invoice.create",
                        "sales.invoice.report",
                    ],
                },
                {
                    "operation": "define_role",
                    "role": "invoice_poster",
                    "grants": [
                        "crm.company.read",
                        "catalog.product.read",
                        "sales.invoice.read",
                        "sales.invoice.post",
                        "sales.invoice.report",
                    ],
                },
            ]
        }
    )
