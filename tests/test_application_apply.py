from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import tide.development.application_apply as application_apply
from tide import compile_project
from tide.development import (
    ApplicationApplyApproval,
    ApplicationApplyError,
    ApplicationApplyService,
    ApplicationGenerationPlan,
    ApplicationMaterializationService,
)


def test_prepare_binds_verified_candidate_without_writing(tmp_path: Path) -> None:
    marker = tmp_path / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    preparation = ApplicationApplyService(tmp_path).prepare(_minimal_plan())

    assert preparation.ready is True
    assert preparation.writes_performed is False
    assert preparation.approval_required is True
    assert preparation.application_id == "generated-app"
    assert preparation.target_path == "applications/generated-app"
    assert preparation.destination_state == "absent"
    assert preparation.base_fingerprint is not None
    assert preparation.candidate_fingerprint is not None
    assert preparation.approval_id is not None
    assert preparation.approval_prompt == f"APPLY {preparation.approval_id}"
    assert preparation.artifact_count > 0
    assert not preparation.blockers
    assert not (tmp_path / "applications").exists()
    assert marker.read_text(encoding="utf-8") == "keep"


def test_approved_candidate_is_published_with_receipt_and_cannot_replay(
    tmp_path: Path,
) -> None:
    service = ApplicationApplyService(tmp_path)
    plan = _minimal_plan()
    preparation = service.prepare(plan)
    approval = ApplicationApplyApproval.from_preparation(preparation)

    result = service.apply(plan, approval)

    target = tmp_path / "applications" / "generated-app"
    assert result.applied is True
    assert result.workspace_writes_performed is True
    assert result.approval_id == preparation.approval_id
    assert result.candidate_fingerprint == preparation.candidate_fingerprint
    assert result.receipt_path == "applications/generated-app/.tide-apply.json"
    assert (target / "tide.yaml").is_file()
    assert compile_project(target).name == "Generated App"
    receipt = json.loads((target / ".tide-apply.json").read_text(encoding="utf-8"))
    assert receipt["workspace_writes_performed"] is True
    assert receipt["approval_id"] == preparation.approval_id
    assert receipt["proposal_id"] == preparation.proposal_id
    assert receipt["candidate_fingerprint"] == preparation.candidate_fingerprint
    assert len(receipt["artifacts"]) == preparation.artifact_count
    assert not list((tmp_path / "applications").glob(".*.tide-candidate-*"))
    assert not list((tmp_path / "applications").glob(".*.tide-apply.lock"))

    with pytest.raises(ApplicationApplyError, match="TIDEAPPLY003"):
        service.apply(plan, approval)


def test_apply_rejects_tampered_approval_without_writing(tmp_path: Path) -> None:
    service = ApplicationApplyService(tmp_path)
    plan = _minimal_plan()
    preparation = service.prepare(plan)
    approval = ApplicationApplyApproval.from_preparation(preparation).model_copy(
        update={"candidate_fingerprint": "sha256:" + ("0" * 64)}
    )

    with pytest.raises(ApplicationApplyError, match="TIDEAPPLY004"):
        service.apply(plan, approval)

    assert not (tmp_path / "applications").exists()


def test_apply_rejects_destination_created_after_approval(tmp_path: Path) -> None:
    service = ApplicationApplyService(tmp_path)
    plan = _minimal_plan()
    preparation = service.prepare(plan)
    approval = ApplicationApplyApproval.from_preparation(preparation)
    target = tmp_path / "applications" / "generated-app"
    target.mkdir(parents=True)
    marker = target / "owned.txt"
    marker.write_text("do not replace", encoding="utf-8")

    with pytest.raises(ApplicationApplyError, match="TIDEAPPLY003"):
        service.apply(plan, approval)

    assert marker.read_text(encoding="utf-8") == "do not replace"


def test_approval_cannot_move_to_a_different_workspace(tmp_path: Path) -> None:
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    plan = _minimal_plan()
    approval = ApplicationApplyApproval.from_preparation(
        ApplicationApplyService(first_workspace).prepare(plan)
    )

    with pytest.raises(ApplicationApplyError, match="TIDEAPPLY004"):
        ApplicationApplyService(second_workspace).apply(plan, approval)

    assert not (first_workspace / "applications").exists()
    assert not (second_workspace / "applications").exists()


def test_prepare_rejects_case_insensitive_existing_target(tmp_path: Path) -> None:
    existing = tmp_path / "applications" / "GENERATED-APP"
    existing.mkdir(parents=True)

    preparation = ApplicationApplyService(tmp_path).prepare(_minimal_plan())

    assert preparation.ready is False
    assert preparation.destination_state == "existing"
    assert "TIDEAPPLY003" in {blocker.code for blocker in preparation.blockers}
    assert existing.is_dir()


