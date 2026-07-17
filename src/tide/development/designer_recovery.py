"""Fail-closed inspection and recovery of interrupted Designer saves."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tide.compiler.compiler import compile_project
from tide.development.designer import (
    DesignerError,
    _fingerprint_files,
    _read_project_sources,
)
from tide.development.designer_transaction import (
    _DesignerTransactionJournal,
    _DesignerTransactionLock,
    _DesignerTransactionReceipt,
    _TransactionArtifact,
    _TransactionLockBusy,
    _TransactionLockHandle,
    _TransactionRecordError,
    _canonical_project_path,
    _content_sha256,
    _model_bytes,
    _read_transaction_receipt,
    _read_transaction_record,
    _validated_transaction_path,
    _write_transaction_record,
)
from tide.diagnostics import CompilationFailed

RecoveryAction = Literal["rollback", "finalize"]
ArtifactState = Literal["base", "candidate", "missing", "other", "unsafe"]


class DesignerRecoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DesignerRecoveryIssue(DesignerRecoveryModel):
    code: str
    message: str


class DesignerRecoveryArtifact(DesignerRecoveryModel):
    path: str
    target_state: ArtifactState
    target_sha256: str | None = None
    backup_state: ArtifactState
    backup_sha256: str | None = None
    candidate_state: ArtifactState
    candidate_sha256: str | None = None


class DesignerRecoveryPreparation(DesignerRecoveryModel):
    ready: bool
    recovery_required: bool
    approval_required: bool = True
    writes_performed: Literal[False] = False
    project: str
    project_path: str
    project_file: str
    transaction_id: str | None = None
    approval_id: str | None = None
    recovery_id: str | None = None
    recovery_action: RecoveryAction | None = None
    approval_prompt: str | None = None
    stage_name: str | None = None
    journal_phase: str | None = None
    receipt_present: bool = False
    summary: str
    artifacts: tuple[DesignerRecoveryArtifact, ...] = ()
    blockers: tuple[DesignerRecoveryIssue, ...] = ()


class DesignerRecoveryApproval(DesignerRecoveryModel):
    recovery_id: str
    transaction_id: str
    project_path: str
    recovery_action: RecoveryAction

    @classmethod
    def from_preparation(
        cls,
        preparation: DesignerRecoveryPreparation,
    ) -> DesignerRecoveryApproval:
        if not preparation.ready:
            raise ValueError("an unready Designer recovery cannot be approved")
        if (
            preparation.recovery_id is None
            or preparation.transaction_id is None
            or preparation.recovery_action is None
        ):
            raise ValueError("Designer recovery preparation is incomplete")
        return cls(
            recovery_id=preparation.recovery_id,
            transaction_id=preparation.transaction_id,
            project_path=preparation.project_path,
            recovery_action=preparation.recovery_action,
        )


class DesignerRecoveryResult(DesignerRecoveryModel):
    recovered: Literal[True] = True
    workspace_writes_performed: Literal[True] = True
    transaction_id: str
    recovery_id: str
    project_path: str
    recovery_action: RecoveryAction
    restored_files: int
    stage_removed: bool
    lock_removed: bool
    recovered_at: str


class DesignerRecoveryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class _RecoveryInspection:
    preparation: DesignerRecoveryPreparation
    record: _DesignerTransactionLock | None = None
    journal: _DesignerTransactionJournal | None = None
    receipt: _DesignerTransactionReceipt | None = None
    stage: Path | None = None
    cleanup_stage: Path | None = None


class DesignerRecoveryService:
    """Inspect and explicitly recover one interrupted Designer transaction."""

    lock_name = ".tide-designer-save.lock"

    def __init__(self, project: str | Path) -> None:
        supplied = Path(project)
        try:
            if supplied.name.casefold() == "tide.yaml" and not supplied.is_dir():
                self.root = supplied.parent.resolve(strict=True)
                project_file = self.root / "tide.yaml"
            else:
                self.root = supplied.resolve(strict=True)
                project_file = self.root / "tide.yaml"
        except OSError as error:
            raise DesignerRecoveryError(
                "TIDEREC003",
                "the Designer recovery project root does not exist",
            ) from error
        if not self.root.is_dir():
            raise DesignerRecoveryError(
                "TIDEREC003",
                "the Designer recovery project root is not a directory",
            )
        self.project_file = project_file.relative_to(self.root).as_posix()
        self.project_path = _canonical_project_path(self.root)
        self.lock_path = self.root / self.lock_name

    def prepare(self) -> DesignerRecoveryPreparation:
        if not os.path.lexists(self.lock_path):
            return self._empty_preparation()
        try:
            with _TransactionLockHandle.open(self.lock_path) as handle:
                return self._inspect_locked(handle).preparation
        except _TransactionLockBusy:
            return self._blocked_preparation(
                "TIDEREC002",
                "the Designer save still owns its operating-system lock",
            )
        except _TransactionRecordError as error:
            return self._blocked_preparation("TIDEREC003", str(error))

    def recover(
        self,
        approval: DesignerRecoveryApproval,
    ) -> DesignerRecoveryResult:
        if not os.path.lexists(self.lock_path):
            raise DesignerRecoveryError(
                "TIDEREC001",
                "there is no interrupted Designer transaction to recover",
            )
        try:
            handle = _TransactionLockHandle.open(self.lock_path)
        except _TransactionLockBusy as error:
            raise DesignerRecoveryError(
                "TIDEREC002",
                "the Designer save is still active",
            ) from error
        except _TransactionRecordError as error:
            raise DesignerRecoveryError("TIDEREC003", str(error)) from error

        inspection: _RecoveryInspection
        try:
            inspection = self._inspect_locked(handle)
            preparation = inspection.preparation
            if not preparation.ready:
                blocker = preparation.blockers[0]
                raise DesignerRecoveryError(blocker.code, blocker.message)
            expected = DesignerRecoveryApproval.from_preparation(preparation)
            if approval != expected:
                raise DesignerRecoveryError(
                    "TIDEREC005",
                    "approval does not match the current recovery evidence",
                )
            record = inspection.record
            if record is None:
                raise DesignerRecoveryError(
                    "TIDEREC003",
                    "the Designer recovery record is unavailable",
                )

            restored = 0
            if preparation.recovery_action == "rollback":
                restored = self._restore_base(inspection)
                self._verify_tree(record.base_fingerprint)
            else:
                self._verify_tree(record.candidate_fingerprint)

            _recovery_checkpoint("before_cleanup", inspection.stage)
            cleanup_stage = inspection.cleanup_stage
            if inspection.stage is not None and inspection.stage.exists():
                cleanup_stage = inspection.stage.with_name(
                    inspection.stage.name + ".cleanup"
                )
                if os.path.lexists(cleanup_stage):
                    raise DesignerRecoveryError(
                        "TIDEREC004",
                        "the Designer cleanup directory already exists",
                    )
                os.replace(inspection.stage, cleanup_stage)
            _recovery_checkpoint("after_stage_quarantine", cleanup_stage)
            if cleanup_stage is not None and cleanup_stage.exists():
                shutil.rmtree(cleanup_stage)
            _recovery_checkpoint("after_stage_cleanup", inspection.stage)
        except DesignerRecoveryError:
            raise
        except (OSError, ValueError, CompilationFailed, DesignerError) as error:
            raise DesignerRecoveryError(
                "TIDEREC006",
                f"Designer recovery failed: {type(error).__name__}",
            ) from error
        finally:
            handle.close()

        try:
            self.lock_path.unlink()
        except OSError as error:
            raise DesignerRecoveryError(
                "TIDEREC006",
                "Designer recovery restored the source but could not remove its lock",
            ) from error
        return DesignerRecoveryResult(
            transaction_id=approval.transaction_id,
            recovery_id=approval.recovery_id,
            project_path=approval.project_path,
            recovery_action=approval.recovery_action,
            restored_files=restored,
            stage_removed=True,
            lock_removed=True,
            recovered_at=datetime.now(timezone.utc).isoformat(),
        )

    def _inspect_locked(
        self,
        handle: _TransactionLockHandle,
    ) -> _RecoveryInspection:
        try:
            record = handle.read()
        except _TransactionRecordError as error:
            return _RecoveryInspection(
                preparation=self._blocked_preparation("TIDEREC003", str(error))
            )
        blockers: list[DesignerRecoveryIssue] = []
        self._validate_record(record, blockers)
        if blockers:
            return _RecoveryInspection(
                preparation=DesignerRecoveryPreparation(
                    ready=False,
                    recovery_required=True,
                    project=self.root.name,
                    project_path=self.project_path,
                    project_file=self.project_file,
                    transaction_id=record.transaction_id,
                    approval_id=record.approval_id,
                    summary=(
                        f"Designer recovery is blocked by {len(blockers)} issue(s)"
                    ),
                    blockers=tuple(blockers),
                ),
                record=record,
            )
        stage = self.root.parent / record.stage_name
        cleanup_stage = stage.with_name(stage.name + ".cleanup")
        journal: _DesignerTransactionJournal | None = None
        safe_cleanup_stage: Path | None = None
        if os.path.lexists(stage) and os.path.lexists(cleanup_stage):
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="both Designer stage and cleanup directories exist",
                )
            )
        if os.path.lexists(cleanup_stage):
            if cleanup_stage.is_symlink() or not cleanup_stage.is_dir():
                blockers.append(
                    DesignerRecoveryIssue(
                        code="TIDEREC003",
                        message="the Designer cleanup stage is not a safe directory",
                    )
                )
            else:
                safe_cleanup_stage = cleanup_stage
        if os.path.lexists(stage):
            if stage.is_symlink() or not stage.is_dir():
                blockers.append(
                    DesignerRecoveryIssue(
                        code="TIDEREC003",
                        message="the Designer recovery stage is not a safe directory",
                    )
                )
            else:
                journal_path = stage / "transaction.json"
                try:
                    journal = _read_transaction_record(journal_path)
                except _TransactionRecordError as error:
                    blockers.append(
                        DesignerRecoveryIssue(code="TIDEREC003", message=str(error))
                    )
                else:
                    if not _journal_matches_lock(journal, record):
                        blockers.append(
                            DesignerRecoveryIssue(
                                code="TIDEREC003",
                                message=(
                                    "the Designer journal does not match its lock"
                                ),
                            )
                        )

        receipt = self._inspect_receipt(record, blockers)
        artifacts = tuple(
            self._inspect_artifact(record, artifact, stage)
            for artifact in record.artifacts
        )
        self._validate_unchanged_sources(record, stage, artifacts, blockers)
        action = self._select_action(record, receipt, artifacts, blockers)
        ready = not blockers and action is not None
        recovery_id = (
            _recovery_id(record, journal, receipt, action, artifacts)
            if ready and action is not None
            else None
        )
        action_label = "rolled back" if action == "rollback" else "finalized"
        preparation = DesignerRecoveryPreparation(
            ready=ready,
            recovery_required=True,
            project=self.root.name,
            project_path=self.project_path,
            project_file=self.project_file,
            transaction_id=record.transaction_id,
            approval_id=record.approval_id,
            recovery_id=recovery_id,
            recovery_action=action,
            approval_prompt=f"RECOVER {recovery_id}" if recovery_id else None,
            stage_name=record.stage_name,
            journal_phase=journal.phase if journal is not None else None,
            receipt_present=receipt is not None,
            summary=(
                f"Interrupted Designer save can be safely {action_label}"
                if ready
                else f"Designer recovery is blocked by {len(blockers)} issue(s)"
            ),
            artifacts=artifacts,
            blockers=tuple(blockers),
        )
        return _RecoveryInspection(
            preparation=preparation,
            record=record,
            journal=journal,
            receipt=receipt,
            stage=stage if stage.is_dir() and not stage.is_symlink() else None,
            cleanup_stage=safe_cleanup_stage,
        )

    def _validate_record(
        self,
        record: _DesignerTransactionLock,
        blockers: list[DesignerRecoveryIssue],
    ) -> None:
        if (
            re.fullmatch(r"tide-designer-save-[0-9a-f]{32}", record.transaction_id)
            is None
        ):
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="the Designer lock contains an invalid transaction ID",
                )
            )
        if (
            re.fullmatch(r"tide-designer-approval-[0-9a-f]{24}", record.approval_id)
            is None
        ):
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="the Designer lock contains an invalid approval ID",
                )
            )
        if (
            re.fullmatch(r"tide-designer-candidate-[0-9a-f]{24}", record.candidate_id)
            is None
        ):
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="the Designer lock contains an invalid candidate ID",
                )
            )
        fingerprints = (
            record.base_fingerprint,
            record.candidate_fingerprint,
            record.change_fingerprint,
            record.diff_sha256,
        )
        if any(
            re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
            for value in fingerprints
        ):
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="the Designer lock contains an invalid fingerprint",
                )
            )
        if record.project_path != self.project_path:
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="the Designer lock belongs to another project path",
                )
            )
        if record.project_file != self.project_file:
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="the Designer lock names another project file",
                )
            )
        expected_stage = f".{self.root.name}.{record.transaction_id}"
        if (
            record.stage_name != expected_stage
            or Path(record.stage_name).name != record.stage_name
        ):
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="the Designer lock contains an unsafe recovery stage",
                )
            )
        expected_receipt = f".tide/designer/{record.approval_id}.json"
        if record.receipt_path != expected_receipt:
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="the Designer lock contains an unsafe receipt path",
                )
            )
        paths: set[str] = set()
        for artifact in record.artifacts:
            if (
                re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.base_sha256) is None
                or re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.candidate_sha256)
                is None
                or artifact.size_bytes < 0
            ):
                blockers.append(
                    DesignerRecoveryIssue(
                        code="TIDEREC003",
                        message="the Designer lock contains invalid artifact metadata",
                    )
                )
            try:
                relative = _validated_transaction_path(artifact.path)
            except ValueError as error:
                blockers.append(
                    DesignerRecoveryIssue(code="TIDEREC003", message=str(error))
                )
                continue
            portable = relative.as_posix().casefold()
            if portable in paths:
                blockers.append(
                    DesignerRecoveryIssue(
                        code="TIDEREC003",
                        message="the Designer lock repeats a source path",
                    )
                )
            paths.add(portable)
        if not record.artifacts:
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="the Designer lock contains no changed artifacts",
                )
            )

    def _inspect_receipt(
        self,
        record: _DesignerTransactionLock,
        blockers: list[DesignerRecoveryIssue],
    ) -> _DesignerTransactionReceipt | None:
        receipt_path = self.root / PurePosixPath(record.receipt_path)
        if not os.path.lexists(receipt_path):
            return None
        try:
            receipt = _read_transaction_receipt(receipt_path)
        except _TransactionRecordError as error:
            blockers.append(
                DesignerRecoveryIssue(code="TIDEREC003", message=str(error))
            )
            return None
        if not _receipt_matches_lock(receipt, record):
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC003",
                    message="the Designer receipt does not match its transaction lock",
                )
            )
            return None
        return receipt

    def _inspect_artifact(
        self,
        record: _DesignerTransactionLock,
        artifact: _TransactionArtifact,
        stage: Path,
    ) -> DesignerRecoveryArtifact:
        relative = _validated_transaction_path(artifact.path)
        target = self.root.joinpath(*relative.parts)
        backup = stage.joinpath("backup", *relative.parts)
        candidate = stage.joinpath("candidate", *relative.parts)
        target_state, target_hash = _artifact_state(target, artifact)
        backup_state, backup_hash = _artifact_state(backup, artifact)
        candidate_state, candidate_hash = _artifact_state(candidate, artifact)
        return DesignerRecoveryArtifact(
            path=artifact.path,
            target_state=target_state,
            target_sha256=target_hash,
            backup_state=backup_state,
            backup_sha256=backup_hash,
            candidate_state=candidate_state,
            candidate_sha256=candidate_hash,
        )

    def _validate_unchanged_sources(
        self,
        record: _DesignerTransactionLock,
        stage: Path,
        artifacts: tuple[DesignerRecoveryArtifact, ...],
        blockers: list[DesignerRecoveryIssue],
    ) -> None:
        changed = {artifact.path for artifact in record.artifacts}
        if stage.is_dir() and not stage.is_symlink():
            try:
                staged = _read_loose_sources(stage / "candidate")
                live = _read_loose_sources(self.root)
            except (OSError, UnicodeDecodeError, ValueError) as error:
                blockers.append(
                    DesignerRecoveryIssue(
                        code="TIDEREC004",
                        message=f"Designer recovery source cannot be read: {error}",
                    )
                )
                return
            reconstructed = dict(staged)
            for artifact in record.artifacts:
                if artifact.path in reconstructed:
                    continue
                relative = _validated_transaction_path(artifact.path)
                possible = (
                    self.root.joinpath(*relative.parts),
                    stage.joinpath("recovery-discard", *relative.parts),
                    stage.joinpath("discard", *relative.parts),
                )
                for source in possible:
                    if source.is_symlink() or not source.is_file():
                        continue
                    content = source.read_bytes()
                    if _content_sha256(content) == artifact.candidate_sha256:
                        reconstructed[artifact.path] = content
                        break
            if _fingerprint_files(reconstructed) != record.candidate_fingerprint:
                blockers.append(
                    DesignerRecoveryIssue(
                        code="TIDEREC004",
                        message="the staged Designer candidate fingerprint changed",
                    )
                )
            candidate_unchanged = {
                path: content
                for path, content in reconstructed.items()
                if path not in changed
            }
            live_unchanged = {
                path: content for path, content in live.items() if path not in changed
            }
            if candidate_unchanged != live_unchanged:
                blockers.append(
                    DesignerRecoveryIssue(
                        code="TIDEREC004",
                        message="unchanged application sources drifted during recovery",
                    )
                )
        else:
            expected_state = _complete_tree_state(artifacts)
            if expected_state is None:
                return
            try:
                live = _read_project_sources(
                    self.root,
                    self.root / PurePosixPath(record.project_file),
                )
            except DesignerError as error:
                blockers.append(
                    DesignerRecoveryIssue(
                        code="TIDEREC004",
                        message=f"Designer recovery source cannot be read: {error}",
                    )
                )
            else:
                expected_fingerprint = (
                    record.base_fingerprint
                    if expected_state == "base"
                    else record.candidate_fingerprint
                )
                if _fingerprint_files(live) != expected_fingerprint:
                    blockers.append(
                        DesignerRecoveryIssue(
                            code="TIDEREC004",
                            message=(
                                "the live application no longer matches its "
                                f"{expected_state} fingerprint"
                            ),
                        )
                    )

    @staticmethod
    def _select_action(
        record: _DesignerTransactionLock,
        receipt: _DesignerTransactionReceipt | None,
        artifacts: tuple[DesignerRecoveryArtifact, ...],
        blockers: list[DesignerRecoveryIssue],
    ) -> RecoveryAction | None:
        if blockers:
            return None
        if receipt is not None:
            if all(item.target_state == "candidate" for item in artifacts):
                return "finalize"
            blockers.append(
                DesignerRecoveryIssue(
                    code="TIDEREC004",
                    message=(
                        "a save receipt exists but live YAML does not match its candidate"
                    ),
                )
            )
            return None
        recoverable = all(
            (item.target_state == "base" and item.backup_state == "missing")
            or (
                item.target_state in {"missing", "candidate"}
                and item.backup_state == "base"
            )
            for item in artifacts
        )
        if recoverable:
            return "rollback"
        blockers.append(
            DesignerRecoveryIssue(
                code="TIDEREC004",
                message=(
                    "Designer recovery evidence is ambiguous; source files were not changed"
                ),
            )
        )
        return None

    def _restore_base(self, inspection: _RecoveryInspection) -> int:
        record = inspection.record
        stage = inspection.stage
        if record is None:
            raise DesignerRecoveryError("TIDEREC003", "recovery record is unavailable")
        journal_path = stage / "transaction.json" if stage is not None else None
        if journal_path is not None and inspection.journal is not None:
            _write_transaction_record(
                journal_path,
                inspection.journal.model_copy(
                    update={
                        "phase": "recovering",
                        "active_path": None,
                        "active_step": None,
                    }
                ),
            )
        restored = 0
        evidence = {item.path: item for item in inspection.preparation.artifacts}
        for artifact in reversed(record.artifacts):
            item = evidence[artifact.path]
            relative = _validated_transaction_path(artifact.path)
            target = self.root.joinpath(*relative.parts)
            backup = stage.joinpath("backup", *relative.parts) if stage else None
            current_target = _artifact_state(target, artifact)
            current_backup = (
                _artifact_state(backup, artifact)
                if backup is not None
                else ("missing", None)
            )
            if current_target != (item.target_state, item.target_sha256) or (
                current_backup != (item.backup_state, item.backup_sha256)
            ):
                raise DesignerRecoveryError(
                    "TIDEREC005",
                    f"recovery evidence changed before restore: {artifact.path}",
                )
            if item.target_state == "base" and item.backup_state == "missing":
                continue
            if stage is None:
                raise DesignerRecoveryError(
                    "TIDEREC004",
                    "the recovery backup directory is unavailable",
                )
            assert backup is not None
            discard = stage.joinpath("recovery-discard", *relative.parts)
            _recovery_checkpoint(f"before_restore:{artifact.path}", stage)
            if item.target_state == "candidate":
                discard.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, discard)
            os.replace(backup, target)
            if _content_sha256(target.read_bytes()) != artifact.base_sha256:
                raise DesignerRecoveryError(
                    "TIDEREC006",
                    f"restored Designer source failed verification: {artifact.path}",
                )
            restored += 1
            _recovery_checkpoint(f"after_restore:{artifact.path}", stage)
        return restored

    def _verify_tree(self, expected_fingerprint: str) -> None:
        files = _read_project_sources(
            self.root,
            self.root / PurePosixPath(self.project_file),
        )
        if _fingerprint_files(files) != expected_fingerprint:
            raise DesignerRecoveryError(
                "TIDEREC004",
                "the recovered application tree does not match its expected fingerprint",
            )
        compile_project(self.root / PurePosixPath(self.project_file))

    def _empty_preparation(self) -> DesignerRecoveryPreparation:
        return DesignerRecoveryPreparation(
            ready=False,
            recovery_required=False,
            approval_required=False,
            project=self.root.name,
            project_path=self.project_path,
            project_file=self.project_file,
            summary="No interrupted Designer transaction was found",
        )

    def _blocked_preparation(
        self,
        code: str,
        message: str,
    ) -> DesignerRecoveryPreparation:
        return DesignerRecoveryPreparation(
            ready=False,
            recovery_required=True,
            project=self.root.name,
            project_path=self.project_path,
            project_file=self.project_file,
            summary="Designer recovery is blocked",
            blockers=(DesignerRecoveryIssue(code=code, message=message),),
        )


def _artifact_state(
    path: Path,
    artifact: _TransactionArtifact,
) -> tuple[ArtifactState, str | None]:
    if not os.path.lexists(path):
        return "missing", None
    if path.is_symlink() or not path.is_file():
        return "unsafe", None
    try:
        digest = _content_sha256(path.read_bytes())
    except OSError:
        return "unsafe", None
    if digest == artifact.base_sha256:
        return "base", digest
    if digest == artifact.candidate_sha256:
        return "candidate", digest
    return "other", digest


def _complete_tree_state(
    artifacts: tuple[DesignerRecoveryArtifact, ...],
) -> Literal["base", "candidate"] | None:
    if artifacts and all(item.target_state == "base" for item in artifacts):
        return "base"
    if artifacts and all(item.target_state == "candidate" for item in artifacts):
        return "candidate"
    return None


def _read_loose_sources(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    portable_paths: set[str] = set()
    for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
        if path.suffix.lower() not in {".yaml", ".yml", ".py"}:
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("Designer recovery source is not a regular file")
        relative = path.relative_to(root).as_posix()
        portable = relative.casefold()
        if portable in portable_paths:
            raise ValueError("Designer recovery source paths collide by case")
        portable_paths.add(portable)
        content = path.read_bytes()
        content.decode("utf-8")
        files[relative] = content
    return files


def _journal_matches_lock(
    journal: _DesignerTransactionJournal,
    record: _DesignerTransactionLock,
) -> bool:
    fields = _DesignerTransactionLock.model_fields
    payload = {name: getattr(journal, name) for name in fields}
    return _DesignerTransactionLock.model_validate(payload) == record


def _receipt_matches_lock(
    receipt: _DesignerTransactionReceipt,
    record: _DesignerTransactionLock,
) -> bool:
    return (
        receipt.approval_id == record.approval_id
        and receipt.project_file == record.project_file
        and receipt.base_fingerprint == record.base_fingerprint
        and receipt.candidate_id == record.candidate_id
        and receipt.candidate_fingerprint == record.candidate_fingerprint
        and receipt.change_fingerprint == record.change_fingerprint
        and receipt.diff_sha256 == record.diff_sha256
        and receipt.artifacts == record.artifacts
    )


def _recovery_id(
    record: _DesignerTransactionLock,
    journal: _DesignerTransactionJournal | None,
    receipt: _DesignerTransactionReceipt | None,
    action: RecoveryAction,
    artifacts: tuple[DesignerRecoveryArtifact, ...],
) -> str:
    digest = sha256()
    digest.update(b"tide-designer-recovery-v1\0")
    for content in (
        _model_bytes(record),
        _model_bytes(journal) if journal is not None else b"missing-journal",
        _model_bytes(receipt) if receipt is not None else b"missing-receipt",
        action.encode("ascii"),
        json.dumps(
            [artifact.model_dump(mode="json") for artifact in artifacts],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    ):
        digest.update(content)
        digest.update(b"\0")
    return "tide-designer-recovery-" + digest.hexdigest()[:24]


def _recovery_checkpoint(_name: str, _stage: Path | None) -> None:
    """Test seam for emulating another process loss during recovery."""
