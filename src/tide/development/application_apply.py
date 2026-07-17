"""Explicit approval and atomic publication for new generated applications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from tempfile import mkdtemp
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tide.compiler.compiler import compile_project
from tide.development.generation import ApplicationGenerationPlan
from tide.development.materialization import (
    ApplicationGenerationPreview,
    ApplicationMaterializationService,
    CandidateCheck,
    _content_hash,
    _fingerprint_artifacts,
    _fingerprint_empty_base,
    _validated_relative_path,
    _write_candidate,
)
from tide.diagnostics import CompilationFailed


class ApplicationApplyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApplicationApplyIssue(ApplicationApplyModel):
    """One stable reason a candidate cannot be approved or applied."""

    code: str
    message: str


class ApplicationApplyPreparation(ApplicationApplyModel):
    """A no-write approval challenge bound to one exact candidate and base."""

    ready: bool
    approval_required: Literal[True] = True
    writes_performed: Literal[False] = False
    proposal_id: str
    application_id: str | None = None
    target_path: str | None = None
    destination_state: Literal["absent", "existing", "invalid"] = "invalid"
    base_fingerprint: str | None = None
    candidate_id: str | None = None
    candidate_fingerprint: str | None = None
    approval_id: str | None = None
    approval_prompt: str | None = None
    summary: str
    artifact_count: int = 0
    diff: str = ""
    checks: tuple[CandidateCheck, ...] = ()
    blockers: tuple[ApplicationApplyIssue, ...] = ()


class ApplicationApplyApproval(ApplicationApplyModel):
    """Exact values a human-approved adapter must return to the apply service."""

    approval_id: str
    proposal_id: str
    application_id: str
    target_path: str
    base_fingerprint: str
    candidate_id: str
    candidate_fingerprint: str

    @classmethod
    def from_preparation(
        cls,
        preparation: ApplicationApplyPreparation,
    ) -> ApplicationApplyApproval:
        if not preparation.ready:
            raise ValueError("an unready application candidate cannot be approved")
        values = (
            preparation.approval_id,
            preparation.application_id,
            preparation.target_path,
            preparation.base_fingerprint,
            preparation.candidate_id,
            preparation.candidate_fingerprint,
        )
        if any(value is None for value in values):
            raise ValueError("application approval preparation is incomplete")
        return cls(
            approval_id=str(preparation.approval_id),
            proposal_id=preparation.proposal_id,
            application_id=str(preparation.application_id),
            target_path=str(preparation.target_path),
            base_fingerprint=str(preparation.base_fingerprint),
            candidate_id=str(preparation.candidate_id),
            candidate_fingerprint=str(preparation.candidate_fingerprint),
        )


class ApplicationApplyResult(ApplicationApplyModel):
    """A durable record of one successfully published new application."""

    applied: Literal[True] = True
    workspace_writes_performed: Literal[True] = True
    proposal_id: str
    approval_id: str
    application_id: str
    target_path: str
    base_fingerprint: str
    candidate_id: str
    candidate_fingerprint: str
    artifact_count: int
    receipt_path: str
    applied_at: str


class ApplicationApplyError(RuntimeError):
    """A generated application was not safe to publish."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _Destination:
    workspace: Path | None
    applications: Path | None
    target: Path | None
    logical_target: str
    state: Literal["absent", "existing", "invalid"]
    base_fingerprint: str | None
    blockers: tuple[ApplicationApplyIssue, ...]


