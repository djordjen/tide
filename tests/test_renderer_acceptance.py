"""The renderer acceptance matrix, checked so that it can fail.

``docs/renderer-acceptance.yaml`` claims three renderers resolve one compiled
contract identically. Three things have to hold for that claim to mean anything,
and each has its own group of tests here:

* the matrix is well formed and every parity cell is genuinely covered;
* every evidence pointer names a test that really exists and really runs;
* each renderer, asked through *its own* entry point, produces the recorded
  contract.

The last group is what catches drift. Every resolver below goes through the
adapter's own code -- ``TideApp`` for the TUI, ``QtBrowseController`` for Qt,
``build_presentation_manifest`` for the Web -- rather than through a shared
helper this file picked, so a renderer that quietly forks its own layout
resolution stops matching.

The checks are written as plain functions taking a matrix or a resolution so the
final group of tests can feed them deliberately broken input and prove they
refuse it. A guarantee nobody has watched fail is not a guarantee.
"""

from __future__ import annotations

from copy import deepcopy
import importlib
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
import yaml

from tide import compile_project
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.api.openapi import REST_OPERATIONS, rest_exposures
from tide.api.presentation import build_presentation_manifest
from tide.data import InMemoryRepository
from tide.presentation import (
    application_navigation,
    browse_columns,
    field_alignment,
    form_layout_sections,
)
from tide.qt import QtBrowseController
from tide.qt.presenter import QtDetailGroup
from tide.runtime import Channel, Principal, RequestContext
from tide.services import ActionService, RecordsService
from tide.sessions import RecordSession
from tide.tui import TideApp
from tide.tui.form import RecordEditScreen
from tide.tui.table import table_label


ROOT = Path(__file__).parents[1]
MATRIX_PATH = ROOT / "docs" / "renderer-acceptance.yaml"
VALID_STATUSES = {"covered", "partial", "planned", "not_applicable"}
VALID_RUNNERS = {"python", "python-gui", "web"}
PYTHON_RUNNERS = {"python", "python-gui"}

# Which parts of the recorded contract each renderer can be asked for without a
# display. Qt resolves navigation inside its widget layer, which needs a live
# QApplication; test_qt_widgets.py covers it there. The TUI offers every browse
# view in one Select, so it resolves an ordered view list rather than groups.
CONTRACT_COVERAGE = {
    "shared": ("navigation", "browse", "forms"),
    "tui": ("navigation_views", "browse", "forms"),
    "qt": ("browse", "forms"),
    "web": ("navigation", "browse", "forms", "reports"),
}


def _load_matrix() -> dict[str, Any]:
    loaded = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


MATRIX = _load_matrix()


def _evidence_entries(matrix: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, str]]]:
    for capability in matrix["capabilities"]:
        for renderer_name, cell in capability["renderers"].items():
            for item in cell.get("evidence", ()):
                yield capability["id"], renderer_name, item


EVIDENCE = tuple(_evidence_entries(MATRIX))
EVIDENCE_IDS = tuple(
    f"{capability}.{renderer}-{item['selector'][:40]}"
    for capability, renderer, item in EVIDENCE
)


@pytest.fixture(scope="module")
def model() -> Any:
    return compile_project(ROOT / MATRIX["reference_application"])


# --- the matrix itself -----------------------------------------------------


def test_the_matrix_is_well_formed() -> None:
    _check_matrix_structure(MATRIX)


def test_every_parity_capability_is_covered_by_every_renderer() -> None:
    _check_parity_coverage(MATRIX)


def test_the_matrix_carries_evidence_for_every_renderer() -> None:
    """A matrix that pointed nowhere would pass every per-entry check below."""

    assert EVIDENCE
    assert {renderer for _, renderer, _ in EVIDENCE} == set(MATRIX["renderers"])


# --- evidence --------------------------------------------------------------


