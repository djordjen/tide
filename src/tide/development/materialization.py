"""Deterministic, isolated previews for structured application proposals."""

from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from decimal import Decimal
from difflib import unified_diff
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic_core import to_jsonable_python
import yaml

from tide.compiler.compiler import compile_project
from tide.compiler.normalized import ApplicationModel
from tide.data import InMemoryRepository, QuerySpec
from tide.development.generation import (
    ApplicationGenerationPlan,
    ApplicationGenerationProposal,
    ApplicationGenerationService,
    CreateApplicationOperation,
    DefineEntityOperation,
    DefineRecordReportOperation,
    DefineRoleOperation,
    DefineStateTransitionOperation,
    GenerationIssue,
    PlannedField,
)
from tide.diagnostics import CompilationFailed
from tide.reporting import PdfDependencyMissing, ReportService, render_html, render_pdf
from tide.runtime import AuthorizationError, Channel, Principal, RequestContext
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService


MAX_INTEGRATION_ENTITIES = 25
MAX_INTEGRATION_TRANSITIONS = 20
MAX_INTEGRATION_REPORTS = 10
MAX_SYNTHETIC_RELATIONSHIP_DEPTH = 4


class CandidateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateArtifact(CandidateModel):
    """One exact source artifact in the proposed application tree."""

    path: str
    sha256: str
    size_bytes: int
    content: str


class CandidateCheck(CandidateModel):
    """One bounded static or isolated-runtime candidate verification result."""

    name: str
    status: Literal["passed", "failed", "skipped"]
    message: str