def test_prepare_rejects_unsafe_applications_root(tmp_path: Path) -> None:
    applications = tmp_path / "applications"
    applications.write_text("externally owned", encoding="utf-8")

    preparation = ApplicationApplyService(tmp_path).prepare(_minimal_plan())

    assert preparation.ready is False
    assert preparation.destination_state == "invalid"
    assert "TIDEAPPLY002" in {blocker.code for blocker in preparation.blockers}
    assert applications.read_text(encoding="utf-8") == "externally owned"


def test_prepare_detects_tampered_preview_artifacts(tmp_path: Path) -> None:
    plan = _minimal_plan()
    preview = ApplicationMaterializationService().preview(plan)
    first = preview.artifacts[0]
    tampered = first.model_copy(update={"content": first.content + "# changed\n"})
    materialization = _FixedMaterialization(
        preview.model_copy(update={"artifacts": (tampered, *preview.artifacts[1:])})
    )

    preparation = ApplicationApplyService(
        tmp_path,
        materialization=materialization,  # type: ignore[arg-type]
    ).prepare(plan)

    assert preparation.ready is False
    assert "TIDEAPPLY004" in {blocker.code for blocker in preparation.blockers}
    assert not (tmp_path / "applications").exists()


def test_apply_rejects_candidate_changed_after_preparation(tmp_path: Path) -> None:
    first_plan = _minimal_plan(name="Generated App")
    changed_plan = _minimal_plan(name="Changed Generated App")
    materialization = _SequenceMaterialization(
        ApplicationMaterializationService().preview(first_plan),
        ApplicationMaterializationService().preview(changed_plan),
    )
    service = ApplicationApplyService(
        tmp_path,
        materialization=materialization,  # type: ignore[arg-type]
    )
    preparation = service.prepare(first_plan)
    approval = ApplicationApplyApproval.from_preparation(preparation)

    with pytest.raises(ApplicationApplyError, match="TIDEAPPLY004"):
        service.apply(first_plan, approval)

    assert not (tmp_path / "applications").exists()


def test_publish_failure_cleans_stage_lock_and_new_applications_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ApplicationApplyService(tmp_path)
    plan = _minimal_plan()
    approval = ApplicationApplyApproval.from_preparation(service.prepare(plan))

    def fail_publish(_stage: Path, _target: Path) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr(application_apply, "_publish_candidate", fail_publish)

    with pytest.raises(ApplicationApplyError, match="TIDEAPPLY007"):
        service.apply(plan, approval)

    assert not (tmp_path / "applications").exists()


def test_existing_apply_lock_is_not_removed_by_contender(tmp_path: Path) -> None:
    service = ApplicationApplyService(tmp_path)
    plan = _minimal_plan()
    approval = ApplicationApplyApproval.from_preparation(service.prepare(plan))
    applications = tmp_path / "applications"
    applications.mkdir()
    lock = applications / ".generated-app.tide-apply.lock"
    lock.write_text("other-approval\n", encoding="utf-8")

    with pytest.raises(ApplicationApplyError, match="TIDEAPPLY006"):
        service.apply(plan, approval)

    assert lock.read_text(encoding="utf-8") == "other-approval\n"
    assert not (applications / "generated-app").exists()


def test_application_id_cannot_escape_applications_root() -> None:
    raw = _minimal_plan().model_dump(mode="json")
    raw["operations"][0]["application_id"] = "../outside"

    with pytest.raises(ValidationError, match="application_id"):
        ApplicationGenerationPlan.model_validate(raw)


class _FixedMaterialization:
    def __init__(self, preview: Any) -> None:
        self.preview_result = preview

    def preview(self, _plan: ApplicationGenerationPlan) -> Any:
        return self.preview_result


class _SequenceMaterialization:
    def __init__(self, *previews: Any) -> None:
        self.previews = iter(previews)

    def preview(self, _plan: ApplicationGenerationPlan) -> Any:
        return next(self.previews)


def _minimal_plan(
    *,
    name: str = "Generated App",
) -> ApplicationGenerationPlan:
    return ApplicationGenerationPlan.model_validate(
        {
            "operations": [
                {
                    "operation": "create_application",
                    "application_id": "generated-app",
                    "name": name,
                },
                {
                    "operation": "define_entity",
                    "entity": "core.Item",
                    "display": "{name}",
                    "fields": [
                        {
                            "name": "id",
                            "type": "integer",
                            "primary_key": True,
                        },
                        {
                            "name": "name",
                            "type": "string",
                            "required": True,
                            "length": 100,
                        },
                    ],
                    "expose_tui": True,
                },
            ]
        }
    )