@pytest.mark.parametrize(("capability", "renderer", "item"), EVIDENCE, ids=EVIDENCE_IDS)
def test_matrix_evidence_names_a_test_that_runs(
    capability: str,
    renderer: str,
    item: dict[str, str],
) -> None:
    path = _check_evidence_location(item)
    if item["runner"] in PYTHON_RUNNERS:
        _resolve_python_evidence(path, item["selector"])
    else:
        _resolve_web_evidence(path, item["selector"])


# --- the reference contract, resolved by each renderer ---------------------


@pytest.mark.parametrize("renderer", tuple(CONTRACT_COVERAGE))
def test_every_renderer_resolves_the_recorded_reference_contract(
    renderer: str,
    model: Any,
) -> None:
    contract = MATRIX["reference_contract"]
    resolved = _RESOLVERS[renderer](model, contract)

    _check_contract(resolved, contract, renderer)


# --- the checks above, proven able to refuse ------------------------------


def test_the_structure_check_refuses_coverage_claimed_without_evidence() -> None:
    broken = deepcopy(MATRIX)
    broken["capabilities"][0]["renderers"]["tui"]["evidence"] = []

    with pytest.raises(AssertionError, match="without automated evidence"):
        _check_matrix_structure(broken)


def test_the_structure_check_refuses_a_duplicated_capability() -> None:
    broken = deepcopy(MATRIX)
    broken["capabilities"].append(deepcopy(broken["capabilities"][0]))

    with pytest.raises(AssertionError):
        _check_matrix_structure(broken)


def test_the_parity_check_refuses_a_regressed_cell() -> None:
    broken = deepcopy(MATRIX)
    capability = next(
        item for item in broken["capabilities"] if item["tier"] == "parity"
    )
    capability["renderers"]["qt"] = {"status": "partial", "note": "regressed"}

    with pytest.raises(AssertionError, match="not covered by every renderer"):
        _check_parity_coverage(broken)


def test_the_evidence_check_refuses_a_selector_that_no_longer_exists() -> None:
    with pytest.raises(AssertionError, match="does not resolve"):
        _resolve_python_evidence(Path(__file__), "test_this_name_was_renamed_away")


def test_the_evidence_check_refuses_a_web_selector_that_no_longer_exists() -> None:
    with pytest.raises(AssertionError, match="does not resolve"):
        _resolve_web_evidence(
            ROOT / "web" / "src" / "App.test.tsx",
            "this vitest name was renamed away",
        )


def test_the_evidence_check_refuses_a_path_outside_the_repository() -> None:
    with pytest.raises(AssertionError):
        _check_evidence_location(
            {"path": "../elsewhere/test_x.py", "selector": "test_x", "runner": "python"}
        )


def test_a_contract_that_names_no_role_is_refused_rather_than_defaulted() -> None:
    """`sales_clerk` was the fallback until a second application existed.

    A default here is an Invoicing role that another application does not
    define, so the contract would resolve as a role granting nothing and fail
    as an empty capability set -- never as the missing setting it actually is.
    """

    assert _contract_role(MATRIX["reference_contract"]) == "sales_clerk"

    with pytest.raises(AssertionError, match="must name the role"):
        _contract_role({"browse": {}, "forms": {}})


def test_the_contract_check_refuses_a_renderer_whose_columns_drift(model: Any) -> None:
    contract = MATRIX["reference_contract"]
    resolved = _shared_resolution(model, contract)
    view_name = next(iter(contract["browse"]))
    resolved["browse"][view_name]["columns"] = ["total", "number"]

    with pytest.raises(AssertionError, match="browse layout"):
        _check_contract(resolved, contract, "shared")


def test_the_contract_check_refuses_a_renderer_whose_form_drifts(model: Any) -> None:
    contract = MATRIX["reference_contract"]
    resolved = _shared_resolution(model, contract)
    view_name = next(iter(contract["forms"]))
    resolved["forms"][view_name]["sections"] = []

    with pytest.raises(AssertionError, match="form layout"):
        _check_contract(resolved, contract, "shared")


