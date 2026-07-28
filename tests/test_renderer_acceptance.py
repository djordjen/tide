from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml

from tide import compile_project
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.api.openapi import REST_OPERATIONS, rest_exposures
from tide.api.presentation import build_presentation_manifest
from tide.presentation import (
    application_navigation,
    browse_columns,
    field_alignment,
    form_layout_sections,
)


ROOT = Path(__file__).parents[1]
MATRIX_PATH = ROOT / "docs" / "renderer-acceptance.yaml"
VALID_STATUSES = {"covered", "partial", "planned", "not_applicable"}
VALID_RUNNERS = {"python", "python-gui", "web"}


def test_renderer_acceptance_matrix_is_complete_and_evidence_resolves() -> None:
    matrix = _matrix()

    assert matrix["schema_version"] == "0.1"
    renderer_names = tuple(matrix["renderers"])
    assert renderer_names == ("tui", "qt", "web")

    capabilities = matrix["capabilities"]
    identifiers = [item["id"] for item in capabilities]
    assert len(identifiers) == len(set(identifiers))
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", item) for item in identifiers)

    for capability in capabilities:
        assert capability["tier"] in {"parity", "roadmap"}
        assert tuple(capability["renderers"]) == renderer_names
        for renderer_name, cell in capability["renderers"].items():
            status = cell["status"]
            assert status in VALID_STATUSES
            evidence = cell.get("evidence", [])
            if status == "covered":
                assert evidence, (
                    f"{capability['id']}.{renderer_name} is covered "
                    "without automated evidence"
                )
            else:
                assert cell.get("note"), (
                    f"{capability['id']}.{renderer_name} requires a note "
                    f"for status {status}"
                )
            for item in evidence:
                _assert_evidence_resolves(item)


def test_all_parity_capabilities_are_covered_by_every_renderer() -> None:
    matrix = _matrix()

    parity = [
        capability
        for capability in matrix["capabilities"]
        if capability["tier"] == "parity"
    ]

    assert parity
    assert {
        cell["status"]
        for capability in parity
        for cell in capability["renderers"].values()
    } == {"covered"}


def test_reference_contract_matches_compiled_model_and_web_projection() -> None:
    matrix = _matrix()
    model = compile_project(ROOT / matrix["reference_application"])
    expected = matrix["reference_contract"]

    actual_navigation = [
        {
            "label": group.label,
            "views": [item.view for item in group.items],
        }
        for group in application_navigation(model)
    ]
    assert actual_navigation == expected["navigation"]

    for view_name, browse_expected in expected["browse"].items():
        view = model.views[view_name]
        entity = model.entity(view.entity)
        columns = browse_columns(view, entity)
        assert list(columns) == browse_expected["columns"]
        assert {
            name: field_alignment(entity.field(name), model.formats)
            for name in columns
        } == browse_expected["alignments"]

    for view_name, form_expected in expected["forms"].items():
        view = model.views[view_name]
        entity = model.entity(view.entity)
        assert _core_sections(model, view_name) == form_expected["sections"]

    exposures = rest_exposures(
        model,
        allowed_operations=REST_OPERATIONS,
    )
    session = TideSessionInfo(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        authentication="renderer-acceptance",
        principal="acceptance:all-fields",
        roles=("sales_clerk",),
        entities={
            name: TideEntityCapabilities(
                operations=tuple(
                    exposures[name].operations if name in exposures else ()
                ),
                draft_operations=("create", "update"),
                readable_fields=tuple(entity.fields),
                writable_fields=tuple(
                    field_name
                    for field_name, field in entity.fields.items()
                    if not field.metadata.get("primary_key")
                    and not field.metadata.get("computed")
                    and not field.metadata.get("readonly")
                    and field.metadata.get("write", "normal") == "normal"
                ),
                actions=tuple(entity.actions),
            )
            for name, entity in model.entities.items()
        },
    )
    manifest = build_presentation_manifest(
        model,
        session,
        exposures,
        base_path="/api/v1",
    )

    assert [
        {
            "label": group.label,
            "views": [item.view for item in group.items],
        }
        for group in manifest.navigation
    ] == expected["navigation"]
    for view_name, browse_expected in expected["browse"].items():
        projected = manifest.views[view_name]
        assert [column.name for column in projected.columns] == (
            browse_expected["columns"]
        )
        assert {
            column.name: column.alignment for column in projected.columns
        } == browse_expected["alignments"]
    for view_name, form_expected in expected["forms"].items():
        assert _web_sections(manifest.forms[view_name]) == (
            form_expected["sections"]
        )


def _matrix() -> dict[str, Any]:
    loaded = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _assert_evidence_resolves(evidence: dict[str, str]) -> None:
    relative = Path(evidence["path"])
    assert not relative.is_absolute()
    path = (ROOT / relative).resolve()
    assert path.is_relative_to(ROOT.resolve())
    assert path.is_file(), f"renderer evidence file does not exist: {relative}"
    assert evidence["runner"] in VALID_RUNNERS

    selector = evidence["selector"]
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        pattern = rf"^def {re.escape(selector)}\s*\("
    else:
        pattern = (
            r"(?:it|test)\(\s*[\"']"
            + re.escape(selector)
            + r"[\"']\s*,"
        )
    assert re.search(pattern, source, flags=re.MULTILINE), (
        f"renderer evidence selector does not resolve: {relative}::{selector}"
    )


def _core_sections(model: Any, view_name: str) -> list[dict[str, Any]]:
    view = model.views[view_name]
    entity = model.entity(view.entity)
    result: list[dict[str, Any]] = []
    for section in form_layout_sections(view, entity):
        if section.kind == "group":
            result.append(
                {
                    "kind": "group",
                    "label": section.label,
                    "rows": [list(row) for row in section.rows],
                }
            )
            continue
        assert section.collection is not None
        inline = model.views[section.inline_view]
        target = model.entity(inline.entity)
        result.append(
            {
                "kind": "collection",
                "label": section.label,
                "collection": section.collection,
                "columns": list(browse_columns(inline, target)),
            }
        )
    return result


def _web_sections(form: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for section in form.sections:
        if section.kind == "group":
            result.append(
                {
                    "kind": "group",
                    "label": section.label,
                    "rows": [list(row) for row in section.rows],
                }
            )
        else:
            result.append(
                {
                    "kind": "collection",
                    "label": section.label,
                    "collection": section.name,
                    "columns": [column.name for column in section.columns],
                }
            )
    return result
