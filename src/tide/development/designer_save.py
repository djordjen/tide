"""Candidate-bound approval and transactional save for Designer sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
from secrets import token_hex
import shutil
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tide.compiler.compiler import compile_project
from tide.development.designer import (
    DesignerError,
    DesignerSession,
    _DesignerSessionState,
    _changed_files,
    _evaluate_project,
    _fingerprint_files,
    _portable_relative_path,
    _read_project_sources,
    _source_diff,
    _write_project_sources,
)
from tide.diagnostics import CompilationFailed
from tide.development.designer_transaction import (
    _DesignerTransactionJournal,
    _DesignerTransactionLock,
    _DesignerTransactionReceipt,
    _TransactionArtifact,
    _TransactionLockBusy,
    _TransactionLockHandle,
    _model_bytes,
    _write_transaction_record,
)


class DesignerSaveModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DesignerSaveIssue(DesignerSaveModel):
    code: str
    message: str


class DesignerSaveArtifact(DesignerSaveModel):
    path: str
    base_sha256: str
    candidate_sha256: str
    size_bytes: int


class DesignerSavePreparation(DesignerSaveModel):
    """A no-write approval challenge bound to a live base and exact diff."""

    ready: bool
    approval_required: Literal[True] = True
    writes_performed: Literal[False] = False
    session_id: str
    project: str
    project_path: str
    project_file: str
    base_state: Literal["current", "stale", "invalid"]
    base_fingerprint: str
    live_base_fingerprint: str | None = None
    candidate_id: str | None = None
    candidate_fingerprint: str
    change_fingerprint: str | None = None
    diff_sha256: str | None = None
    approval_id: str | None = None
    approval_prompt: str | None = None
    receipt_path: str | None = None
    summary: str
    changed_files: tuple[str, ...] = ()
    artifacts: tuple[DesignerSaveArtifact, ...] = ()
    diff: str = ""
    diagnostics: tuple[dict[str, object], ...] = ()
    blockers: tuple[DesignerSaveIssue, ...] = ()


class DesignerSaveApproval(DesignerSaveModel):
    """Exact values a human-approved host returns to the save service."""

    approval_id: str
    project_path: str
    project_file: str
    base_fingerprint: str
    candidate_id: str
    candidate_fingerprint: str
    change_fingerprint: str
    diff_sha256: str

    @classmethod
    def from_preparation(
        cls,
        preparation: DesignerSavePreparation,
    ) -> DesignerSaveApproval:
        if not preparation.ready:
            raise ValueError("an unready Designer candidate cannot be approved")
        required = (
            preparation.approval_id,
            preparation.candidate_id,
            preparation.change_fingerprint,
            preparation.diff_sha256,
        )
        if any(value is None for value in required):
            raise ValueError("Designer save preparation is incomplete")
        return cls(
            approval_id=str(preparation.approval_id),
            project_path=preparation.project_path,
            project_file=preparation.project_file,
            base_fingerprint=preparation.base_fingerprint,
            candidate_id=str(preparation.candidate_id),
            candidate_fingerprint=preparation.candidate_fingerprint,
            change_fingerprint=str(preparation.change_fingerprint),
            diff_sha256=str(preparation.diff_sha256),
        )


class DesignerSaveResult(DesignerSaveModel):
    saved: Literal[True] = True
    workspace_writes_performed: Literal[True] = True
    approval_id: str
    project: str
    project_path: str
    project_file: str
    base_fingerprint: str
    candidate_id: str
    candidate_fingerprint: str
    change_fingerprint: str
    changed_files: tuple[str, ...]
    receipt_path: str
    saved_at: str
    rollback_required: Literal[False] = False


class DesignerSaveError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class _Inspection:
    preparation: DesignerSavePreparation
    state: _DesignerSessionState


@dataclass(frozen=True, slots=True)
class _Replacement:
    path: str
    target: Path
    backup: Path


class DesignerSaveService:
    """Prepare and transactionally save one exact existing-app candidate."""

    lock_name = ".tide-designer-save.lock"

    def prepare(self, session: DesignerSession) -> DesignerSavePreparation:
        return self._inspect(session).preparation

    def save(
        self,
        session: DesignerSession,
        approval: DesignerSaveApproval,
    ) -> DesignerSaveResult:
        inspection = self._inspect(session)
        preparation = inspection.preparation
        if not preparation.ready:
            blocker = preparation.blockers[0]
            raise DesignerSaveError(blocker.code, blocker.message)
        self._validate_approval(preparation, approval)
        state = inspection.state
        root = state.root
        lock_path = root / self.lock_name
        transaction_id = "tide-designer-save-" + token_hex(16)
        stage_name = f".{root.name}.{transaction_id}"
        stage: Path | None = None
        lock_handle: _TransactionLockHandle | None = None
        journal: _DesignerTransactionJournal | None = None
        journal_path: Path | None = None
        replacements: list[_Replacement] = []
        preserve_recovery = False
        receipt_path = root / str(preparation.receipt_path)
        created_directories: list[Path] = []
        transaction_artifacts = tuple(
            _TransactionArtifact.model_validate(artifact.model_dump())
            for artifact in preparation.artifacts
        )
        lock_record = _DesignerTransactionLock(
            transaction_id=transaction_id,
            approval_id=approval.approval_id,
            project_path=approval.project_path,
            project_file=approval.project_file,
            base_fingerprint=approval.base_fingerprint,
            candidate_id=approval.candidate_id,
            candidate_fingerprint=approval.candidate_fingerprint,
            change_fingerprint=approval.change_fingerprint,
            diff_sha256=approval.diff_sha256,
            receipt_path=str(preparation.receipt_path),
            stage_name=stage_name,
            artifacts=transaction_artifacts,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            try:
                lock_handle = _TransactionLockHandle.create(lock_path, lock_record)
            except _TransactionLockBusy as error:
                raise DesignerSaveError(
                    "TIDEDSAVE006",
                    "another Designer save owns the application lock",
                ) from error
            self._assert_live_base(state, approval.base_fingerprint)
            stage = root.parent / stage_name
            stage.mkdir()
            candidate_root = stage / "candidate"
            candidate_root.mkdir()
            _write_project_sources(candidate_root, state.working_files)
            self._flush_staged_sources(candidate_root, state.working_files)
            self._verify_staged_candidate(candidate_root, state, approval)
            self._assert_live_base(state, approval.base_fingerprint)

            receipt_directory = receipt_path.parent
            created_directories = self._prepare_receipt_directory(
                root,
                receipt_directory,
            )
            if os.path.lexists(receipt_path):
                raise DesignerSaveError(
                    "TIDEDSAVE009",
                    "the Designer save receipt already exists",
                )

            journal = _DesignerTransactionJournal.model_validate(
                {
                    **lock_record.model_dump(mode="json"),
                    "phase": "prepared",
                }
            )
            journal_path = stage / "transaction.json"
            _write_transaction_record(journal_path, journal)
            _save_checkpoint("prepared", stage)

            backup_root = stage / "backup"
            for artifact in preparation.artifacts:
                relative = _validated_source_path(artifact.path)
                target = root.joinpath(*relative.parts)
                candidate = candidate_root.joinpath(*relative.parts)
                backup = backup_root.joinpath(*relative.parts)
                backup.parent.mkdir(parents=True, exist_ok=True)
                self._assert_artifact_base(target, artifact)
                shutil.copystat(target, candidate, follow_symlinks=False)
                replacement = _Replacement(artifact.path, target, backup)
                journal = journal.model_copy(
                    update={
                        "phase": "replacing",
                        "active_path": artifact.path,
                        "active_step": "before_backup",
                    }
                )
                _write_transaction_record(journal_path, journal)
                _save_checkpoint(f"before_backup:{artifact.path}", stage)
                _replace_file(target, backup)
                replacements.append(replacement)
                journal = journal.model_copy(update={"active_step": "backup_moved"})
                _write_transaction_record(journal_path, journal)
                _save_checkpoint(f"after_backup:{artifact.path}", stage)
                _replace_file(candidate, target)
                journal = journal.model_copy(
                    update={
                        "active_path": None,
                        "active_step": None,
                        "completed_paths": (*journal.completed_paths, artifact.path),
                    }
                )
                _write_transaction_record(journal_path, journal)
                _save_checkpoint(f"after_install:{artifact.path}", stage)

            saved_at = datetime.now(timezone.utc).isoformat()
            journal = journal.model_copy(
                update={
                    "phase": "publishing_receipt",
                    "active_path": None,
                    "active_step": None,
                }
            )
            _write_transaction_record(journal_path, journal)
            receipt = _DesignerTransactionReceipt(
                approval_id=approval.approval_id,
                project=preparation.project,
                project_file=approval.project_file,
                base_fingerprint=approval.base_fingerprint,
                candidate_id=approval.candidate_id,
                candidate_fingerprint=approval.candidate_fingerprint,
                change_fingerprint=approval.change_fingerprint,
                diff_sha256=approval.diff_sha256,
                artifacts=transaction_artifacts,
                saved_at=saved_at,
            )
            staged_receipt = stage / "receipt.json"
            staged_receipt.write_bytes(_model_bytes(receipt))
            _fsync_file(staged_receipt)
            _replace_file(staged_receipt, receipt_path)
            _save_checkpoint("after_receipt", stage)

            session._mark_saved(
                approval.candidate_fingerprint,
                state.working_files,
            )
            return DesignerSaveResult(
                approval_id=approval.approval_id,
                project=preparation.project,
                project_path=approval.project_path,
                project_file=approval.project_file,
                base_fingerprint=approval.base_fingerprint,
                candidate_id=approval.candidate_id,
                candidate_fingerprint=approval.candidate_fingerprint,
                change_fingerprint=approval.change_fingerprint,
                changed_files=preparation.changed_files,
                receipt_path=str(preparation.receipt_path),
                saved_at=saved_at,
            )
        except DesignerSaveError:
            if replacements:
                self._mark_rolling_back(journal_path, journal)
                errors = self._rollback(replacements, stage)
                if errors:
                    preserve_recovery = True
                    raise DesignerSaveError(
                        "TIDEDSAVE010",
                        "Designer save failed and rollback is incomplete; "
                        "preserved the lock and recovery directory",
                    )
            raise
        except (OSError, ValueError) as error:
            if replacements:
                self._mark_rolling_back(journal_path, journal)
                errors = self._rollback(replacements, stage)
                if errors:
                    preserve_recovery = True
                    raise DesignerSaveError(
                        "TIDEDSAVE010",
                        "Designer save failed and rollback is incomplete; "
                        "preserved the lock and recovery directory",
                    ) from error
            raise DesignerSaveError(
                "TIDEDSAVE007",
                f"Designer save failed: {type(error).__name__}",
            ) from error
        except BaseException:
            preserve_recovery = bool(
                journal_path is not None and journal_path.is_file()
            )
            raise
        finally:
            cleanup_error: OSError | None = None
            if stage is not None and stage.exists() and not preserve_recovery:
                try:
                    _remove_stage(stage)
                except OSError as error:
                    preserve_recovery = True
                    cleanup_error = error
            if lock_handle is not None:
                lock_handle.close()
            if lock_handle is not None and not preserve_recovery:
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError as error:
                    preserve_recovery = True
                    cleanup_error = error
            if not replacements or not preserve_recovery:
                self._remove_empty_directories(created_directories)
            if cleanup_error is not None:
                raise DesignerSaveError(
                    "TIDEDSAVE010",
                    "Designer save reached a consistent source state but cleanup "
                    "is incomplete; preserved recovery evidence",
                ) from cleanup_error

    def _inspect(self, session: DesignerSession) -> _Inspection:
        state = session._capture_save_state()
        evaluation = _evaluate_project(state.project_file, state.working_files)
        blockers: list[DesignerSaveIssue] = []
        if not evaluation.valid:
            blockers.append(
                DesignerSaveIssue(
                    code="TIDEDSAVE001",
                    message="the Designer candidate does not compile",
                )
            )
        inventory_matches = set(state.base_files) == set(state.working_files)
        if not inventory_matches:
            blockers.append(
                DesignerSaveIssue(
                    code="TIDEDSAVE002",
                    message="Designer save cannot add or remove source files",
                )
            )
        changed_files = _changed_files(state.base_files, state.working_files)
        if not changed_files:
            blockers.append(
                DesignerSaveIssue(
                    code="TIDEDSAVE002",
                    message="the Designer session has no source changes to save",
                )
            )
        if any(
            PurePosixPath(path).suffix.lower() not in {".yaml", ".yml"}
            for path in changed_files
        ):
            blockers.append(
                DesignerSaveIssue(
                    code="TIDEDSAVE002",
                    message="Designer save may replace YAML source files only",
                )
            )

        root = state.root
        canonical_project = _canonical_project(root)
        lock_path = root / self.lock_name
        base_state: Literal["current", "stale", "invalid"] = "invalid"
        live_fingerprint: str | None = None
        if os.path.lexists(lock_path):
            blockers.append(
                DesignerSaveIssue(
                    code="TIDEDSAVE006",
                    message="another Designer save owns the application lock",
                )
            )
        else:
            try:
                live_files = _read_project_sources(
                    root,
                    root / PurePosixPath(state.project_file),
                )
            except DesignerError as error:
                blockers.append(
                    DesignerSaveIssue(
                        code="TIDEDSAVE003",
                        message=f"the live application source cannot be read: {error}",
                    )
                )
            else:
                live_fingerprint = _fingerprint_files(live_files)
                if live_fingerprint != _fingerprint_files(state.base_files):
                    base_state = "stale"
                    blockers.append(
                        DesignerSaveIssue(
                            code="TIDEDSAVE003",
                            message=(
                                "the application source changed after the Designer "
                                "session opened"
                            ),
                        )
                    )
                else:
                    base_state = "current"

        diff = _source_diff(state.base_files, state.working_files)
        artifacts = tuple(
            DesignerSaveArtifact(
                path=path,
                base_sha256=_bytes_hash(state.base_files[path]),
                candidate_sha256=_bytes_hash(state.working_files[path]),
                size_bytes=len(state.working_files[path]),
            )
            for path in changed_files
            if path in state.base_files and path in state.working_files
        )
        base_fingerprint = _fingerprint_files(state.base_files)
        candidate_fingerprint = _fingerprint_files(state.working_files)
        candidate_id = (
            "tide-designer-candidate-"
            + candidate_fingerprint.removeprefix("sha256:")[:24]
        )
        change_fingerprint = _change_fingerprint(artifacts) if artifacts else None
        diff_sha256 = _text_hash(diff) if diff else None
        approval_id: str | None = None
        ready = not blockers
        if ready and change_fingerprint is not None and diff_sha256 is not None:
            approval_id = _approval_id(
                canonical_project,
                state.project_file,
                base_fingerprint,
                candidate_id,
                candidate_fingerprint,
                change_fingerprint,
                diff_sha256,
            )
        receipt_path = (
            f".tide/designer/{approval_id}.json" if approval_id is not None else None
        )
        preparation = DesignerSavePreparation(
            ready=ready,
            session_id=state.session_id,
            project=root.name,
            project_path=canonical_project,
            project_file=state.project_file,
            base_state=base_state,
            base_fingerprint=base_fingerprint,
            live_base_fingerprint=live_fingerprint,
            candidate_id=candidate_id,
            candidate_fingerprint=candidate_fingerprint,
            change_fingerprint=change_fingerprint,
            diff_sha256=diff_sha256,
            approval_id=approval_id,
            approval_prompt=f"SAVE {approval_id}" if approval_id else None,
            receipt_path=receipt_path,
            summary=(
                "Designer candidate is ready for explicit save approval"
                if ready
                else f"Designer save is blocked by {len(blockers)} issue(s)"
            ),
            changed_files=changed_files,
            artifacts=artifacts,
            diff=diff,
            diagnostics=evaluation.diagnostics,
            blockers=tuple(blockers),
        )
        return _Inspection(preparation=preparation, state=state)

    @staticmethod
    def _validate_approval(
        preparation: DesignerSavePreparation,
        approval: DesignerSaveApproval,
    ) -> None:
        expected = DesignerSaveApproval.from_preparation(preparation)
        if approval != expected:
            raise DesignerSaveError(
                "TIDEDSAVE004",
                "approval values do not match the current Designer candidate and base",
            )

    @staticmethod
    def _mark_rolling_back(
        journal_path: Path | None,
        journal: _DesignerTransactionJournal | None,
    ) -> None:
        if journal_path is None or journal is None:
            return
        try:
            _write_transaction_record(
                journal_path,
                journal.model_copy(
                    update={
                        "phase": "rolling_back",
                        "active_path": None,
                        "active_step": None,
                    }
                ),
            )
        except OSError:
            pass

    @staticmethod
    def _assert_live_base(
        state: _DesignerSessionState,
        expected_fingerprint: str,
    ) -> None:
        try:
            live = _read_project_sources(
                state.root,
                state.root / PurePosixPath(state.project_file),
            )
        except DesignerError as error:
            raise DesignerSaveError(
                "TIDEDSAVE003",
                f"the live application source cannot be read: {error}",
            ) from error
        if _fingerprint_files(live) != expected_fingerprint:
            raise DesignerSaveError(
                "TIDEDSAVE003",
                "the application source changed after save approval preparation",
            )

    @staticmethod
    def _verify_staged_candidate(
        candidate_root: Path,
        state: _DesignerSessionState,
        approval: DesignerSaveApproval,
    ) -> None:
        staged = _read_project_sources(
            candidate_root,
            candidate_root / PurePosixPath(state.project_file),
        )
        if staged != state.working_files:
            raise DesignerSaveError(
                "TIDEDSAVE005",
                "the staged Designer source differs from the approved candidate",
            )
        if _fingerprint_files(staged) != approval.candidate_fingerprint:
            raise DesignerSaveError(
                "TIDEDSAVE005",
                "the staged Designer fingerprint differs from approval",
            )
        try:
            compile_project(candidate_root / PurePosixPath(state.project_file))
        except CompilationFailed as error:
            raise DesignerSaveError(
                "TIDEDSAVE005",
                "the staged Designer candidate no longer compiles",
            ) from error

    @staticmethod
    def _flush_staged_sources(
        candidate_root: Path,
        files: dict[str, bytes],
    ) -> None:
        for relative in files:
            _fsync_file(candidate_root / PurePosixPath(relative))

    @staticmethod
    def _assert_artifact_base(
        target: Path,
        artifact: DesignerSaveArtifact,
    ) -> None:
        if target.is_symlink() or not target.is_file():
            raise DesignerSaveError(
                "TIDEDSAVE003",
                f"live Designer source is no longer a regular file: {artifact.path}",
            )
        try:
            content = target.read_bytes()
        except OSError as error:
            raise DesignerSaveError(
                "TIDEDSAVE003",
                f"live Designer source cannot be read: {artifact.path}",
            ) from error
        if _bytes_hash(content) != artifact.base_sha256:
            raise DesignerSaveError(
                "TIDEDSAVE003",
                f"live Designer source changed before replacement: {artifact.path}",
            )

    @staticmethod
    def _prepare_receipt_directory(root: Path, receipt_directory: Path) -> list[Path]:
        tide = root / ".tide"
        designer = tide / "designer"
        created: list[Path] = []
        try:
            for directory in (tide, designer):
                if os.path.lexists(directory):
                    if directory.is_symlink() or not directory.is_dir():
                        raise DesignerSaveError(
                            "TIDEDSAVE009",
                            "the Designer receipt path is not a safe directory",
                        )
                else:
                    directory.mkdir()
                    created.append(directory)
            if receipt_directory != designer:
                raise DesignerSaveError(
                    "TIDEDSAVE009",
                    "the Designer receipt escaped its canonical directory",
                )
        except (DesignerSaveError, OSError):
            DesignerSaveService._remove_empty_directories(created)
            raise
        return created

    @staticmethod
    def _remove_empty_directories(directories: list[Path]) -> None:
        for directory in reversed(directories):
            try:
                directory.rmdir()
            except OSError:
                pass

    @staticmethod
    def _rollback(
        replacements: list[_Replacement],
        stage: Path | None,
    ) -> tuple[str, ...]:
        if stage is None:
            return ("the recovery directory is unavailable",)
        errors: list[str] = []
        discard_root = stage / "discard"
        for replacement in reversed(replacements):
            try:
                if os.path.lexists(replacement.target):
                    discard = discard_root / PurePosixPath(replacement.path)
                    discard.parent.mkdir(parents=True, exist_ok=True)
                    _replace_file(replacement.target, discard)
                _replace_file(replacement.backup, replacement.target)
            except OSError as error:
                errors.append(f"{replacement.path}: {type(error).__name__}")
        return tuple(errors)


def _validated_source_path(path: str) -> PurePosixPath:
    normalized = _portable_relative_path(path)
    relative = PurePosixPath(normalized)
    if relative.suffix.lower() not in {".yaml", ".yml"}:
        raise DesignerSaveError(
            "TIDEDSAVE002",
            "Designer save may replace YAML source files only",
        )
    return relative


def _canonical_project(root: Path) -> str:
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise DesignerSaveError(
            "TIDEDSAVE003",
            "the application root cannot be resolved",
        ) from error
    if not resolved.is_dir():
        raise DesignerSaveError(
            "TIDEDSAVE003",
            "the application root is not a directory",
        )
    return os.path.normcase(str(resolved.absolute()))


def _approval_id(
    canonical_project: str,
    project_file: str,
    base_fingerprint: str,
    candidate_id: str,
    candidate_fingerprint: str,
    change_fingerprint: str,
    diff_sha256: str,
) -> str:
    digest = sha256()
    digest.update(b"tide-designer-save-approval-v1\0")
    for value in (
        canonical_project,
        project_file,
        base_fingerprint,
        candidate_id,
        candidate_fingerprint,
        change_fingerprint,
        diff_sha256,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return "tide-designer-approval-" + digest.hexdigest()[:24]


def _change_fingerprint(artifacts: tuple[DesignerSaveArtifact, ...]) -> str:
    digest = sha256()
    digest.update(b"tide-designer-change-v1\0")
    for artifact in artifacts:
        for value in (
            artifact.path,
            artifact.base_sha256,
            artifact.candidate_sha256,
            str(artifact.size_bytes),
        ):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _bytes_hash(content: bytes) -> str:
    return "sha256:" + sha256(content).hexdigest()


def _text_hash(content: str) -> str:
    return _bytes_hash(content.encode("utf-8"))


def _replace_file(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _save_checkpoint(_name: str, _stage: Path) -> None:
    """Test seam for emulating process loss after a durable save phase."""


def _remove_stage(stage: Path) -> None:
    cleanup = stage.with_name(stage.name + ".cleanup")
    if os.path.lexists(cleanup):
        raise OSError("Designer cleanup directory already exists")
    os.replace(stage, cleanup)
    shutil.rmtree(cleanup)


def _fsync_file(path: Path) -> None:
    # Windows requires a writable handle for FlushFileBuffers, which backs
    # Python's fsync implementation there.
    with path.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())