def test_the_contract_check_refuses_a_renderer_that_stops_resolving(
    model: Any,
) -> None:
    """Dropping an aspect must fail rather than quietly shrink what is compared."""

    contract = MATRIX["reference_contract"]
    resolved = _shared_resolution(model, contract)
    del resolved["navigation"]

    with pytest.raises(AssertionError, match="resolves"):
        _check_contract(resolved, contract, "shared")


# --- checks ----------------------------------------------------------------


def _check_matrix_structure(matrix: dict[str, Any]) -> None:
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
            if status == "covered":
                assert cell.get("evidence"), (
                    f"{capability['id']}.{renderer_name} is covered "
                    "without automated evidence"
                )
            else:
                assert cell.get("note"), (
                    f"{capability['id']}.{renderer_name} requires a note "
                    f"for status {status}"
                )


def _check_parity_coverage(matrix: dict[str, Any]) -> None:
    parity = [
        capability
        for capability in matrix["capabilities"]
        if capability["tier"] == "parity"
    ]
    assert parity
    uncovered = [
        f"{capability['id']}.{renderer_name} is {cell['status']}"
        for capability in parity
        for renderer_name, cell in capability["renderers"].items()
        if cell["status"] != "covered"
    ]
    assert not uncovered, (
        "parity capabilities are not covered by every renderer: "
        + "; ".join(uncovered)
    )


def _check_evidence_location(item: dict[str, str]) -> Path:
    relative = Path(item["path"])
    assert not relative.is_absolute()
    path = (ROOT / relative).resolve()
    assert path.is_relative_to(ROOT.resolve())
    assert path.is_file(), f"renderer evidence file does not exist: {relative}"
    assert item["runner"] in VALID_RUNNERS
    return path


def _resolve_python_evidence(path: Path, selector: str) -> Any:
    """Import the evidence module and return the named test function.

    Importing is the point. A regex over the source proves only that the
    characters are present: a module that no longer imports, a test that moved
    inside a class, and a ``def`` sitting in a docstring all read the same to a
    text search, and none of them can be run.

    A module guarded by ``importorskip`` skips this check rather than failing it,
    because the optional GUI extra is genuinely absent on some platforms. CI
    installs it on the Windows job and asserts the import separately, so the
    matrix is still verified somewhere on every push.
    """

    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    try:
        module = importlib.import_module(path.stem)
    except pytest.skip.Exception as skipped:
        pytest.skip(f"{path.name} cannot run in this environment: {skipped}")

    resolved = getattr(module, selector, None)
    assert callable(resolved), (
        f"renderer evidence does not resolve to a test function: "
        f"{path.name}::{selector}"
    )
    for mark in getattr(resolved, "pytestmark", ()):
        assert mark.name not in {"skip", "xfail"}, (
            f"renderer evidence is marked {mark.name} and cannot prove "
            f"coverage: {path.name}::{selector}"
        )
    return resolved


def _resolve_web_evidence(path: Path, selector: str) -> None:
    """Match a vitest name in the source.

    Vitest owns these and the Python suite cannot ask it what ran, so this stays
    a source match -- the one evidence class the matrix still takes on trust.
    Requiring the file to sit under ``web/src`` at least stops a pointer drifting
    to somewhere vitest never collects.
    """

    assert path.is_relative_to((ROOT / "web" / "src").resolve())
    pattern = r"(?:it|test)\(\s*[\"']" + re.escape(selector) + r"[\"']\s*,"
    assert re.search(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE), (
        f"renderer evidence does not resolve: {path.name}::{selector}"
    )


