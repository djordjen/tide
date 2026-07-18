from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter, ValidationError
import pytest

from tide.development import (
    DesignerCommand,
    DesignerCommandBatch,
    DesignerDocumentReference,
    DesignerError,
    DesignerInsertSequenceItemCommand,
    DesignerMoveSequenceItemCommand,
    DesignerRemoveValueCommand,
    DesignerReplaceDocumentSourceCommand,
    DesignerRenameKeyCommand,
    DesignerReorderMappingCommand,
    DesignerService,
    DesignerSetValueCommand,
)


def test_designer_command_union_has_a_discriminated_json_schema() -> None:
    schema = TypeAdapter(DesignerCommand).json_schema()

    assert schema["discriminator"]["propertyName"] == "operation"
    assert set(schema["discriminator"]["mapping"]) == {
        "set_value",
        "remove_value",
        "rename_key",
        "reorder_mapping",
        "insert_sequence_item",
        "move_sequence_item",
        "replace_document_source",
    }


def test_designer_session_opens_without_writing(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    before = _source_bytes(project)

    session = DesignerService(project).open_session()
    snapshot = session.snapshot()

    assert snapshot.valid is True
    assert snapshot.dirty is False
    assert snapshot.diff == ""
    assert snapshot.writes_performed is False
    assert snapshot.temporary_candidate_deleted is True
    assert snapshot.external_commands_executed is False
    assert snapshot.application_database_accessed is False
    assert snapshot.round_trip_yaml_used is True
    assert snapshot.entity_count == 1
    catalog = session.documents()
    assert catalog.writes_performed is False
    assert catalog.candidate_fingerprint == snapshot.candidate_fingerprint
    assert {
        (item.target.kind, item.target.name)
        for item in catalog.documents
        if item.target.kind != "source"
    } == {
        ("project", None),
        ("entity", "core.Item"),
        ("view", "core.item.browse"),
    }
    assert _source_bytes(project) == before


def test_set_value_preserves_comments_quotes_and_source_files(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    before = _source_bytes(project)
    session = DesignerService(project).open_session()

    snapshot = session.execute(
        DesignerSetValueCommand(
            target=_entity(),
            path=("label",),
            value="Stock items",
        )
    )
    content = session.document(_entity())

    assert snapshot.valid is True
    assert snapshot.dirty is True
    assert snapshot.changed_files == ("models/item.yaml",)
    assert '-label: "Items"' in snapshot.diff
    assert '+label: "Stock items"' in snapshot.diff
    assert "# Kept beside the label." in content.content
    assert 'label: "Stock items"' in content.content
    assert content.writes_performed is False
    assert _source_bytes(project) == before


def test_round_trip_and_expert_source_preserve_crlf_documents(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    for path in project.rglob("*.yaml"):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(content.replace(b"\n", b"\r\n"))
    session = DesignerService(project).open_session()

    changed = session.execute(
        DesignerSetValueCommand(
            target=_entity(),
            path=("label",),
            value="Stock items",
        )
    )
    assert changed.dirty
    restored = session.execute(
        DesignerSetValueCommand(
            target=_entity(),
            path=("label",),
            value="Items",
        )
    )
    assert not restored.dirty
    assert "\r\n" in session.document(_entity()).content

    source = session.document(_entity()).content.replace("\r\n", "\n")
    invalid = session.execute(
        DesignerReplaceDocumentSourceCommand(
            target=_entity(),
            source=source.replace('display: "{name}"', 'display: "{missing}"'),
        )
    )
    assert not invalid.valid
    assert "\r\n" in session.document(_entity()).content


def test_invalid_intermediate_state_is_visible_and_undoable(tmp_path: Path) -> None:
    session = DesignerService(_write_project(tmp_path)).open_session()

    invalid = session.execute(
        DesignerSetValueCommand(
            target=_entity(),
            path=("display",),
            value="{missing}",
        )
    )

    assert invalid.valid is False
    assert invalid.can_undo is True
    assert {item["code"] for item in invalid.diagnostics} == {"TIDE215"}

    restored = session.undo()
    assert restored.valid is True
    assert restored.dirty is False
    assert restored.can_redo is True

    repeated = session.redo()
    assert repeated.valid is False
    assert repeated.can_undo is True


def test_command_batch_is_one_history_entry_and_can_rename_a_field(
    tmp_path: Path,
) -> None:
    session = DesignerService(_write_project(tmp_path)).open_session()
    batch = DesignerCommandBatch(
        label="Rename the display field",
        commands=(
            DesignerRenameKeyCommand(
                target=_entity(),
                path=("fields",),
                old_key="name",
                new_key="title",
            ),
            DesignerSetValueCommand(
                target=_entity(),
                path=("display",),
                value="{title}",
            ),
            DesignerSetValueCommand(
                target=DesignerDocumentReference(
                    kind="view",
                    name="core.item.browse",
                ),
                path=("columns", 1),
                value="title",
            ),
        ),
    )

    changed = session.execute_batch(batch)

    assert changed.valid is True
    assert changed.undo_depth == 1
    assert "title:" in session.document(_entity()).content
    assert "name:" not in session.document(_entity()).content
    assert session.undo().dirty is False


def test_structural_batch_failure_is_atomic(tmp_path: Path) -> None:
    session = DesignerService(_write_project(tmp_path)).open_session()
    before = session.snapshot()
    batch = DesignerCommandBatch(
        commands=(
            DesignerSetValueCommand(
                target=_entity(),
                path=("label",),
                value="Changed",
            ),
            DesignerRemoveValueCommand(
                target=_entity(),
                path=("fields", "missing"),
            ),
        )
    )

    with pytest.raises(DesignerError, match="TIDEDES005"):
        session.execute_batch(batch)

    after = session.snapshot()
    assert after.candidate_fingerprint == before.candidate_fingerprint
    assert after.undo_depth == 0
    assert after.dirty is False


def test_mapping_and_sequence_order_commands_compile(tmp_path: Path) -> None:
    session = DesignerService(_write_project(tmp_path)).open_session()

    fields = session.execute(
        DesignerReorderMappingCommand(
            target=_entity(),
            path=("fields",),
            keys=("name", "id"),
        )
    )
    columns = session.execute(
        DesignerMoveSequenceItemCommand(
            target=DesignerDocumentReference(kind="view", name="core.item.browse"),
            path=("columns",),
            from_index=1,
            to_index=0,
        )
    )

    assert fields.valid is True
    assert columns.valid is True
    assert columns.changed_files == ("models/item.yaml", "views/item-browse.yaml")
    assert session.document(_entity()).content.index("  name:") < session.document(
        _entity()
    ).content.index("  id:")


def test_sequence_insert_can_request_compact_flow_style(tmp_path: Path) -> None:
    session = DesignerService(_write_project(tmp_path)).open_session()
    target = DesignerDocumentReference(kind="view", name="core.item.browse")

    changed = session.execute_batch(
        DesignerCommandBatch(
            commands=(
                DesignerSetValueCommand(
                    target=target,
                    path=("settings",),
                    value={"rows": []},
                ),
                DesignerInsertSequenceItemCommand(
                    target=target,
                    path=("settings", "rows"),
                    index=0,
                    value=["id"],
                    flow_style=True,
                ),
            )
        )
    )

    assert changed.valid
    assert "  - [id]" in session.document(target).content


def test_designer_session_exposes_only_a_valid_resolved_candidate_model(
    tmp_path: Path,
) -> None:
    session = DesignerService(_write_project(tmp_path)).open_session()

    assert session.application_model().entity("core.Item").name == "core.Item"

    session.execute(
        DesignerSetValueCommand(
            target=_entity(),
            path=("display",),
            value="missing",
        )
    )
    with pytest.raises(DesignerError, match="TIDEDES013"):
        session.application_model()


def test_source_reference_can_address_non_semantic_yaml(tmp_path: Path) -> None:
    session = DesignerService(_write_project(tmp_path)).open_session()
    target = DesignerDocumentReference(kind="source", name="security/policies.yaml")

    changed = session.execute(
        DesignerSetValueCommand(
            target=target,
            path=("roles", "reader", "grants"),
            value=["core.item.read"],
        )
    )

    assert changed.valid is True
    assert changed.changed_files == ("security/policies.yaml",)
    assert "core.item.read" in session.document(target).content


def test_replace_document_source_is_exact_in_memory_and_undoable(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    before = _source_bytes(project)
    session = DesignerService(project).open_session()
    original = session.document(_entity()).content
    replacement = original.replace('label: "Items"', 'label: "Expert items"')

    changed = session.execute(
        DesignerReplaceDocumentSourceCommand(
            target=_entity(),
            source=replacement,
        )
    )

    assert changed.valid
    assert changed.dirty
    assert changed.can_undo
    assert '+label: "Expert items"' in changed.diff
    assert session.document(_entity()).content == replacement
    assert session.undo().dirty is False
    assert session.document(_entity()).content == original
    assert _source_bytes(project) == before


def test_replace_document_source_rejects_malformed_yaml_and_identity_change(
    tmp_path: Path,
) -> None:
    session = DesignerService(_write_project(tmp_path)).open_session()
    original = session.document(_entity()).content

    with pytest.raises(DesignerError, match="TIDEDES003"):
        session.execute(
            DesignerReplaceDocumentSourceCommand(
                target=_entity(),
                source="entity: [\n",
            )
        )
    with pytest.raises(DesignerError, match="TIDEDES012"):
        session.execute(
            DesignerReplaceDocumentSourceCommand(
                target=_entity(),
                source=original.replace("core.Item", "core.Renamed", 1),
            )
        )

    assert session.snapshot().dirty is False
    assert session.document(_entity()).content == original


def test_source_reference_rejects_path_escape() -> None:
    with pytest.raises(ValidationError, match="safe relative paths"):
        DesignerDocumentReference(kind="source", name="../outside.yaml")
    with pytest.raises(ValidationError, match="portable path parts"):
        DesignerDocumentReference(kind="source", name="C:/outside.yaml")


def test_undo_and_redo_require_available_history(tmp_path: Path) -> None:
    session = DesignerService(_write_project(tmp_path)).open_session()

    with pytest.raises(DesignerError, match="TIDEDES010"):
        session.undo()
    with pytest.raises(DesignerError, match="TIDEDES011"):
        session.redo()


def _entity() -> DesignerDocumentReference:
    return DesignerDocumentReference(kind="entity", name="core.Item")


def _source_bytes(project: Path) -> dict[str, bytes]:
    return {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }


def _write_project(tmp_path: Path) -> Path:
    project = tmp_path / "application"
    (project / "models").mkdir(parents=True)
    (project / "views").mkdir()
    (project / "security").mkdir()
    (project / "tide.yaml").write_text(
        """# Project comment.
schema_version: "0.1"
application:
  name: Designer Fixture
  version: 0.1.0
model:
  paths: [models]
views:
  paths: [views]
security:
  paths: [security]
""",
        encoding="utf-8",
    )
    (project / "models" / "item.yaml").write_text(
        """entity: core.Item
# Kept beside the label.
label: "Items"
display: "{name}"
expose:
  tui: true
permissions:
  list: core.item.read
  read: core.item.read
fields:
  id:
    type: integer
    primary_key: true
  name:
    type: string
    required: true
""",
        encoding="utf-8",
    )
    (project / "views" / "item-browse.yaml").write_text(
        """view: core.item.browse
entity: core.Item
kind: browse
columns: [id, name]
""",
        encoding="utf-8",
    )
    (project / "security" / "policies.yaml").write_text(
        """permissions:
  - core.item.read
roles:
  reader:
    grants: [core.item.read]
""",
        encoding="utf-8",
    )
    return project
