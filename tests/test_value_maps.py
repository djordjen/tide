"""A `values:` map captions a stored code without changing what is stored.

A legacy integer column usually carries an enumeration whose member names live
in the application that wrote the rows. This says what they are: the column
stays an integer in SQL, in filters and over REST, and only what a reader sees
and what a writer may choose come from the map.

Every layer is covered here rather than beside its own module, because the
point of the feature is that one declaration reaches all of them -- and the
way that breaks is one layer quietly not reading it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import DataTable, Select

from tide import CompilationFailed, compile_project
from tide.api.contracts import TideEntityCapabilities, TideSessionInfo
from tide.api.openapi import rest_exposures
from tide.api.presentation import build_presentation_manifest
from tide.data import InMemoryRepository
from tide.labels import declared_values, value_caption
from tide.runtime import Channel, Principal, RequestContext, ValidationFailed
from tide.services import RecordsService
from tide.tui import TideApp

MANIFEST = (
    'schema_version: "0.1"\n'
    "application: {name: Captioned, version: 0.1.0}\n"
    "database: {mode: managed}\n"
    "model: {paths: [models]}\n"
    "views: {paths: [views]}\n"
    "security: {paths: [security]}\n"
)

POLICIES = "permissions:\n- demo.all\nroles:\n  operator:\n    grants:\n    - demo.all\n"

BROWSE = (
    "view: demo.Item.browse\nentity: demo.Item\nkind: browse\n"
    "columns:\n- name\n- status\n"
)

EDIT = (
    "view: demo.Item.edit\nentity: demo.Item\nkind: form\n"
    "layout:\n- group: Item\n  rows:\n  - - name\n    - status\n"
)


def _project(tmp_path: Path, status_field: str, name: str = "captioned") -> Path:
    project = tmp_path / name
    for relative, text in (
        ("tide.yaml", MANIFEST),
        ("security/policies.yaml", POLICIES),
        ("views/item-browse.yaml", BROWSE),
        ("views/item-edit.yaml", EDIT),
        (
            "models/item.yaml",
            "entity: demo.Item\n"
            "display: name\n"
            # REST too: the manifest the browser reads is built only for
            # entities the API exposes, so a TUI-only entity has no columns
            # there to carry anything.
            "expose:\n"
            "  tui: true\n"
            "  rest:\n"
            "    operations: [list, get, update]\n"
            "permissions: {list: demo.all, read: demo.all, create: demo.all,"
            " update: demo.all, delete: demo.all}\n"
            "fields:\n"
            "  id: {type: integer, primary_key: true}\n"
            "  name: {type: string, length: 40, required: true}\n"
            f"{status_field}",
        ),
    ):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return project


CAPTIONED = (
    "  status:\n"
    "    type: integer\n"
    "    values:\n"
    "    - {value: 0, label: Ordered}\n"
    "    - {value: 2, label: In repair}\n"
)


def test_a_captioned_field_keeps_the_type_it_stores(tmp_path: Path) -> None:
    model = compile_project(_project(tmp_path, CAPTIONED))
    metadata = model.entity("demo.Item").field("status").metadata

    assert metadata["type"] == "integer"
    assert declared_values(metadata) == ((0, "Ordered"), (2, "In repair"))
    assert value_caption(metadata, 2) == "In repair"
    # A code nobody captioned is shown as itself. A legacy column holds values
    # nobody wrote down, and blanking one hides that it is there.
    assert value_caption(metadata, 7) is None


@pytest.mark.parametrize(
    ("declaration", "code"),
    [
        # A quantity is not a code, and `choice` already names its members.
        (
            "  status:\n    type: decimal\n    precision: 6\n    scale: 2\n"
            "    values:\n    - {value: 0, label: Ordered}\n",
            "TIDE277",
        ),
        # `true` must not slip into an integer map: bool subclasses int.
        (
            "  status:\n    type: integer\n"
            "    values:\n    - {value: true, label: Ordered}\n",
            "TIDE278",
        ),
        (
            "  status:\n    type: integer\n    values:\n"
            "    - {value: 0, label: Ordered}\n    - {value: 0, label: Other}\n",
            "TIDE279",
        ),
    ],
)
def test_a_value_map_that_cannot_work_is_refused(
    tmp_path: Path, declaration: str, code: str
) -> None:
    with pytest.raises(CompilationFailed) as caught:
        compile_project(_project(tmp_path, declaration, name=code.lower()))

    assert code in {item.code for item in caught.value.diagnostics}
    assert all(item.location.line >= 1 for item in caught.value.diagnostics)


def _service(project: Path) -> tuple[RecordsService, RequestContext]:
    model = compile_project(project)
    return (
        RecordsService(model, InMemoryRepository()),
        RequestContext(
            principal=Principal("p", roles=frozenset({"operator"})),
            channel=Channel.TUI,
        ),
    )


def test_the_boundary_accepts_a_declared_code_and_refuses_any_other(
    tmp_path: Path,
) -> None:
    """Constraining is what makes the map a contract rather than decoration.

    It is enforced in the service, so it holds for the terminal, the browser,
    REST and MCP alike instead of once per renderer.
    """

    records, context = _service(_project(tmp_path, CAPTIONED))

    accepted = records.create("demo.Item", context, {"name": "a", "status": 2})
    records.commit(accepted, context)

    refused = records.create("demo.Item", context, {"name": "b", "status": 7})
    with pytest.raises(ValidationFailed) as caught:
        records.commit(refused, context)
    assert [issue.rule for issue in caught.value.issues] == ["value"]


def test_the_manifest_carries_the_map_to_the_browser(tmp_path: Path) -> None:
    """The browser has no model, only the manifest.

    Asserted on a field that declares a map, because a field without one
    serialises to the same empty list whether the projection reads the
    declaration or not -- which is exactly how the first version of this
    passed while carrying nothing.
    """

    model = compile_project(_project(tmp_path, CAPTIONED, name="manifest"))
    exposures = rest_exposures(model, allowed_operations=frozenset({"list", "get", "update"}))
    manifest = build_presentation_manifest(
        model,
        TideSessionInfo(
            application=model.name,
            application_version=model.version,
            schema_version=model.schema_version,
            authentication="test",
            principal="p",
            roles=("operator",),
            reports=(),
            entities={
                "demo.Item": TideEntityCapabilities(
                    operations=("list", "get", "update"),
                    draft_operations=("update",),
                    readable_fields=("id", "name", "status"),
                    writable_fields=("name", "status"),
                )
            },
        ),
        exposures,
        base_path="/api/v1",
    )

    column = next(
        item
        for item in manifest.views["demo.Item.browse"].columns
        if item.name == "status"
    )
    assert [(item.value, item.label) for item in column.values] == [
        (0, "Ordered"),
        (2, "In repair"),
    ]
    field = manifest.forms["demo.Item.edit"].fields["status"]
    assert [item.value for item in field.values] == [0, 2]


def test_the_terminal_shows_the_caption_and_offers_only_declared_codes(
    tmp_path: Path,
) -> None:
    """The two halves a reader meets: the grid reads, the form writes."""

    project = _project(tmp_path, CAPTIONED)
    model = compile_project(project)
    repository = InMemoryRepository()
    repository.seed("demo.Item", ({"id": 1, "name": "Bridge PC", "status": 2},))
    application = TideApp(
        model,
        RecordsService(model, repository),
        RequestContext(
            principal=Principal("p", roles=frozenset({"operator"})),
            channel=Channel.TUI,
        ),
    )

    async def drive() -> tuple[list[str], list[tuple[str, object]]]:
        async with application.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            table = application.query_one(DataTable)
            row = [str(cell) for cell in table.get_row(list(table.rows)[0])]
            application.open_record(1)
            for _ in range(5):
                await pilot.pause()
            editor = application.screen.query_one("#field-status", Select)
            return row, [(str(label), code) for label, code in editor._options]

    row, options = asyncio.run(drive())

    assert "In repair" in row and "2" not in row
    # The codes stay integers: a select that handed back "2" would be sending
    # a different value than the one the map declared.
    assert options[1:] == [("Ordered", 0), ("In repair", 2)]