def _check_contract(
    resolved: dict[str, Any],
    contract: dict[str, Any],
    renderer: str,
) -> None:
    assert tuple(resolved) == CONTRACT_COVERAGE[renderer], (
        f"{renderer} resolves {tuple(resolved)} of the reference contract, "
        f"not the declared {CONTRACT_COVERAGE[renderer]}"
    )

    for view_name, expected in contract["browse"].items():
        assert resolved["browse"][view_name] == {
            "columns": expected["columns"],
            "alignments": expected["alignments"],
        }, f"{renderer} browse layout drifted for {view_name}"

    for view_name, expected in contract["forms"].items():
        assert resolved["forms"][view_name]["sections"] == expected["sections"], (
            f"{renderer} form layout drifted for {view_name}"
        )

    if "navigation" in resolved:
        assert resolved["navigation"] == contract["navigation"], (
            f"{renderer} navigation drifted"
        )
    if "navigation_views" in resolved:
        assert resolved["navigation_views"] == [
            view for group in contract["navigation"] for view in group["views"]
        ], f"{renderer} navigation drifted"
    if "reports" in resolved:
        assert resolved["reports"] == contract["reports"], (
            f"{renderer} reports drifted"
        )


# --- per-renderer resolution ----------------------------------------------


def _shared_resolution(model: Any, contract: dict[str, Any]) -> dict[str, Any]:
    browse = {}
    for view_name in contract["browse"]:
        view = model.views[view_name]
        entity = model.entity(view.entity)
        columns = list(browse_columns(view, entity))
        browse[view_name] = {
            "columns": columns,
            "alignments": {
                name: field_alignment(entity.field(name), model.formats)
                for name in columns
            },
        }
    return {
        "navigation": [
            {"label": group.label, "views": [item.view for item in group.items]}
            for group in application_navigation(model)
        ],
        "browse": browse,
        "forms": {
            view_name: {"sections": _shared_sections(model, view_name)}
            for view_name in contract["forms"]
        },
    }


def _tui_resolution(model: Any, contract: dict[str, Any]) -> dict[str, Any]:
    records = RecordsService(model, InMemoryRepository())
    actions = ActionService(model, records)
    context = RequestContext(
        principal=Principal(
            "acceptance:tui",
            roles=frozenset({_contract_role(contract)}),
        ),
        channel=Channel.TUI,
    )
    default = TideApp(model, records, context, actions=actions)

    browse = {}
    for view_name in contract["browse"]:
        app = (
            default
            if default.view.name == view_name
            else TideApp(
                model, records, context, actions=actions, view_name=view_name
            )
        )
        browse[view_name] = {
            "columns": list(app.columns),
            "alignments": {
                name: table_label(
                    app.entity.field(name), "", model.formats
                ).justify
                for name in app.columns
            },
        }

    forms = {}
    for view_name in contract["forms"]:
        view = model.views[view_name]
        screen = RecordEditScreen(
            model,
            records,
            actions,
            context,
            view,
            RecordSession(
                entity=view.entity,
                identity=None,
                original={},
                values={},
                expected_version=None,
                is_new=True,
            ),
        )
        forms[view_name] = {"sections": _tui_sections(screen)}

    return {
        "navigation_views": [view.name for view in default.browse_views],
        "browse": browse,
        "forms": forms,
    }


def _qt_resolution(model: Any, contract: dict[str, Any]) -> dict[str, Any]:
    client = _QtLayoutClient()
    session = _qt_session(model, role=_contract_role(contract))

    browse = {}
    for view_name in contract["browse"]:
        controller = QtBrowseController(model, client, session, view_name=view_name)
        browse[view_name] = {
            "columns": [column.name for column in controller.columns],
            "alignments": {
                column.name: column.alignment for column in controller.columns
            },
        }

    forms = {}
    for view_name in contract["forms"]:
        controller = QtBrowseController(
            model,
            client,
            session,
            view_name=_browse_view_for(model, model.views[view_name].entity),
            form_view_name=view_name,
        )
        forms[view_name] = {"sections": _qt_sections(controller.load_detail(1))}

    return {"browse": browse, "forms": forms}