class ApplicationGenerationPreview(CandidateModel):
    """An exact, ephemeral candidate bound to its proposal and empty base."""

    proposal_id: str
    application_id: str | None = None
    valid: bool
    approval_required: Literal[True] = True
    workspace_writes_performed: Literal[False] = False
    candidate_persisted: Literal[False] = False
    external_commands_executed: Literal[False] = False
    application_database_accessed: Literal[False] = False
    fixed_template_code_executed: bool = False
    in_memory_runtime_checks_performed: bool = False
    temporary_candidate_used: bool
    temporary_candidate_deleted: bool
    target_path: str | None = None
    base_fingerprint: str | None = None
    candidate_id: str | None = None
    candidate_fingerprint: str | None = None
    summary: str
    artifacts: tuple[CandidateArtifact, ...] = ()
    diff: str = ""
    checks: tuple[CandidateCheck, ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()
    issues: tuple[GenerationIssue, ...] = ()


class ApplicationMaterializationService:
    """Render and compile a new application only inside a temporary directory."""

    def __init__(
        self,
        generation: ApplicationGenerationService | None = None,
    ) -> None:
        self.generation = generation or ApplicationGenerationService()

    def preview(self, plan: ApplicationGenerationPlan) -> ApplicationGenerationPreview:
        proposal = self.generation.propose(plan)
        application_id = proposal.application_id
        target_path = (
            f"applications/{application_id}" if application_id is not None else None
        )
        base_fingerprint = (
            _fingerprint_empty_base(target_path) if target_path is not None else None
        )
        if not proposal.valid or application_id is None:
            return ApplicationGenerationPreview(
                proposal_id=proposal.proposal_id,
                application_id=application_id,
                valid=False,
                temporary_candidate_used=False,
                temporary_candidate_deleted=True,
                target_path=target_path,
                base_fingerprint=base_fingerprint,
                summary="proposal validation failed; no candidate was materialized",
                checks=(
                    CandidateCheck(
                        name="proposal_semantics",
                        status="failed",
                        message="fix the structured proposal errors before previewing",
                    ),
                ),
                issues=proposal.issues,
            )

        try:
            rendered = _render_candidate(plan, proposal)
        except ValueError as error:
            issue = GenerationIssue(
                severity="error",
                code="TIDECAND001",
                message=str(error),
            )
            return ApplicationGenerationPreview(
                proposal_id=proposal.proposal_id,
                application_id=application_id,
                valid=False,
                temporary_candidate_used=False,
                temporary_candidate_deleted=True,
                target_path=target_path,
                base_fingerprint=base_fingerprint,
                summary="candidate source rendering failed",
                checks=(
                    CandidateCheck(
                        name="proposal_semantics",
                        status="passed",
                        message="structured proposal is semantically valid",
                    ),
                    CandidateCheck(
                        name="candidate_paths",
                        status="failed",
                        message=str(error),
                    ),
                ),
                issues=(*proposal.issues, issue),
            )

        artifacts = tuple(
            CandidateArtifact(
                path=path,
                sha256=_content_hash(content),
                size_bytes=len(content.encode("utf-8")),
                content=content,
            )
            for path, content in rendered.items()
        )
        candidate_fingerprint = _fingerprint_artifacts(artifacts)
        candidate_id = (
            "tide-candidate-" + candidate_fingerprint.removeprefix("sha256:")[:24]
        )
        exact_diff = _new_tree_diff(target_path, artifacts)
        checks: list[CandidateCheck] = [
            CandidateCheck(
                name="proposal_semantics",
                status="passed",
                message="structured proposal is semantically valid",
            ),
            CandidateCheck(
                name="candidate_paths",
                status="passed",
                message=f"{len(artifacts)} deterministic relative paths are collision-free",
            ),
        ]
        diagnostics: tuple[dict[str, Any], ...] = ()
        model: ApplicationModel | None = None
        temporary_root: Path | None = None
        with TemporaryDirectory(prefix="tide-candidate-") as temporary:
            temporary_root = Path(temporary) / application_id
            temporary_root.mkdir()
            _write_candidate(temporary_root, artifacts)
            try:
                model = compile_project(temporary_root)
            except CompilationFailed as error:
                diagnostics = tuple(
                    diagnostic.as_dict(root=temporary_root)
                    for diagnostic in error.diagnostics
                )
                checks.append(
                    CandidateCheck(
                        name="compiler",
                        status="failed",
                        message=f"compiler returned {len(diagnostics)} diagnostic(s)",
                    )
                )
            else:
                diagnostics = tuple(
                    diagnostic.as_dict(root=temporary_root)
                    for diagnostic in model.diagnostics
                )
                checks.append(
                    CandidateCheck(
                        name="compiler",
                        status="passed",
                        message=(
                            f"compiled {len(model.entities)} entities, "
                            f"{len(model.reports)} reports, and {len(model.roles)} roles; "
                            f"{len(diagnostics)} warning(s)"
                        ),
                    )
                )
                static_checks = _verify_candidate(model, plan, rendered)
                checks.extend(static_checks)
                if all(check.status == "passed" for check in static_checks):
                    checks.extend(
                        _verify_runnable_candidate(
                            model,
                            plan,
                        )
                    )
                else:
                    checks.extend(_skipped_runnable_checks("static checks failed"))

        temporary_deleted = bool(
            temporary_root is not None and not temporary_root.parent.exists()
        )
        if model is None:
            checks.extend(_skipped_model_checks())
        if not temporary_deleted:
            checks.append(
                CandidateCheck(
                    name="temporary_cleanup",
                    status="failed",
                    message="temporary candidate cleanup could not be confirmed",
                )
            )
        else:
            checks.append(
                CandidateCheck(
                    name="temporary_cleanup",
                    status="passed",
                    message="the isolated candidate tree was deleted",
                )
            )
        valid = not any(check.status == "failed" for check in checks)
        failed = sum(check.status == "failed" for check in checks)
        skipped = sum(check.status == "skipped" for check in checks)
        runtime_check = next(
            (check for check in checks if check.name == "runtime_registration"),
            None,
        )
        persistence_check = next(
            (check for check in checks if check.name == "persistence_integration"),
            None,
        )
        fixed_template_executed = bool(
            model is not None
            and runtime_check is not None
            and runtime_check.status != "skipped"
            and any(
                isinstance(operation, DefineStateTransitionOperation)
                or (
                    isinstance(operation, DefineEntityOperation)
                    and any(field.sequence is not None for field in operation.fields)
                )
                for operation in plan.operations
            )
        )
        return ApplicationGenerationPreview(
            proposal_id=proposal.proposal_id,
            application_id=application_id,
            valid=valid,
            fixed_template_code_executed=fixed_template_executed,
            in_memory_runtime_checks_performed=bool(
                persistence_check is not None and persistence_check.status != "skipped"
            ),
            temporary_candidate_used=True,
            temporary_candidate_deleted=temporary_deleted,
            target_path=target_path,
            base_fingerprint=base_fingerprint,
            candidate_id=candidate_id,
            candidate_fingerprint=candidate_fingerprint,
            summary=(
                f"{len(artifacts)} artifacts rendered and checked; "
                f"{failed} verification failures and {skipped} skipped checks"
            ),
            artifacts=artifacts,
            diff=exact_diff,
            checks=tuple(checks),
            diagnostics=diagnostics,
            issues=proposal.issues,
        )


def _render_candidate(
    plan: ApplicationGenerationPlan,
    proposal: ApplicationGenerationProposal,
) -> dict[str, str]:
    application = next(
        operation
        for operation in plan.operations
        if isinstance(operation, CreateApplicationOperation)
    )
    entities = sorted(
        (
            operation
            for operation in plan.operations
            if isinstance(operation, DefineEntityOperation)
        ),
        key=lambda operation: operation.entity,
    )
    transitions = sorted(
        (
            operation
            for operation in plan.operations
            if isinstance(operation, DefineStateTransitionOperation)
        ),
        key=lambda operation: (operation.entity, operation.action),
    )
    reports = sorted(
        (
            operation
            for operation in plan.operations
            if isinstance(operation, DefineRecordReportOperation)
        ),
        key=lambda operation: operation.report,
    )
    roles = sorted(
        (
            operation
            for operation in plan.operations
            if isinstance(operation, DefineRoleOperation)
        ),
        key=lambda operation: operation.role,
    )
    sequence_fields = [
        (entity, field)
        for entity in entities
        for field in entity.fields
        if field.sequence is not None
    ]
    transitions_by_entity: dict[str, list[DefineStateTransitionOperation]] = {}
    for transition in transitions:
        transitions_by_entity.setdefault(transition.entity, []).append(transition)

    manifest: dict[str, Any] = {
        "schema_version": "0.1",
        "application": {"name": application.name, "version": application.version},
        "database": {"mode": application.database_mode},
        "model": {"paths": ["models"]},
    }
    if any(entity.expose_tui for entity in entities) or any(
        field.type == "collection"
        and any(item in {"create", "update"} for item in field.cascade)
        for entity in entities
        for field in entity.fields
    ):
        manifest["views"] = {"paths": ["views"]}
    if reports:
        manifest["reports"] = {"paths": ["reports"]}
    if proposal.permissions or roles:
        manifest["security"] = {"paths": ["security"]}

    rendered: dict[str, str] = {"tide.yaml": _yaml_document(manifest)}
    entity_index = {entity.entity: entity for entity in entities}
    for entity in entities:
        path = _logical_path("models", entity.entity)
        _add_artifact(
            rendered,
            path,
            _yaml_document(
                _entity_document(entity, transitions_by_entity.get(entity.entity, []))
            ),
        )
    default_view_entity = _default_view_entity(entities, transitions, reports)
    for path, document in _view_documents(
        entities,
        default_view_entity=default_view_entity,
    ):
        _add_artifact(rendered, path, _yaml_document(document))
    for report in reports:
        path = _logical_path("reports", report.report)
        _add_artifact(
            rendered,
            path,
            _yaml_document(_report_document(report, entity_index)),
        )
    if proposal.permissions or roles:
        _add_artifact(
            rendered,
            "security/policies.yaml",
            _yaml_document(_security_document(proposal.permissions, roles)),
        )
    if transitions or sequence_fields:
        _add_artifact(
            rendered,
            "actions.py",
            _actions_module(transitions, sequence_fields),
        )
        _add_artifact(
            rendered,
            "runtime.py",
            _runtime_module(transitions, sequence_fields),
        )
    return dict(sorted(rendered.items()))


def _entity_document(
    entity: DefineEntityOperation,
    transitions: list[DefineStateTransitionOperation],
) -> dict[str, Any]:
    document: dict[str, Any] = {"entity": entity.entity}
    if entity.label is not None:
        document["label"] = entity.label
    if entity.display is not None:
        document["display"] = entity.display
    exposure: dict[str, Any] = {"tui": entity.expose_tui}
    if entity.expose_rest:
        exposure["rest"] = {"operations": list(entity.expose_rest)}
    if entity.expose_mcp:
        exposure["mcp"] = {
            "resources": [
                item for item in entity.expose_mcp if item in {"schema", "record"}
            ],
            "tools": [item for item in entity.expose_mcp if item == "search"],
        }
    document["expose"] = exposure
    permissions = {
        key: value
        for key, value in (
            ("list", entity.list_permission),
            ("read", entity.read_permission),
            ("create", entity.create_permission),
            ("update", entity.update_permission),
        )
        if value is not None
    }
    if permissions:
        document["permissions"] = permissions
    action_owned = {
        name
        for transition in transitions
        for name in (
            transition.state_field,
            transition.stamp_datetime_field,
            transition.stamp_principal_field,
        )
        if name is not None
    }
    document["fields"] = {
        field.name: _field_document(
            entity.entity,
            field,
            field.name in action_owned,
        )
        for field in entity.fields
    }
    if transitions:
        document["actions"] = {
            transition.action: _action_document(transition)
            for transition in transitions
        }
    return document


def _field_document(
    entity_name: str,
    field: PlannedField,
    action_owned: bool,
) -> dict[str, Any]:
    document: dict[str, Any] = {"type": field.type}
    optional = (
        ("label", field.label),
        ("length", field.length),
        ("precision", field.precision),
        ("scale", field.scale),
        ("target", field.target),
        ("inverse", field.inverse),
        ("on_delete", field.on_delete),
    )
    for key, value in optional:
        if value is not None:
            document[key] = value
    for key, value in (
        ("primary_key", field.primary_key),
        ("required", field.required),
        ("unique", field.unique),
        ("readonly", field.readonly or action_owned),
        ("orphan_delete", field.orphan_delete),
    ):
        if value:
            document[key] = value
    if field.choices:
        document["choices"] = list(field.choices)
    if field.cascade:
        document["cascade"] = list(field.cascade)
    if field.default is not None:
        document["default"] = to_jsonable_python(field.default)
    if field.default_factory is not None:
        document["default_factory"] = field.default_factory
    if field.computed_expression is not None:
        document["computed"] = {
            "expression": field.computed_expression,
            "materialization": field.computed_materialization,
        }
    if field.sequence is not None:
        document["generated_by"] = _generator_reference(entity_name, field.name)
        document["write"] = "system"
    if action_owned:
        document["write"] = "action_only"
    return document


def _default_view_entity(
    entities: list[DefineEntityOperation],
    transitions: list[DefineStateTransitionOperation],
    reports: list[DefineRecordReportOperation],
) -> str | None:
    exposed = {entity.entity for entity in entities if entity.expose_tui}
    for candidate in (
        *(transition.entity for transition in transitions),
        *(report.entity for report in reports),
        *(entity.entity for entity in entities),
    ):
        if candidate in exposed:
            return candidate
    return None


def _view_documents(
    entities: list[DefineEntityOperation],
    *,
    default_view_entity: str | None,
) -> list[tuple[str, dict[str, Any]]]:
    documents: list[tuple[str, dict[str, Any]]] = []
    inline_targets: dict[str, set[str]] = {}
    for entity in entities:
        for field in entity.fields:
            if (
                field.type == "collection"
                and field.target is not None
                and any(item in {"create", "update"} for item in field.cascade)
            ):
                if field.inverse is not None:
                    inline_targets.setdefault(field.target, set()).add(field.inverse)
                else:
                    inline_targets.setdefault(field.target, set())

    for entity in entities:
        if not entity.expose_tui:
            continue
        scalar_fields = [
            field
            for field in entity.fields
            if field.type != "collection" and not field.primary_key
        ]
        columns = [field.name for field in scalar_fields]
        if not columns:
            columns = [field.name for field in entity.fields if field.primary_key]
        search = [
            field.name
            for field in scalar_fields
            if field.type in {"string", "choice"} and field.computed_expression is None
        ]
        browse: dict[str, Any] = {
            "view": f"{entity.entity}.browse",
            "entity": entity.entity,
            "kind": "browse",
            "columns": columns,
            "search": search,
        }
        if entity.entity == default_view_entity:
            browse["settings"] = {"default": True}
        documents.append((_view_path(entity.entity, "browse"), browse))

        form_fields = [field.name for field in scalar_fields]
        layout: list[dict[str, Any]] = []
        if form_fields:
            layout.append(
                {
                    "group": entity.label or _humanize_name(entity.entity),
                    "rows": _paired_rows(form_fields),
                }
            )
        for field in entity.fields:
            if (
                field.type == "collection"
                and field.target in inline_targets
                and any(item in {"create", "update"} for item in field.cascade)
            ):
                layout.append(
                    {
                        "collection": field.name,
                        "view": f"{field.target}.inline_edit",
                    }
                )
        documents.append(
            (
                _view_path(entity.entity, "edit"),
                {
                    "view": f"{entity.entity}.edit",
                    "entity": entity.entity,
                    "kind": "form",
                    "layout": layout,
                },
            )
        )
        documents.append(
            (
                _view_path(entity.entity, "lookup"),
                {
                    "view": f"{entity.entity}.lookup",
                    "entity": entity.entity,
                    "kind": "lookup",
                    "columns": columns,
                    "search": search,
                },
            )
        )

    entity_index = {entity.entity: entity for entity in entities}
    for entity_name, excluded in sorted(inline_targets.items()):
        entity = entity_index[entity_name]
        columns = [
            field.name
            for field in entity.fields
            if not field.primary_key
            and field.type != "collection"
            and field.name not in excluded
        ]
        editable = [
            field.name
            for field in entity.fields
            if field.name in columns
            and not field.readonly
            and field.computed_expression is None
        ]
        inline: dict[str, Any] = {
            "view": f"{entity_name}.inline_edit",
            "entity": entity_name,
            "kind": "inline_edit",
            "columns": columns,
        }
        if editable:
            inline["layout"] = [
                {
                    "group": entity.label or _humanize_name(entity.entity),
                    "rows": _paired_rows(editable),
                }
            ]
        documents.append((_view_path(entity_name, "inline-edit"), inline))
    return sorted(documents, key=lambda item: item[0])


def _paired_rows(fields: list[str]) -> list[list[str]]:
    midpoint = (len(fields) + 1) // 2
    left = fields[:midpoint]
    right = fields[midpoint:]
    return [
        [left[index], right[index]] if index < len(right) else [left[index]]
        for index in range(len(left))
    ]


def _view_path(entity_name: str, suffix: str) -> str:
    *namespace, leaf = entity_name.split(".")
    return PurePosixPath(
        "views",
        *(_snake(part) for part in namespace),
        f"{_snake(leaf)}-{suffix}.yaml",
    ).as_posix()


def _humanize_name(entity_name: str) -> str:
    leaf = entity_name.rsplit(".", 1)[-1]
    return " ".join(
        part.capitalize() for part in re.sub(r"(?<!^)(?=[A-Z])", " ", leaf).split()
    )


def _action_document(transition: DefineStateTransitionOperation) -> dict[str, Any]:
    conditions = [
        (
            f"{transition.state_field} == {transition.from_values[0]!r}"
            if len(transition.from_values) == 1
            else f"{transition.state_field} in {list(transition.from_values)!r}"
        )
    ]
    if transition.requires_collection is not None:
        conditions.append(f"count({transition.requires_collection}) > 0")
    return {
        "label": transition.label,
        "enabled_when": " and ".join(conditions),
        "permission": transition.permission,
        "execute": _handler_reference(transition),
        "expose": {
            "rest": transition.expose_rest,
            "mcp": transition.expose_mcp,
        },
        "idempotent": transition.idempotent,
    }


def _report_document(
    report: DefineRecordReportOperation,
    entities: dict[str, DefineEntityOperation],
) -> dict[str, Any]:
    entity = entities[report.entity]
    primary_key = next(field for field in entity.fields if field.primary_key)
    parameter = f"{_snake(entity.entity.rsplit('.', 1)[-1])}_{primary_key.name}"
    parameter_type = "string" if primary_key.type == "choice" else primary_key.type
    bands: dict[str, Any] = {
        "report_header": [{"text": report.title}],
        "record_header": [{"field": name} for name in report.header_fields],
        "detail": {
            "source": report.detail_collection,
            "columns": list(report.detail_columns),
        },
    }
    if report.footer_fields:
        bands["report_footer"] = [{"field": name} for name in report.footer_fields]
    bands["page_footer"] = [{"expression": "'Page ' + page_number"}]
    return {
        "report": report.report,
        "title": report.title,
        "entity": report.entity,
        "permission": report.permission,
        "expose": {"rest": report.expose_rest, "mcp": report.expose_mcp},
        "parameters": {
            parameter: {"type": parameter_type, "required": True},
        },
        "query": {"criteria": f"{primary_key.name} == ${parameter}"},
        "bands": bands,
    }


def _security_document(
    permissions: tuple[str, ...],
    roles: list[DefineRoleOperation],
) -> dict[str, Any]:
    return {
        "permissions": list(permissions),
        "roles": {role.role: {"grants": list(role.grants)} for role in roles},
    }


def _actions_module(
    transitions: list[DefineStateTransitionOperation],
    sequence_fields: list[tuple[DefineEntityOperation, PlannedField]],
) -> str:
    sections = [
        '"""TIDE-owned state-transition handlers generated from structured operations."""',
        "",
        "from __future__ import annotations",
        "",
        "from datetime import datetime, timezone",
        "from typing import Any, Mapping, MutableMapping",
        "",
        "",
        "class StateTransitionError(ValueError):",
        '    """The record cannot make the requested generated transition."""',
    ]
    for entity, field in sequence_fields:
        assert field.sequence is not None
        function = _generator_function(entity.entity, field.name)
        sections.extend(
            [
                "",
                "",
                f"def {function}(",
                "    values: MutableMapping[str, Any],",
                "    context: Any,",
                "    repository: Any,",
                ") -> str:",
                "    del context",
                f"    sequence = repository.peek_next_identity({entity.entity!r})",
                "    parts: list[str] = []",
            ]
        )
        if field.sequence.prefix:
            sections.append(f"    parts.append({field.sequence.prefix!r})")
        if field.sequence.date_field is not None:
            sections.extend(
                [
                    f"    date_value = values.get({field.sequence.date_field!r})",
                    "    if date_value is None or not hasattr(date_value, 'year'):",
                    "        raise ValueError('sequence date field must contain a date')",
                    "    parts.append(str(date_value.year))",
                ]
            )
        sections.extend(
            [
                f"    parts.append(f'{{sequence:0{field.sequence.width}d}}')",
                f"    return {field.sequence.separator!r}.join(parts)",
            ]
        )
    for transition in transitions:
        function = _handler_function(transition)
        sections.extend(
            [
                "",
                "",
                f"def {function}(",
                "    record: MutableMapping[str, Any],",
                "    context: Any,",
                "    payload: Mapping[str, Any],",
                ") -> MutableMapping[str, Any]:",
                f"    current = record.get({transition.state_field!r})",
            ]
        )
        if transition.idempotent:
            sections.extend(
                [
                    f"    if current == {transition.to_value!r}:",
                    "        return record",
                ]
            )
        sections.extend(
            [
                f"    if current not in {transition.from_values!r}:",
                "        raise StateTransitionError(",
                f"            {('invalid state for ' + transition.entity + '.' + transition.action)!r}",
                "        )",
            ]
        )
        if transition.requires_collection is not None:
            sections.extend(
                [
                    f"    if not record.get({transition.requires_collection!r}):",
                    "        raise StateTransitionError(",
                    f"            {('transition requires ' + transition.requires_collection)!r}",
                    "        )",
                ]
            )
        sections.append(
            f"    record[{transition.state_field!r}] = {transition.to_value!r}"
        )
        if transition.stamp_datetime_field is not None:
            sections.extend(
                [
                    f"    record[{transition.stamp_datetime_field!r}] = "
                    "datetime.now(timezone.utc)",
                ]
            )
        if transition.stamp_principal_field is not None:
            sections.append(
                f"    record[{transition.stamp_principal_field!r}] = "
                "context.principal.identifier"
            )
        sections.append("    return record")
    return "\n".join(sections) + "\n"


def _runtime_module(
    transitions: list[DefineStateTransitionOperation],
    sequence_fields: list[tuple[DefineEntityOperation, PlannedField]],
) -> str:
    action_registrations = [
        f"    actions.register({_handler_reference(item)!r}, generated.{_handler_function(item)})"
        for item in transitions
    ]
    generator_registrations = [
        "    records.register_generator("
        f"{_generator_reference(entity.entity, field.name)!r}, "
        f"generated.{_generator_function(entity.entity, field.name)})"
        for entity, field in sequence_fields
    ]
    return "\n".join(
        [
            '"""Register TIDE-owned generated behavior with the application runtime."""',
            "",
            "from __future__ import annotations",
            "",
            "import importlib.util",
            "from pathlib import Path",
            "from types import ModuleType",
            "from tide.services import ActionService, RecordsService",
            "",
            "",
            "def configure_runtime(records: RecordsService, actions: ActionService) -> None:",
            "    generated = _load_actions()",
            *generator_registrations,
            *action_registrations,
            "",
            "",
            "def _load_actions() -> ModuleType:",
            "    actions_file = Path(__file__).with_name('actions.py')",
            "    spec = importlib.util.spec_from_file_location(",
            "        'tide_generated_actions', actions_file",
            "    )",
            "    if spec is None or spec.loader is None:",
            "        raise RuntimeError(f'could not load {actions_file.as_posix()}')",
            "    module = importlib.util.module_from_spec(spec)",
            "    spec.loader.exec_module(module)",
            "    return module",
            "",
        ]
    )


def _verify_candidate(
    model: ApplicationModel,
    plan: ApplicationGenerationPlan,
    rendered: dict[str, str],
) -> list[CandidateCheck]:
    entities = {
        item.entity: item
        for item in plan.operations
        if isinstance(item, DefineEntityOperation)
    }
    transitions = [
        item
        for item in plan.operations
        if isinstance(item, DefineStateTransitionOperation)
    ]
    reports = [
        item
        for item in plan.operations
        if isinstance(item, DefineRecordReportOperation)
    ]
    roles = {
        item.role: tuple(item.grants)
        for item in plan.operations
        if isinstance(item, DefineRoleOperation)
    }
    checks: list[CandidateCheck] = []
    checks.append(
        _equality_check(
            "model_shape",
            set(model.entities),
            set(entities),
            "compiled entity inventory matches the proposal",
        )
    )
    expected_views = _expected_view_names(entities.values())
    checks.append(
        _equality_check(
            "presentation_contract",
            set(model.views),
            expected_views,
            f"{len(expected_views)} browse/form/lookup/inline views compile",
        )
    )
    expected_permissions = {
        permission
        for entity in entities.values()
        for permission in (
            entity.list_permission,
            entity.read_permission,
            entity.create_permission,
            entity.update_permission,
        )
        if permission is not None
    }
    expected_permissions.update(item.permission for item in transitions)
    expected_permissions.update(item.permission for item in reports)
    security_matches = (
        set(model.permissions) == expected_permissions
        and {name: tuple(grants) for name, grants in model.roles.items()} == roles
    )
    checks.append(
        CandidateCheck(
            name="security_contract",
            status="passed" if security_matches else "failed",
            message=(
                "permissions and role grants match the structured proposal"
                if security_matches
                else "compiled permissions or role grants differ from the proposal"
            ),
        )
    )
    workflow_matches = all(
        _compiled_transition_matches(model, transition) for transition in transitions
    )
    templates_parse = all(
        _parses_as_python(rendered[path])
        for path in ("actions.py", "runtime.py")
        if path in rendered
    )
    checks.append(
        CandidateCheck(
            name="workflow_contract",
            status="passed" if workflow_matches and templates_parse else "failed",
            message=(
                f"{len(transitions)} constrained transition(s) and registrations "
                "passed static checks"
                if workflow_matches and templates_parse
                else "generated workflow metadata or templates failed static checks"
            ),
        )
    )
    sequences = [
        (entity, field)
        for entity in entities.values()
        for field in entity.fields
        if field.sequence is not None
    ]
    generator_matches = all(
        model.entities[entity.entity].fields[field.name].metadata.get("generated_by")
        == _generator_reference(entity.entity, field.name)
        and model.entities[entity.entity].fields[field.name].metadata.get("write")
        == "system"
        for entity, field in sequences
    )
    checks.append(
        CandidateCheck(
            name="generator_contract",
            status="passed" if generator_matches and templates_parse else "failed",
            message=(
                f"{len(sequences)} constrained sequence generator(s) passed "
                "static checks"
                if generator_matches and templates_parse
                else "generated sequence metadata or templates failed static checks"
            ),
        )
    )
    report_matches = set(model.reports) == {item.report for item in reports}
    report_matches = report_matches and all(
        _compiled_report_matches(model, item) for item in reports
    )
    pdf_count = sum(item.pdf_enabled for item in reports)
    checks.append(
        CandidateCheck(
            name="report_contract",
            status="passed" if report_matches else "failed",
            message=(
                f"{len(reports)} record report(s) compile; {pdf_count} request "
                "framework PDF rendering"
                if report_matches
                else "compiled report inventory differs from the proposal"
            ),
        )
    )
    return checks


def _expected_view_names(
    entities: Any,
) -> set[str]:
    entity_list = list(entities)
    names = {
        f"{entity.entity}.{suffix}"
        for entity in entity_list
        if entity.expose_tui
        for suffix in ("browse", "edit", "lookup")
    }
    names.update(
        f"{field.target}.inline_edit"
        for entity in entity_list
        for field in entity.fields
        if field.type == "collection"
        and field.target is not None
        and any(item in {"create", "update"} for item in field.cascade)
    )
    return names


class _FixtureUnavailable(ValueError):
    pass


def _verify_runnable_candidate(
    model: ApplicationModel,
    plan: ApplicationGenerationPlan,
) -> list[CandidateCheck]:
    checks: list[CandidateCheck] = []
    repository = InMemoryRepository()
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    has_runtime = any(
        isinstance(operation, DefineStateTransitionOperation)
        or (
            isinstance(operation, DefineEntityOperation)
            and any(field.sequence is not None for field in operation.fields)
        )
        for operation in plan.operations
    )
    try:
        configured = configure_application_runtime(model, records, actions)
    except Exception as error:
        checks.append(
            CandidateCheck(
                name="runtime_registration",
                status="failed",
                message=f"generated runtime registration failed: {error}",
            )
        )
        checks.extend(_skipped_runtime_after_registration())
        return checks
    registration_matches = configured is has_runtime
    checks.append(
        CandidateCheck(
            name="runtime_registration",
            status="passed" if registration_matches else "failed",
            message=(
                "fixed generated handlers and value generators registered"
                if configured
                else "candidate requires no application runtime registrations"
            ),
        )
    )
    if not registration_matches:
        checks.extend(_skipped_runtime_after_registration())
        return checks

    context = RequestContext(
        Principal(
            "tide:candidate-preview",
            permissions=frozenset(model.permissions),
        ),
        channel=Channel.SYSTEM,
    )
    special_collections = {
        (operation.entity, operation.requires_collection)
        for operation in plan.operations
        if isinstance(operation, DefineStateTransitionOperation)
        and operation.requires_collection is not None
    }
    special_collections.update(
        (operation.entity, operation.detail_collection)
        for operation in plan.operations
        if isinstance(operation, DefineRecordReportOperation)
    )
    created, persistence = _create_candidate_records(
        model,
        records,
        context,
        special_collections,
    )
    checks.append(persistence)
    if persistence.status == "failed":
        checks.extend(_skipped_runtime_after_persistence())
        return checks
    checks.append(_verify_crud(model, records, created, context))
    action_check = _verify_actions(model, plan, actions, created, context)
    checks.append(action_check)
    report_checks = _verify_reports(model, plan, records, created, context)
    checks.extend(report_checks)
    return checks


def _create_candidate_records(
    model: ApplicationModel,
    records: RecordsService,
    context: RequestContext,
    special_collections: set[tuple[str, str]],
) -> tuple[dict[str, dict[str, Any]], CandidateCheck]:
    candidates = {
        name: entity
        for name, entity in model.entities.items()
        if entity.metadata.get("permissions", {}).get("create") is not None
    }
    if not candidates:
        return {}, CandidateCheck(
            name="persistence_integration",
            status="skipped",
            message="proposal exposes no entity create operation",
        )
    if len(candidates) > MAX_INTEGRATION_ENTITIES:
        return {}, CandidateCheck(
            name="persistence_integration",
            status="skipped",
            message=(
                f"{len(candidates)} creatable entities exceed the bounded preview "
                f"limit of {MAX_INTEGRATION_ENTITIES}"
            ),
        )
    first_entity = next(iter(candidates))
    try:
        records.create(
            first_entity,
            RequestContext(
                Principal("tide:candidate-denied"),
                channel=Channel.SYSTEM,
            ),
        )
    except AuthorizationError:
        pass
    except Exception as error:
        return {}, CandidateCheck(
            name="persistence_integration",
            status="failed",
            message=f"unauthorized create returned an unexpected error: {error}",
        )
    else:
        return {}, CandidateCheck(
            name="persistence_integration",
            status="failed",
            message="entity create did not reject an unauthorized principal",
        )
    created: dict[str, dict[str, Any]] = {}
    pending = dict(candidates)
    while pending:
        progressed = False
        for entity_name in tuple(pending):
            try:
                values = _sample_entity_values(
                    model,
                    entity_name,
                    created,
                    special_collections,
                )
            except _FixtureUnavailable:
                continue
            try:
                session = records.create(entity_name, context, values)
                created[entity_name] = records.commit(session, context)
            except Exception as error:
                return created, CandidateCheck(
                    name="persistence_integration",
                    status="failed",
                    message=f"in-memory create failed for {entity_name}: {error}",
                )
            del pending[entity_name]
            progressed = True
        if not progressed:
            return created, CandidateCheck(
                name="persistence_integration",
                status="failed",
                message=(
                    "could not synthesize required references for: "
                    + ", ".join(sorted(pending))
                ),
            )
    return created, CandidateCheck(
        name="persistence_integration",
        status="passed",
        message=(
            f"created and validated {len(created)} record(s) through RecordsService "
            "using isolated in-memory persistence with an authorization denial check"
        ),
    )


def _sample_entity_values(
    model: ApplicationModel,
    entity_name: str,
    created: dict[str, dict[str, Any]],
    special_collections: set[tuple[str, str]],
    *,
    skipped_fields: frozenset[str] = frozenset(),
    ancestry: tuple[str, ...] = (),
) -> dict[str, Any]:
    if len(ancestry) >= MAX_SYNTHETIC_RELATIONSHIP_DEPTH or entity_name in ancestry:
        raise _FixtureUnavailable(
            f"synthetic relationship depth exceeded at {entity_name}"
        )
    entity = model.entities[entity_name]
    values: dict[str, Any] = {}
    for field_name, field in entity.fields.items():
        metadata = field.metadata
        if field_name in skipped_fields or metadata.get("primary_key"):
            continue
        field_type = metadata["type"]
        if field_type == "collection":
            if (entity_name, field_name) not in special_collections:
                continue
            if field.target_entity is None:
                raise _FixtureUnavailable(f"{entity_name}.{field_name} has no target")
            inverse = metadata.get("inverse")
            values[field_name] = [
                _sample_entity_values(
                    model,
                    field.target_entity,
                    created,
                    special_collections,
                    skipped_fields=(
                        frozenset({str(inverse)})
                        if inverse is not None
                        else frozenset()
                    ),
                    ancestry=(*ancestry, entity_name),
                )
            ]
            continue
        if (
            metadata.get("computed")
            or metadata.get("generated_by")
            or metadata.get("default_factory")
            or "default" in metadata
        ):
            continue
        if metadata.get("readonly") or metadata.get("write", "normal") != "normal":
            continue
        if field_type == "reference":
            if field.target_entity in created:
                target = model.entities[field.target_entity]
                primary_key = _primary_key_name(target)
                values[field_name] = created[field.target_entity][primary_key]
            elif metadata.get("required"):
                raise _FixtureUnavailable(
                    f"{entity_name}.{field_name} requires {field.target_entity}"
                )
            continue
        values[field_name] = _sample_scalar(entity_name, field_name, metadata)
    return values


def _sample_scalar(
    entity_name: str,
    field_name: str,
    metadata: Any,
) -> Any:
    field_type = metadata["type"]
    if field_type == "string":
        candidate = f"sample-{_snake(entity_name)}-{field_name}"
        length = int(metadata.get("length") or len(candidate))
        return candidate[:length] or "x"
    if field_type == "integer":
        return 1
    if field_type == "decimal":
        scale = int(metadata.get("scale") or 0)
        return Decimal(1).scaleb(-scale) if scale else Decimal(1)
    if field_type == "boolean":
        return True
    if field_type == "date":
        return date(2026, 1, 15)
    if field_type == "datetime":
        return datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    if field_type == "choice":
        choices = tuple(metadata.get("choices", ()))
        if not choices:
            raise _FixtureUnavailable(f"{entity_name}.{field_name} has no choices")
        return choices[0]
    raise _FixtureUnavailable(
        f"cannot synthesize {entity_name}.{field_name} ({field_type})"
    )


def _verify_crud(
    model: ApplicationModel,
    records: RecordsService,
    created: dict[str, dict[str, Any]],
    context: RequestContext,
) -> CandidateCheck:
    if not created:
        return CandidateCheck(
            name="crud_integration",
            status="skipped",
            message="no synthesized record is available for CRUD checks",
        )
    queried = 0
    for entity_name, record in created.items():
        entity = model.entities[entity_name]
        permissions = entity.metadata.get("permissions", {})
        primary_key = _primary_key_name(entity)
        if permissions.get("read") is not None:
            loaded = records.get(entity_name, record[primary_key], context)
            if loaded[primary_key] != record[primary_key]:
                return CandidateCheck(
                    name="crud_integration",
                    status="failed",
                    message=f"read-back identity mismatch for {entity_name}",
                )
        if permissions.get("list") is not None:
            page = records.query(entity_name, QuerySpec(limit=10), context)
            if not any(item[primary_key] == record[primary_key] for item in page):
                return CandidateCheck(
                    name="crud_integration",
                    status="failed",
                    message=f"list query omitted synthesized {entity_name}",
                )
            queried += 1
    update = _update_one_record(model, records, created, context)
    if update is not None:
        return update
    return CandidateCheck(
        name="crud_integration",
        status="passed",
        message=(
            f"secured read/list checks passed for {len(created)} record(s)"
            + ("; no safely mutable scalar was available" if queried == 0 else "")
        ),
    )


def _update_one_record(
    model: ApplicationModel,
    records: RecordsService,
    created: dict[str, dict[str, Any]],
    context: RequestContext,
) -> CandidateCheck | None:
    for entity_name, record in created.items():
        entity = model.entities[entity_name]
        permissions = entity.metadata.get("permissions", {})
        if permissions.get("read") is None or permissions.get("update") is None:
            continue
        for field_name, field in entity.fields.items():
            metadata = field.metadata
            if (
                metadata["type"] in {"reference", "collection", "datetime"}
                or metadata.get("primary_key")
                or metadata.get("readonly")
                or metadata.get("computed")
                or metadata.get("write", "normal") != "normal"
            ):
                continue
            replacement = _replacement_scalar(record.get(field_name), metadata)
            if replacement == record.get(field_name):
                continue
            try:
                session = records.begin_edit(
                    entity_name,
                    record[_primary_key_name(entity)],
                    context,
                )
                session.set(field_name, replacement)
                updated = records.commit(session, context)
            except Exception as error:
                return CandidateCheck(
                    name="crud_integration",
                    status="failed",
                    message=f"in-memory update failed for {entity_name}: {error}",
                )
            created[entity_name] = updated
            return CandidateCheck(
                name="crud_integration",
                status="passed",
                message=f"secured create/read/list/update passed using {entity_name}",
            )
    return None


def _replacement_scalar(current: Any, metadata: Any) -> Any:
    field_type = metadata["type"]
    if field_type == "string":
        length = int(metadata.get("length") or 7)
        return "updated"[:length] or "u"
    if field_type == "integer":
        return int(current or 0) + 1
    if field_type == "decimal":
        scale = int(metadata.get("scale") or 0)
        increment = Decimal(1).scaleb(-scale) if scale else Decimal(1)
        return Decimal(current or 0) + increment
    if field_type == "boolean":
        return not bool(current)
    if field_type == "date":
        return date(2026, 1, 16)
    if field_type == "choice":
        return next(
            (choice for choice in metadata.get("choices", ()) if choice != current),
            current,
        )
    return current


def _verify_actions(
    model: ApplicationModel,
    plan: ApplicationGenerationPlan,
    actions: ActionService,
    created: dict[str, dict[str, Any]],
    context: RequestContext,
) -> CandidateCheck:
    transitions = [
        operation
        for operation in plan.operations
        if isinstance(operation, DefineStateTransitionOperation)
    ]
    if not transitions:
        return CandidateCheck(
            name="action_integration",
            status="skipped",
            message="proposal defines no state transition",
        )
    if len(transitions) > MAX_INTEGRATION_TRANSITIONS:
        return CandidateCheck(
            name="action_integration",
            status="skipped",
            message=(
                f"{len(transitions)} transitions exceed the bounded preview limit "
                f"of {MAX_INTEGRATION_TRANSITIONS}"
            ),
        )
    for index, transition in enumerate(transitions):
        record = created.get(transition.entity)
        if record is None:
            return CandidateCheck(
                name="action_integration",
                status="skipped",
                message=f"no synthesized {transition.entity} action target is available",
            )
        entity = model.entities[transition.entity]
        identity = record[_primary_key_name(entity)]
        denied_context = RequestContext(
            Principal("tide:candidate-denied"),
            channel=Channel.SYSTEM,
        )
        try:
            actions.execute(
                transition.entity,
                transition.action,
                identity,
                {},
                denied_context,
            )
        except AuthorizationError:
            pass
        except Exception as error:
            return CandidateCheck(
                name="action_integration",
                status="failed",
                message=f"unauthorized action returned an unexpected error: {error}",
            )
        else:
            return CandidateCheck(
                name="action_integration",
                status="failed",
                message="generated action did not reject an unauthorized principal",
            )
        idempotency_key = (
            f"candidate-{index}-{transition.action}" if transition.idempotent else None
        )
        try:
            result = actions.execute(
                transition.entity,
                transition.action,
                identity,
                {},
                context,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            return CandidateCheck(
                name="action_integration",
                status="failed",
                message=(
                    f"generated action {transition.entity}.{transition.action} "
                    f"failed: {error}"
                ),
            )
        if result.get(transition.state_field) != transition.to_value:
            return CandidateCheck(
                name="action_integration",
                status="failed",
                message=f"generated action did not set {transition.state_field}",
            )
        if transition.stamp_datetime_field is not None and not isinstance(
            result.get(transition.stamp_datetime_field), datetime
        ):
            return CandidateCheck(
                name="action_integration",
                status="failed",
                message="generated action did not produce its datetime stamp",
            )
        if (
            transition.stamp_principal_field is not None
            and result.get(transition.stamp_principal_field)
            != context.principal.identifier
        ):
            return CandidateCheck(
                name="action_integration",
                status="failed",
                message="generated action did not produce its principal stamp",
            )
        if idempotency_key is not None:
            try:
                replay = actions.execute(
                    transition.entity,
                    transition.action,
                    identity,
                    {},
                    context,
                    idempotency_key=idempotency_key,
                )
            except Exception as error:
                return CandidateCheck(
                    name="action_integration",
                    status="failed",
                    message=f"idempotent generated action did not replay: {error}",
                )
            if replay.get(transition.state_field) != transition.to_value:
                return CandidateCheck(
                    name="action_integration",
                    status="failed",
                    message="idempotent generated action replay changed its outcome",
                )
        created[transition.entity] = result
    return CandidateCheck(
        name="action_integration",
        status="passed",
        message=(
            f"executed {len(transitions)} generated transition(s) through "
            "ActionService with denial and idempotency checks"
        ),
    )


def _verify_reports(
    model: ApplicationModel,
    plan: ApplicationGenerationPlan,
    records: RecordsService,
    created: dict[str, dict[str, Any]],
    context: RequestContext,
) -> list[CandidateCheck]:
    reports = [
        operation
        for operation in plan.operations
        if isinstance(operation, DefineRecordReportOperation)
    ]
    if not reports:
        return [
            CandidateCheck(
                name=name,
                status="skipped",
                message="proposal defines no record report",
            )
            for name in (
                "report_document_integration",
                "html_renderer_integration",
                "pdf_renderer_integration",
            )
        ]
    if len(reports) > MAX_INTEGRATION_REPORTS:
        message = (
            f"{len(reports)} reports exceed the bounded preview limit of "
            f"{MAX_INTEGRATION_REPORTS}"
        )
        return [
            CandidateCheck(name=name, status="skipped", message=message)
            for name in (
                "report_document_integration",
                "html_renderer_integration",
                "pdf_renderer_integration",
            )
        ]
    service = ReportService(model, records)
    documents = []
    for report in reports:
        record = created.get(report.entity)
        if record is None:
            return [
                CandidateCheck(
                    name="report_document_integration",
                    status="skipped",
                    message=f"no synthesized {report.entity} report record is available",
                ),
                CandidateCheck(
                    name="html_renderer_integration",
                    status="skipped",
                    message="report document was not built",
                ),
                CandidateCheck(
                    name="pdf_renderer_integration",
                    status="skipped",
                    message="report document was not built",
                ),
            ]
        identity = record[_primary_key_name(model.entities[report.entity])]
        denied_context = RequestContext(
            Principal("tide:candidate-denied"),
            channel=Channel.REPORT,
        )
        try:
            service.build_for_record(report.report, identity, denied_context)
        except AuthorizationError:
            pass
        except Exception as error:
            return [
                CandidateCheck(
                    name="report_document_integration",
                    status="failed",
                    message=f"unauthorized report returned an unexpected error: {error}",
                ),
                CandidateCheck(
                    name="html_renderer_integration",
                    status="skipped",
                    message="report authorization contract failed",
                ),
                CandidateCheck(
                    name="pdf_renderer_integration",
                    status="skipped",
                    message="report authorization contract failed",
                ),
            ]
        else:
            return [
                CandidateCheck(
                    name="report_document_integration",
                    status="failed",
                    message="report did not reject an unauthorized principal",
                ),
                CandidateCheck(
                    name="html_renderer_integration",
                    status="skipped",
                    message="report authorization contract failed",
                ),
                CandidateCheck(
                    name="pdf_renderer_integration",
                    status="skipped",
                    message="report authorization contract failed",
                ),
            ]
        try:
            document = service.build_for_record(
                report.report,
                identity,
                context,
                generated_at=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
            )
        except Exception as error:
            return [
                CandidateCheck(
                    name="report_document_integration",
                    status="failed",
                    message=f"report service failed for {report.report}: {error}",
                ),
                CandidateCheck(
                    name="html_renderer_integration",
                    status="skipped",
                    message="report document was not built",
                ),
                CandidateCheck(
                    name="pdf_renderer_integration",
                    status="skipped",
                    message="report document was not built",
                ),
            ]
        if not document.detail.columns or not document.detail.rows:
            return [
                CandidateCheck(
                    name="report_document_integration",
                    status="failed",
                    message=f"report {report.report} has no rendered detail row",
                ),
                CandidateCheck(
                    name="html_renderer_integration",
                    status="skipped",
                    message="report document contract failed",
                ),
                CandidateCheck(
                    name="pdf_renderer_integration",
                    status="skipped",
                    message="report document contract failed",
                ),
            ]
        documents.append((report, document))
    checks = [
        CandidateCheck(
            name="report_document_integration",
            status="passed",
            message=(
                f"built {len(documents)} secured report document(s) with "
                "unauthorized-access denial checks"
            ),
        )
    ]
    try:
        html_documents = [render_html(document) for _report, document in documents]
    except Exception as error:
        checks.append(
            CandidateCheck(
                name="html_renderer_integration",
                status="failed",
                message=f"HTML rendering failed: {error}",
            )
        )
    else:
        html_valid = all(
            html.startswith("<!doctype html>") and document.title in html
            for html, (_report, document) in zip(html_documents, documents)
        )
        checks.append(
            CandidateCheck(
                name="html_renderer_integration",
                status="passed" if html_valid else "failed",
                message=(
                    f"rendered {len(html_documents)} standalone HTML report(s)"
                    if html_valid
                    else "HTML renderer output failed its document checks"
                ),
            )
        )
    pdf_documents = [document for report, document in documents if report.pdf_enabled]
    if not pdf_documents:
        checks.append(
            CandidateCheck(
                name="pdf_renderer_integration",
                status="skipped",
                message="no report requests PDF rendering",
            )
        )
        return checks
    try:
        pdf_values = [render_pdf(document) for document in pdf_documents]
    except PdfDependencyMissing:
        checks.append(
            CandidateCheck(
                name="pdf_renderer_integration",
                status="skipped",
                message="the optional report extra is not installed",
            )
        )
    except Exception as error:
        checks.append(
            CandidateCheck(
                name="pdf_renderer_integration",
                status="failed",
                message=f"PDF rendering failed: {error}",
            )
        )
    else:
        valid = all(
            value.startswith(b"%PDF-") and len(value) > 1_000 for value in pdf_values
        )
        checks.append(
            CandidateCheck(
                name="pdf_renderer_integration",
                status="passed" if valid else "failed",
                message=(
                    f"rendered {len(pdf_values)} PDF report(s)"
                    if valid
                    else "PDF renderer output failed its binary checks"
                ),
            )
        )
    return checks


def _primary_key_name(entity: Any) -> str:
    return next(
        name
        for name, field in entity.fields.items()
        if field.metadata.get("primary_key")
    )


def _skipped_runtime_after_registration() -> list[CandidateCheck]:
    return _skipped_runnable_checks("runtime registration failed")


def _skipped_runtime_after_persistence() -> list[CandidateCheck]:
    return [
        CandidateCheck(name=name, status="skipped", message="persistence check failed")
        for name in (
            "crud_integration",
            "action_integration",
            "report_document_integration",
            "html_renderer_integration",
            "pdf_renderer_integration",
        )
    ]


def _skipped_runnable_checks(reason: str) -> list[CandidateCheck]:
    return [
        CandidateCheck(name=name, status="skipped", message=reason)
        for name in (
            "runtime_registration",
            "persistence_integration",
            "crud_integration",
            "action_integration",
            "report_document_integration",
            "html_renderer_integration",
            "pdf_renderer_integration",
        )
    ]


def _compiled_transition_matches(
    model: ApplicationModel,
    transition: DefineStateTransitionOperation,
) -> bool:
    entity = model.entities.get(transition.entity)
    if entity is None:
        return False
    action = entity.actions.get(transition.action)
    if action is None:
        return False
    if (
        action.get("permission") != transition.permission
        or action.get("execute") != _handler_reference(transition)
        or bool(action.get("idempotent")) != transition.idempotent
        or dict(action.get("expose", {}))
        != {"rest": transition.expose_rest, "mcp": transition.expose_mcp}
    ):
        return False
    for field_name in (
        transition.state_field,
        transition.stamp_datetime_field,
        transition.stamp_principal_field,
    ):
        if field_name is None:
            continue
        field = entity.fields.get(field_name)
        if field is None or field.metadata.get("write") != "action_only":
            return False
    return True


def _compiled_report_matches(
    model: ApplicationModel,
    report: DefineRecordReportOperation,
) -> bool:
    compiled = model.reports.get(report.report)
    if compiled is None:
        return False
    bands = compiled.get("bands", {})
    header = tuple(item.get("field") for item in bands.get("record_header", ()))
    footer = tuple(item.get("field") for item in bands.get("report_footer", ()))
    detail = bands.get("detail", {})
    return (
        compiled.get("entity") == report.entity
        and compiled.get("title") == report.title
        and compiled.get("permission") == report.permission
        and dict(compiled.get("expose", {}))
        == {"rest": report.expose_rest, "mcp": report.expose_mcp}
        and header == report.header_fields
        and detail.get("source") == report.detail_collection
        and tuple(detail.get("columns", ())) == report.detail_columns
        and footer == report.footer_fields
    )


def _skipped_model_checks() -> list[CandidateCheck]:
    static = [
        CandidateCheck(
            name=name,
            status="skipped",
            message="candidate did not compile",
        )
        for name in (
            "model_shape",
            "presentation_contract",
            "security_contract",
            "workflow_contract",
            "generator_contract",
            "report_contract",
        )
    ]
    return [*static, *_skipped_runnable_checks("candidate did not compile")]


def _equality_check(
    name: str,
    actual: Any,
    expected: Any,
    success: str,
) -> CandidateCheck:
    return CandidateCheck(
        name=name,
        status="passed" if actual == expected else "failed",
        message=success if actual == expected else f"{name} differs from the proposal",
    )


def _write_candidate(root: Path, artifacts: tuple[CandidateArtifact, ...]) -> None:
    resolved_root = root.resolve()
    for artifact in artifacts:
        relative = _validated_relative_path(artifact.path)
        destination = root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.resolve().is_relative_to(resolved_root):
            raise ValueError("candidate artifact escaped the temporary root")
        destination.write_text(artifact.content, encoding="utf-8", newline="\n")


def _add_artifact(rendered: dict[str, str], path: str, content: str) -> None:
    normalized = _validated_relative_path(path).as_posix()
    if normalized.casefold() in {item.casefold() for item in rendered}:
        raise ValueError(f"candidate artifact path collision at {normalized!r}")
    rendered[normalized] = content


def _validated_relative_path(path: str) -> PurePosixPath:
    relative = PurePosixPath(path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"unsafe candidate artifact path {path!r}")
    if any(part in {"", "."} for part in relative.parts):
        raise ValueError(f"invalid candidate artifact path {path!r}")
    if len(path.encode("utf-8")) > 512 or any(
        len(part.encode("utf-8")) > 120 for part in relative.parts
    ):
        raise ValueError(f"candidate artifact path is too long: {path!r}")
    windows_devices = {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    for part in relative.parts:
        if part.split(".", 1)[0].casefold() in windows_devices:
            raise ValueError(
                f"candidate artifact path uses reserved device name: {path!r}"
            )
    return relative


def _logical_path(folder: str, name: str) -> str:
    *namespace, leaf = name.split(".")
    parts = [folder, *(_snake(part) for part in namespace), f"{_snake(leaf)}.yaml"]
    return PurePosixPath(*parts).as_posix()


def _snake(value: str) -> str:
    first = "".join(
        ("_" + character.lower()) if character.isupper() else character
        for character in value
    ).lstrip("_")
    return first.replace("-", "_")


def _handler_function(transition: DefineStateTransitionOperation) -> str:
    entity = "_".join(_snake(part) for part in transition.entity.split("."))
    return f"transition_{entity}_{_snake(transition.action)}"


def _handler_reference(transition: DefineStateTransitionOperation) -> str:
    return f"actions.{_handler_function(transition)}"


def _generator_function(entity_name: str, field_name: str) -> str:
    entity = "_".join(_snake(part) for part in entity_name.split("."))
    return f"generate_{entity}_{_snake(field_name)}"


def _generator_reference(entity_name: str, field_name: str) -> str:
    return f"actions.{_generator_function(entity_name, field_name)}"


def _yaml_document(value: dict[str, Any]) -> str:
    return yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )


def _new_tree_diff(
    target_path: str,
    artifacts: tuple[CandidateArtifact, ...],
) -> str:
    chunks: list[str] = []
    for artifact in artifacts:
        target = f"b/{target_path}/{artifact.path}"
        chunks.extend(
            unified_diff(
                [],
                artifact.content.splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=target,
            )
        )
    return "".join(chunks)


def _fingerprint_empty_base(target_path: str) -> str:
    return _content_hash(f"tide-empty-tree-v1\0{target_path}")


def _fingerprint_artifacts(artifacts: tuple[CandidateArtifact, ...]) -> str:
    digest = sha256()
    digest.update(b"tide-candidate-tree-v1\0")
    for artifact in artifacts:
        digest.update(artifact.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(artifact.content.encode("utf-8"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _content_hash(content: str) -> str:
    return "sha256:" + sha256(content.encode("utf-8")).hexdigest()


def _parses_as_python(content: str) -> bool:
    try:
        ast.parse(content)
    except SyntaxError:
        return False
    return True
