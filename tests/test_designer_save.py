from __future__ import annotations

import json
from pathlib import Path

import pytest

from tide import compile_project
from tide.development import (
    DesignerCommandBatch,
    DesignerDocumentReference,
    DesignerSaveApproval,
    DesignerSaveError,
    DesignerSaveService,
    DesignerService,
    DesignerSetValueCommand,
)
from tide.development import designer_save as designer_save_module


def test_prepare_binds_exact_candidate_without_writing(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    before = _all_bytes(project)
    session = _changed_session(project)

    preparation = DesignerSaveService().prepare(session)

    assert preparation.ready is True
    assert preparation.writes_performed is False
    assert preparation.base_state == "current"
    assert preparation.changed_files == ("models/item.yaml",)
    assert preparation.approval_prompt == f"SAVE {preparation.approval_id}"
    assert preparation.diff_sha256 is not None
    assert preparation.change_fingerprint is not None
    assert preparation.artifacts[0].base_sha256 != (
        preparation.artifacts[0].candidate_sha256
    )
    assert _all_bytes(project) == before


def test_approved_save_replaces_yaml_and_records_receipt(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    session = _changed_session(project)
    service = DesignerSaveService()
    preparation = service.prepare(session)

    result = service.save(
        session,
        DesignerSaveApproval.from_preparation(preparation),
    )

    assert result.changed_files == ("models/item.yaml",)
    assert 'label: "Stock items"' in (project / "models" / "item.yaml").read_text(
        encoding="utf-8"
    )
    receipt = json.loads((project / result.receipt_path).read_text(encoding="utf-8"))
    assert receipt["approval_id"] == preparation.approval_id
    assert receipt["candidate_fingerprint"] == result.candidate_fingerprint
    assert receipt["artifacts"][0]["path"] == "models/item.yaml"
    assert compile_project(project).name == "Designer Fixture"
    saved = session.snapshot()
    assert saved.dirty is False
    assert saved.can_undo is True
    assert session.undo().dirty is True
    assert not (project / DesignerSaveService.lock_name).exists()
    assert not tuple(tmp_path.glob(".application.tide-designer-save-*"))


def test_tampered_approval_is_rejected_without_writing(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    before = _all_bytes(project)
    session = _changed_session(project)
    service = DesignerSaveService()
    preparation = service.prepare(session)
    approval = DesignerSaveApproval.from_preparation(preparation).model_copy(
        update={"candidate_fingerprint": "sha256:tampered"}
    )

    with pytest.raises(DesignerSaveError, match="TIDEDSAVE004"):
        service.save(session, approval)

    assert _all_bytes(project) == before


def test_external_source_change_makes_approved_base_stale(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    session = _changed_session(project)
    service = DesignerSaveService()
    preparation = service.prepare(session)
    entity = project / "models" / "item.yaml"
    entity.write_text(
        entity.read_text(encoding="utf-8") + "# External edit.\n",
        encoding="utf-8",
    )
    externally_edited = _all_bytes(project)

    with pytest.raises(DesignerSaveError, match="TIDEDSAVE003"):
        service.save(
            session,
            DesignerSaveApproval.from_preparation(preparation),
        )

    assert _all_bytes(project) == externally_edited
    assert not (project / DesignerSaveService.lock_name).exists()


def test_last_moment_source_change_is_checked_before_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _write_project(tmp_path)
    session = _changed_session(project)
    service = DesignerSaveService()
    preparation = service.prepare(session)
    entity = project / "models" / "item.yaml"
    real_assert = DesignerSaveService._assert_live_base
    calls = 0

    def edit_after_recheck(state, expected_fingerprint: str) -> None:
        nonlocal calls
        real_assert(state, expected_fingerprint)
        calls += 1
        if calls == 2:
            entity.write_text(
                entity.read_text(encoding="utf-8") + "# Last-moment edit.\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(
        DesignerSaveService,
        "_assert_live_base",
        staticmethod(edit_after_recheck),
    )

    with pytest.raises(DesignerSaveError, match="TIDEDSAVE003"):
        service.save(
            session,
            DesignerSaveApproval.from_preparation(preparation),
        )

    assert "# Last-moment edit." in entity.read_text(encoding="utf-8")
    assert 'label: "Stock items"' not in entity.read_text(encoding="utf-8")
    assert not (project / DesignerSaveService.lock_name).exists()
    assert not (project / ".tide").exists()


def test_invalid_and_unchanged_candidates_are_blocked(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    unchanged = DesignerService(project).open_session()
    invalid = DesignerService(project).open_session()
    invalid.execute(
        DesignerSetValueCommand(
            target=_entity(),
            path=("display",),
            value="{missing}",
        )
    )

    unchanged_preparation = DesignerSaveService().prepare(unchanged)
    invalid_preparation = DesignerSaveService().prepare(invalid)

    assert unchanged_preparation.ready is False
    assert {item.code for item in unchanged_preparation.blockers} == {"TIDEDSAVE002"}
    assert invalid_preparation.ready is False
    assert {item.code for item in invalid_preparation.blockers} == {"TIDEDSAVE001"}


def test_approval_is_bound_to_current_session_candidate(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    session = _changed_session(project)
    service = DesignerSaveService()
    preparation = service.prepare(session)
    session.execute(
        DesignerSetValueCommand(
            target=_entity(),
            path=("display",),
            value="Item {name}",
        )
    )

    with pytest.raises(DesignerSaveError, match="TIDEDSAVE004"):
        service.save(
            session,
            DesignerSaveApproval.from_preparation(preparation),
        )

    assert 'label: "Items"' in (project / "models" / "item.yaml").read_text(
        encoding="utf-8"
    )


def test_approval_is_bound_to_canonical_project_path(tmp_path: Path) -> None:
    first = _write_project(tmp_path / "first")
    second = _write_project(tmp_path / "second")
    first_session = _changed_session(first)
    second_session = _changed_session(second)
    service = DesignerSaveService()
    approval = DesignerSaveApproval.from_preparation(service.prepare(first_session))

    with pytest.raises(DesignerSaveError, match="TIDEDSAVE004"):
        service.save(second_session, approval)

    assert 'label: "Items"' in (second / "models" / "item.yaml").read_text(
        encoding="utf-8"
    )


def test_existing_lock_blocks_save_and_is_preserved(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    session = _changed_session(project)
    service = DesignerSaveService()
    preparation = service.prepare(session)
    lock = project / DesignerSaveService.lock_name
    lock.write_text("other-operation\n", encoding="utf-8")

    with pytest.raises(DesignerSaveError, match="TIDEDSAVE006"):
        service.save(
            session,
            DesignerSaveApproval.from_preparation(preparation),
        )

    assert lock.read_text(encoding="utf-8") == "other-operation\n"
    assert 'label: "Items"' in (project / "models" / "item.yaml").read_text(
        encoding="utf-8"
    )


def test_replacement_failure_rolls_back_all_yaml_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _write_project(tmp_path)
    before = _all_bytes(project)
    session = DesignerService(project).open_session()
    session.execute_batch(
        DesignerCommandBatch(
            commands=(
                DesignerSetValueCommand(
                    target=_entity(),
                    path=("label",),
                    value="Stock items",
                ),
                DesignerSetValueCommand(
                    target=DesignerDocumentReference(
                        kind="view",
                        name="core.item.browse",
                    ),
                    path=("columns",),
                    value=["name", "id"],
                ),
            )
        )
    )
    service = DesignerSaveService()
    preparation = service.prepare(session)
    real_replace = designer_save_module._replace_file
    failure_injected = False

    def fail_second_candidate(source: Path, destination: Path) -> None:
        nonlocal failure_injected
        if (
            not failure_injected
            and "candidate" in source.parts
            and destination.name == "item-browse.yaml"
        ):
            failure_injected = True
            raise OSError("injected replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr(designer_save_module, "_replace_file", fail_second_candidate)

    with pytest.raises(DesignerSaveError, match="TIDEDSAVE007"):
        service.save(
            session,
            DesignerSaveApproval.from_preparation(preparation),
        )

    assert failure_injected is True
    assert _all_bytes(project) == before
    assert not (project / DesignerSaveService.lock_name).exists()
    assert not (project / ".tide").exists()
    assert not tuple(tmp_path.glob(".application.tide-designer-save-*"))


def test_unsafe_receipt_directory_refuses_before_source_replacement(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    unsafe = project / ".tide"
    unsafe.write_text("not a directory\n", encoding="utf-8")
    before = _all_bytes(project)
    session = _changed_session(project)
    service = DesignerSaveService()
    preparation = service.prepare(session)

    with pytest.raises(DesignerSaveError, match="TIDEDSAVE009"):
        service.save(
            session,
            DesignerSaveApproval.from_preparation(preparation),
        )

    assert _all_bytes(project) == before
    assert not (project / DesignerSaveService.lock_name).exists()


def _changed_session(project: Path):
    session = DesignerService(project).open_session()
    session.execute(
        DesignerSetValueCommand(
            target=_entity(),
            path=("label",),
            value="Stock items",
        )
    )
    return session


def _entity() -> DesignerDocumentReference:
    return DesignerDocumentReference(kind="entity", name="core.Item")


def _all_bytes(project: Path) -> dict[str, bytes]:
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
        """schema_version: "0.1"
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