def _web_resolution(model: Any, contract: dict[str, Any]) -> dict[str, Any]:
    exposures = rest_exposures(model, allowed_operations=REST_OPERATIONS)
    manifest = build_presentation_manifest(
        model,
        _web_session(model, exposures, role=_contract_role(contract)),
        exposures,
        base_path="/api/v1",
    )
    return {
        "navigation": [
            {"label": group.label, "views": [item.view for item in group.items]}
            for group in manifest.navigation
        ],
        "browse": {
            view_name: {
                "columns": [
                    column.name for column in manifest.views[view_name].columns
                ],
                "alignments": {
                    column.name: column.alignment
                    for column in manifest.views[view_name].columns
                },
            }
            for view_name in contract["browse"]
        },
        "forms": {
            view_name: {"sections": _web_sections(manifest.forms[view_name])}
            for view_name in contract["forms"]
        },
        "reports": [
            {
                "name": report.name,
                "title": report.title,
                "kind": report.kind,
                "entity": report.entity,
            }
            for report in manifest.reports.values()
        ],
    }


_RESOLVERS = {
    "shared": _shared_resolution,
    "tui": _tui_resolution,
    "qt": _qt_resolution,
    "web": _web_resolution,
}


# --- section projections --------------------------------------------------


def _shared_sections(model: Any, view_name: str) -> list[dict[str, Any]]:
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


def _tui_sections(screen: RecordEditScreen) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for section in screen.layout_sections:
        if section.kind == "group":
            result.append(
                {
                    "kind": "group",
                    "label": section.label,
                    "rows": [list(row) for row in section.rows],
                }
            )
            continue
        assert section.collection == screen.collection_name
        result.append(
            {
                "kind": "collection",
                "label": section.label,
                "collection": section.collection,
                "columns": list(screen.line_fields),
            }
        )
    return result


def _qt_sections(detail: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for section in detail.sections:
        if isinstance(section, QtDetailGroup):
            result.append(
                {
                    "kind": "group",
                    "label": section.label,
                    "rows": [[field.name for field in row] for row in section.rows],
                }
            )
            continue
        result.append(
            {
                "kind": "collection",
                "label": section.label,
                "collection": section.name,
                "columns": [column.name for column in section.columns],
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
            continue
        result.append(
            {
                "kind": "collection",
                "label": section.label,
                "collection": section.name,
                "columns": [column.name for column in section.columns],
            }
        )
    return result


# --- renderer scaffolding -------------------------------------------------


class _QtLayoutClient:
    """Answer the Qt presenter with empty records.

    The reference contract records layout, not data, and an empty record still
    resolves every group, row, and inline column.
    """

    def get_record(self, entity_name: str, identity: Any) -> Any:
        return SimpleNamespace(values={})


def _browse_view_for(model: Any, entity_name: str) -> str:
    return next(
        view.name
        for view in model.views.values()
        if view.kind == "browse" and view.entity == entity_name
    )


def _contract_role(contract: dict[str, Any]) -> str:
    """Every contract states its own role; there is no default.

    `sales_clerk` used to be the fallback, which is an Invoicing role a second
    application does not define. A contract that forgot to set one would be
    resolved as a role granting nothing, and the failure read as an empty
    capability set rather than as a missing setting.
    """

    role = contract.get("role")
    assert role, "the contract must name the role its renderers resolve as"
    return str(role)


def _qt_session(model: Any, *, role: str) -> TideSessionInfo:
    return TideSessionInfo(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        authentication="renderer-acceptance",
        principal="acceptance:qt",
        roles=(role,),
        entities={
            name: TideEntityCapabilities(
                operations=("list", "get"),
                readable_fields=tuple(entity.fields),
            )
            for name, entity in model.entities.items()
        },
    )


def _web_session(
    model: Any,
    exposures: Any,
    *,
    role: str,
) -> TideSessionInfo:
    return TideSessionInfo(
        application=model.name,
        application_version=model.version,
        schema_version=model.schema_version,
        authentication="renderer-acceptance",
        principal="acceptance:all-fields",
        roles=(role,),
        reports=tuple(model.reports),
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
