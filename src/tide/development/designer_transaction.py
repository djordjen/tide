"""Private durable transaction records shared by Designer save and recovery."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
from secrets import token_hex
from typing import IO, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from tide.development.designer import _portable_relative_path


class _TransactionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _TransactionArtifact(_TransactionModel):
    path: str
    base_sha256: str
    candidate_sha256: str
    size_bytes: int


class _DesignerTransactionLock(_TransactionModel):
    schema_version: Literal["1"] = "1"
    operation: Literal["existing_application_designer_save"] = (
        "existing_application_designer_save"
    )
    transaction_id: str
    approval_id: str
    project_path: str
    project_file: str
    base_fingerprint: str
    candidate_id: str
    candidate_fingerprint: str
    change_fingerprint: str
    diff_sha256: str
    receipt_path: str
    stage_name: str
    artifacts: tuple[_TransactionArtifact, ...]
    created_at: str


class _DesignerTransactionJournal(_DesignerTransactionLock):
    phase: Literal[
        "prepared",
        "replacing",
        "publishing_receipt",
        "rolling_back",
        "recovering",
    ] = "prepared"
    active_path: str | None = None
    active_step: Literal["before_backup", "backup_moved"] | None = None
    completed_paths: tuple[str, ...] = ()


class _DesignerTransactionReceipt(_TransactionModel):
    schema_version: Literal["1"] = "1"
    operation: Literal["existing_application_designer_save"] = (
        "existing_application_designer_save"
    )
    workspace_writes_performed: Literal[True] = True
    approval_id: str
    project: str
    project_file: str
    base_fingerprint: str
    candidate_id: str
    candidate_fingerprint: str
    change_fingerprint: str
    diff_sha256: str
    artifacts: tuple[_TransactionArtifact, ...]
    transaction: Literal["exclusive-lock-atomic-file-replace-with-rollback"] = (
        "exclusive-lock-atomic-file-replace-with-rollback"
    )
    saved_at: str


class _TransactionLockBusy(RuntimeError):
    pass


class _TransactionRecordError(RuntimeError):
    pass


class _TransactionLockHandle:
    """Own a one-byte OS lock for the lifetime of a save or recovery."""

    def __init__(self, path: Path, stream: IO[bytes]) -> None:
        self.path = path
        self.stream = stream
        self.closed = False

    @classmethod
    def create(
        cls,
        path: Path,
        record: _DesignerTransactionLock,
    ) -> _TransactionLockHandle:
        try:
            descriptor = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
                0o600,
            )
        except FileExistsError as error:
            raise _TransactionLockBusy("the Designer lock already exists") from error
        stream = os.fdopen(descriptor, "r+b", buffering=0)
        handle = cls(path, stream)
        try:
            stream.write(b"\n")
            stream.flush()
            _lock_stream(stream)
            handle.write(record)
        except BaseException:
            handle.close()
            path.unlink(missing_ok=True)
            raise
        return handle

    @classmethod
    def open(cls, path: Path) -> _TransactionLockHandle:
        if path.is_symlink() or not path.is_file():
            raise _TransactionRecordError(
                "the Designer transaction lock is not a regular file"
            )
        stream = path.open("r+b", buffering=0)
        handle = cls(path, stream)
        try:
            _lock_stream(stream)
        except OSError as error:
            handle.close()
            raise _TransactionLockBusy(
                "the Designer transaction is still active"
            ) from error
        return handle

    def read(self) -> _DesignerTransactionLock:
        try:
            self.stream.seek(0)
            payload = json.loads(self.stream.read().decode("utf-8"))
            return _DesignerTransactionLock.model_validate(payload)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            raise _TransactionRecordError(
                "the Designer transaction lock is malformed or unsupported"
            ) from error

    def write(self, record: _DesignerTransactionLock) -> None:
        content = _model_bytes(record)
        self.stream.seek(0)
        self.stream.truncate()
        self.stream.write(content)
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self) -> None:
        if self.closed:
            return
        try:
            _unlock_stream(self.stream)
        finally:
            self.stream.close()
            self.closed = True

    def __enter__(self) -> _TransactionLockHandle:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


def _write_transaction_record(
    path: Path,
    record: _DesignerTransactionJournal,
) -> None:
    temporary = path.with_name(f".{path.name}.{token_hex(8)}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(_model_bytes(record))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_transaction_record(path: Path) -> _DesignerTransactionJournal:
    if path.is_symlink() or not path.is_file():
        raise _TransactionRecordError(
            "the Designer transaction journal is not a regular file"
        )
    try:
        content = path.read_text(encoding="utf-8")
        return _DesignerTransactionJournal.model_validate_json(content)
    except (OSError, UnicodeDecodeError, ValidationError) as error:
        raise _TransactionRecordError(
            "the Designer transaction journal is malformed or unsupported"
        ) from error


def _read_transaction_receipt(path: Path) -> _DesignerTransactionReceipt:
    if path.is_symlink() or not path.is_file():
        raise _TransactionRecordError(
            "the Designer transaction receipt is not a regular file"
        )
    try:
        content = path.read_text(encoding="utf-8")
        return _DesignerTransactionReceipt.model_validate_json(content)
    except (OSError, UnicodeDecodeError, ValidationError) as error:
        raise _TransactionRecordError(
            "the Designer transaction receipt is malformed or unsupported"
        ) from error


def _validated_transaction_path(path: str) -> PurePosixPath:
    normalized = _portable_relative_path(path)
    relative = PurePosixPath(normalized)
    if relative.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("Designer transactions may contain YAML source files only")
    return relative


def _canonical_project_path(root: Path) -> str:
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("the application root is not a directory")
    return os.path.normcase(str(resolved.absolute()))


def _content_sha256(content: bytes) -> str:
    return "sha256:" + sha256(content).hexdigest()


def _model_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


if os.name == "nt":
    import msvcrt

    def _lock_stream(stream: IO[bytes]) -> None:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_stream(stream: IO[bytes]) -> None:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _lock_stream(stream: IO[bytes]) -> None:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_stream(stream: IO[bytes]) -> None:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