class ApplicationApplyService:
    """Prepare and publish a verified application under ``applications/`` only."""

    def __init__(
        self,
        workspace: str | Path,
        materialization: ApplicationMaterializationService | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.materialization = materialization or ApplicationMaterializationService()

    def prepare(
        self,
        plan: ApplicationGenerationPlan,
    ) -> ApplicationApplyPreparation:
        preparation, _, _ = self._inspect(plan)
        return preparation

    def apply(
        self,
        plan: ApplicationGenerationPlan,
        approval: ApplicationApplyApproval,
    ) -> ApplicationApplyResult:
        preparation, preview, destination = self._inspect(plan)
        if not preparation.ready:
            blocker = preparation.blockers[0]
            raise ApplicationApplyError(blocker.code, blocker.message)
        self._validate_approval(preparation, approval)
        if (
            destination.applications is None
            or destination.target is None
            or destination.workspace is None
        ):
            raise ApplicationApplyError(
                "TIDEAPPLY002",
                "the applications destination is not available",
            )

        applications = destination.applications
        target = destination.target
        applications_created = False
        lock_path: Path | None = None
        lock_acquired = False
        stage: Path | None = None
        published = False
        try:
            if not applications.exists():
                applications.mkdir()
                applications_created = True
            self._assert_safe_applications_root(destination.workspace, applications)
            lock_path = applications / f".{approval.application_id}.tide-apply.lock"
            self._acquire_lock(lock_path, approval)
            lock_acquired = True
            current = self._inspect_destination(
                approval.application_id,
                approval.target_path,
            )
            if current.state != "absent" or current.base_fingerprint != (
                approval.base_fingerprint
            ):
                raise ApplicationApplyError(
                    "TIDEAPPLY003",
                    "the application destination changed after approval preparation",
                )

            stage = Path(
                mkdtemp(
                    prefix=f".{approval.application_id}.tide-candidate-",
                    dir=applications,
                )
            )
            _write_candidate(stage, preview.artifacts)
            self._verify_staged_candidate(stage, preview)
            try:
                compile_project(stage)
            except CompilationFailed as error:
                raise ApplicationApplyError(
                    "TIDEAPPLY005",
                    "the staged candidate no longer compiles",
                ) from error

            receipt_name = ".tide-apply.json"
            applied_at = datetime.now(timezone.utc).isoformat()
            receipt = {
                "schema_version": "1",
                "workspace_writes_performed": True,
                "approval_id": approval.approval_id,
                "proposal_id": approval.proposal_id,
                "application_id": approval.application_id,
                "target_path": approval.target_path,
                "base_fingerprint": approval.base_fingerprint,
                "candidate_id": approval.candidate_id,
                "candidate_fingerprint": approval.candidate_fingerprint,
                "diff_sha256": _content_hash(preparation.diff),
                "artifacts": [
                    {"path": artifact.path, "sha256": artifact.sha256}
                    for artifact in preview.artifacts
                ],
                "applied_at": applied_at,
            }
            (stage / receipt_name).write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )

            final_check = self._inspect_destination(
                approval.application_id,
                approval.target_path,
            )
            if final_check.state != "absent" or final_check.base_fingerprint != (
                approval.base_fingerprint
            ):
                raise ApplicationApplyError(
                    "TIDEAPPLY003",
                    "the application destination changed immediately before publish",
                )
            _publish_candidate(stage, target)
            published = True
            return ApplicationApplyResult(
                proposal_id=approval.proposal_id,
                approval_id=approval.approval_id,
                application_id=approval.application_id,
                target_path=approval.target_path,
                base_fingerprint=approval.base_fingerprint,
                candidate_id=approval.candidate_id,
                candidate_fingerprint=approval.candidate_fingerprint,
                artifact_count=len(preview.artifacts),
                receipt_path=f"{approval.target_path}/{receipt_name}",
                applied_at=applied_at,
            )
        except ApplicationApplyError:
            raise
        except FileExistsError as error:
            raise ApplicationApplyError(
                "TIDEAPPLY003",
                "the application destination or apply lock already exists",
            ) from error
        except (OSError, ValueError) as error:
            raise ApplicationApplyError(
                "TIDEAPPLY007",
                f"application publication failed: {type(error).__name__}",
            ) from error
        finally:
            if stage is not None and stage.exists() and not published:
                shutil.rmtree(stage, ignore_errors=True)
            if lock_path is not None and lock_acquired:
                lock_path.unlink(missing_ok=True)
            if applications_created and applications.exists() and not published:
                try:
                    applications.rmdir()
                except OSError:
                    pass

    def _inspect(
        self,
        plan: ApplicationGenerationPlan,
    ) -> tuple[
        ApplicationApplyPreparation,
        ApplicationGenerationPreview,
        _Destination,
    ]:
        preview = self.materialization.preview(plan)
        application_id = preview.application_id
        logical_target = preview.target_path or "applications/invalid"
        blockers: list[ApplicationApplyIssue] = []
        if not preview.valid or application_id is None:
            blockers.append(
                ApplicationApplyIssue(
                    code="TIDEAPPLY001",
                    message="the generated application candidate did not pass preview",
                )
            )
        expected_target = (
            f"applications/{application_id}" if application_id is not None else None
        )
        if expected_target is not None and preview.target_path != expected_target:
            blockers.append(
                ApplicationApplyIssue(
                    code="TIDEAPPLY002",
                    message="the candidate target is not the canonical applications path",
                )
            )
        if expected_target is not None and preview.base_fingerprint != (
            _fingerprint_empty_base(expected_target)
        ):
            blockers.append(
                ApplicationApplyIssue(
                    code="TIDEAPPLY004",
                    message="the preview empty-base fingerprint is inconsistent",
                )
            )
        integrity = self._preview_integrity_issue(preview)
        if integrity is not None:
            blockers.append(integrity)

        destination = self._inspect_destination(
            application_id or "invalid",
            expected_target or logical_target,
        )
        blockers.extend(destination.blockers)
        if destination.state == "existing":
            blockers.append(
                ApplicationApplyIssue(
                    code="TIDEAPPLY003",
                    message=(
                        f"destination {destination.logical_target!r} already exists; "
                        "new-application apply never overwrites"
                    ),
                )
            )
        ready = not blockers
        approval_id = None
        if (
            ready
            and preview.candidate_id is not None
            and preview.candidate_fingerprint is not None
            and destination.base_fingerprint is not None
            and preview.target_path is not None
        ):
            approval_id = _approval_id(
                preview.proposal_id,
                preview.candidate_id,
                preview.candidate_fingerprint,
                destination.base_fingerprint,
                preview.target_path,
            )
        preparation = ApplicationApplyPreparation(
            ready=ready,
            proposal_id=preview.proposal_id,
            application_id=application_id,
            target_path=preview.target_path,
            destination_state=destination.state,
            base_fingerprint=destination.base_fingerprint,
            candidate_id=preview.candidate_id,
            candidate_fingerprint=preview.candidate_fingerprint,
            approval_id=approval_id,
            approval_prompt=f"APPLY {approval_id}" if approval_id is not None else None,
            summary=(
                "candidate is ready for explicit approval"
                if ready
                else f"candidate apply is blocked by {len(blockers)} issue(s)"
            ),
            artifact_count=len(preview.artifacts),
            diff=preview.diff,
            checks=preview.checks,
            blockers=tuple(blockers),
        )
        return preparation, preview, destination

    def _inspect_destination(
        self,
        application_id: str,
        logical_target: str,
    ) -> _Destination:
        blockers: list[ApplicationApplyIssue] = []
        try:
            workspace = self.workspace.resolve(strict=True)
        except OSError:
            blockers.append(
                ApplicationApplyIssue(
                    code="TIDEAPPLY002",
                    message="the workspace root does not exist or cannot be resolved",
                )
            )
            return _Destination(
                None,
                None,
                None,
                logical_target,
                "invalid",
                None,
                tuple(blockers),
            )
        if not workspace.is_dir():
            blockers.append(
                ApplicationApplyIssue(
                    code="TIDEAPPLY002",
                    message="the workspace root is not a directory",
                )
            )
        applications = workspace / "applications"
        if os.path.lexists(applications):
            if applications.is_symlink():
                blockers.append(
                    ApplicationApplyIssue(
                        code="TIDEAPPLY002",
                        message="the applications root must not be a symbolic link",
                    )
                )
            elif not applications.is_dir():
                blockers.append(
                    ApplicationApplyIssue(
                        code="TIDEAPPLY002",
                        message="the applications path is not a directory",
                    )
                )
            else:
                try:
                    self._assert_safe_applications_root(workspace, applications)
                except ApplicationApplyError as error:
                    blockers.append(
                        ApplicationApplyIssue(code=error.code, message=error.message)
                    )
        target = applications / application_id
        if target.parent != applications:
            blockers.append(
                ApplicationApplyIssue(
                    code="TIDEAPPLY002",
                    message="the application target escaped the applications root",
                )
            )
        target_exists = os.path.lexists(target)
        if applications.is_dir() and not target_exists:
            try:
                target_exists = any(
                    child.name.casefold() == application_id.casefold()
                    for child in applications.iterdir()
                )
            except OSError:
                blockers.append(
                    ApplicationApplyIssue(
                        code="TIDEAPPLY002",
                        message="the applications directory cannot be inspected",
                    )
                )
        if blockers:
            return _Destination(
                workspace,
                applications,
                target,
                logical_target,
                "invalid",
                None,
                tuple(blockers),
            )
        state: Literal["absent", "existing", "invalid"] = (
            "existing" if target_exists else "absent"
        )
        base_fingerprint = (
            _destination_fingerprint(target, logical_target, "existing")
            if target_exists
            else _destination_fingerprint(target, logical_target, "absent")
        )
        return _Destination(
            workspace,
            applications,
            target,
            logical_target,
            state,
            base_fingerprint,
            (),
        )

    @staticmethod
    def _assert_safe_applications_root(workspace: Path, applications: Path) -> None:
        if applications.is_symlink() or not applications.is_dir():
            raise ApplicationApplyError(
                "TIDEAPPLY002",
                "the applications root is not a safe directory",
            )
        try:
            resolved = applications.resolve(strict=True)
        except OSError as error:
            raise ApplicationApplyError(
                "TIDEAPPLY002",
                "the applications root cannot be resolved",
            ) from error
        if not resolved.is_relative_to(workspace):
            raise ApplicationApplyError(
                "TIDEAPPLY002",
                "the applications root escaped the workspace",
            )

    @staticmethod
    def _preview_integrity_issue(
        preview: ApplicationGenerationPreview,
    ) -> ApplicationApplyIssue | None:
        if not preview.valid:
            return None
        if (
            preview.candidate_id is None
            or preview.candidate_fingerprint is None
            or not preview.artifacts
        ):
            return ApplicationApplyIssue(
                code="TIDEAPPLY004",
                message="the valid candidate preview is missing fingerprinted artifacts",
            )
        seen: set[str] = set()
        try:
            for artifact in preview.artifacts:
                normalized = _validated_relative_path(artifact.path).as_posix()
                folded = normalized.casefold()
                if folded in seen:
                    raise ValueError("candidate paths collide case-insensitively")
                seen.add(folded)
                if artifact.sha256 != _content_hash(artifact.content):
                    raise ValueError(f"artifact hash differs for {artifact.path!r}")
                if artifact.size_bytes != len(artifact.content.encode("utf-8")):
                    raise ValueError(f"artifact size differs for {artifact.path!r}")
        except ValueError as error:
            return ApplicationApplyIssue(code="TIDEAPPLY004", message=str(error))
        fingerprint = _fingerprint_artifacts(preview.artifacts)
        expected_id = "tide-candidate-" + fingerprint.removeprefix("sha256:")[:24]
        if (
            fingerprint != preview.candidate_fingerprint
            or expected_id != preview.candidate_id
        ):
            return ApplicationApplyIssue(
                code="TIDEAPPLY004",
                message="the candidate artifact set does not match its fingerprint",
            )
        return None

    @staticmethod
    def _validate_approval(
        preparation: ApplicationApplyPreparation,
        approval: ApplicationApplyApproval,
    ) -> None:
        expected = ApplicationApplyApproval.from_preparation(preparation)
        if approval != expected:
            raise ApplicationApplyError(
                "TIDEAPPLY004",
                "approval values do not match the current candidate and destination",
            )

    @staticmethod
    def _acquire_lock(lock_path: Path, approval: ApplicationApplyApproval) -> None:
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as error:
            raise ApplicationApplyError(
                "TIDEAPPLY006",
                "another apply operation already owns this application target",
            ) from error
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as lock:
            lock.write(approval.approval_id + "\n")

    @staticmethod
    def _verify_staged_candidate(
        stage: Path,
        preview: ApplicationGenerationPreview,
    ) -> None:
        expected_paths = {artifact.path for artifact in preview.artifacts}
        actual_paths = {
            path.relative_to(stage).as_posix()
            for path in stage.rglob("*")
            if path.is_file()
        }
        if actual_paths != expected_paths:
            raise ApplicationApplyError(
                "TIDEAPPLY005",
                "the staged candidate file inventory differs from approval",
            )
        for artifact in preview.artifacts:
            relative = _validated_relative_path(artifact.path)
            content = stage.joinpath(*relative.parts).read_bytes()
            if content != artifact.content.encode("utf-8"):
                raise ApplicationApplyError(
                    "TIDEAPPLY005",
                    f"staged artifact {artifact.path!r} differs from approval",
                )


def _approval_id(
    proposal_id: str,
    candidate_id: str,
    candidate_fingerprint: str,
    base_fingerprint: str,
    target_path: str,
) -> str:
    digest = sha256()
    digest.update(b"tide-application-approval-v1\0")
    for value in (
        proposal_id,
        candidate_id,
        candidate_fingerprint,
        base_fingerprint,
        target_path,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return "tide-approval-" + digest.hexdigest()[:24]


def _destination_fingerprint(
    target: Path,
    logical_target: str,
    state: Literal["absent", "existing"],
) -> str:
    canonical = os.path.normcase(str(target.absolute()))
    return _content_hash(
        f"tide-application-destination-v1\0{state}\0{logical_target}\0{canonical}"
    )


def _publish_candidate(stage: Path, target: Path) -> None:
    """Publish within one filesystem; the exclusive TIDE lock prevents replay."""

    os.rename(stage, target)
