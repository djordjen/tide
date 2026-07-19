"""Verified, non-overwriting backups for path-based SQLite deployments."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Mapping

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from tide.compiler.normalized import ApplicationModel
from tide.data.sqlalchemy import SQLAlchemyRepository
from tide.data.sqlalchemy_actions import SQLAlchemyActionExecutionStore
from tide.data.sqlalchemy_cursors import SQLAlchemyCursorStore
from tide.runtime.errors import TideRuntimeError


BACKUP_FORMAT = "tide.sqlite-backup"
BACKUP_FORMAT_VERSION = 1
MAX_MANIFEST_BYTES = 65_536


class DatabaseBackupError(TideRuntimeError):
    """A database backup could not be safely created or verified."""

    code = "database_backup_error"


class DatabaseBackupUnsupported(DatabaseBackupError):
    """The configured database requires a database-native backup tool."""

    code = "database_backup_unsupported"


@dataclass(frozen=True, slots=True)
class DatabaseBackupArtifact:
    path: Path
    manifest_path: Path
    sha256: str
    size_bytes: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DatabaseBackupVerification:
    path: Path
    manifest_path: Path
    sha256: str
    size_bytes: int
    created_at: datetime
    application: str
    application_version: str
    database_mode: str


def create_sqlite_backup(
    model: ApplicationModel,
    database_url: str | URL,
    destination: str | Path,
) -> DatabaseBackupArtifact:
    """Create and verify one consistent SQLite snapshot without overwriting files."""

    source = _sqlite_source_path(database_url)
    target = Path(destination).expanduser().resolve()
    manifest_path = _manifest_path(target)
    if source == target or source == manifest_path:
        raise DatabaseBackupError("backup destination must differ from the source database")
    if not source.is_file():
        raise DatabaseBackupError("source SQLite database does not exist")

    reserved: list[Path] = []
    temporary: list[Path] = []
    completed = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        for path in (target, manifest_path):
            _reserve_new_file(path)
            reserved.append(path)

        backup_temporary = _temporary_path(target)
        manifest_temporary = _temporary_path(manifest_path)
        temporary.extend((backup_temporary, manifest_temporary))
        _online_sqlite_backup(source, backup_temporary)
        _check_sqlite_integrity(backup_temporary)
        _validate_application_backup(model, backup_temporary)
        _fsync_file(backup_temporary)

        size_bytes = backup_temporary.stat().st_size
        digest = _sha256(backup_temporary)
        created_at = datetime.now(timezone.utc)
        manifest = {
            "format": BACKUP_FORMAT,
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": created_at.isoformat(),
            "application": {
                "name": model.name,
                "version": model.version,
                "schema_version": model.schema_version,
            },
            "database": {"dialect": "sqlite", "mode": str(model.database["mode"])},
            "backup": {
                "filename": target.name,
                "size_bytes": size_bytes,
                "sha256": digest,
            },
        }
        manifest_temporary.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _fsync_file(manifest_temporary)

        os.replace(backup_temporary, target)
        temporary.remove(backup_temporary)
        os.replace(manifest_temporary, manifest_path)
        temporary.remove(manifest_temporary)
        completed = True
        return DatabaseBackupArtifact(
            path=target,
            manifest_path=manifest_path,
            sha256=digest,
            size_bytes=size_bytes,
            created_at=created_at,
        )
    except DatabaseBackupError:
        raise
    except (OSError, SQLAlchemyError, sqlite3.Error, ValueError) as error:
        raise DatabaseBackupError(
            f"SQLite backup failed safely ({type(error).__name__})"
        ) from error
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
        if not completed:
            for path in reserved:
                path.unlink(missing_ok=True)


def verify_sqlite_backup(
    model: ApplicationModel,
    backup: str | Path,
    *,
    manifest: str | Path | None = None,
) -> DatabaseBackupVerification:
    """Verify manifest identity, bytes, SQLite integrity, and TIDE compatibility."""

    backup_path = Path(backup).expanduser().resolve()
    manifest_path = (
        Path(manifest).expanduser().resolve()
        if manifest is not None
        else _manifest_path(backup_path)
    )
    if not backup_path.is_file():
        raise DatabaseBackupError("backup file does not exist")
    if not manifest_path.is_file():
        raise DatabaseBackupError("backup manifest does not exist")
    try:
        manifest_size = manifest_path.stat().st_size
    except OSError as error:
        raise DatabaseBackupError("backup manifest could not be inspected") from error
    if manifest_size > MAX_MANIFEST_BYTES:
        raise DatabaseBackupError("backup manifest exceeds the safe size limit")

    document = _read_manifest(manifest_path)
    created_at, expected_size, expected_digest = _validate_manifest(
        model,
        backup_path,
        document,
    )
    try:
        actual_size = backup_path.stat().st_size
    except OSError as error:
        raise DatabaseBackupError("backup file could not be inspected") from error
    if actual_size != expected_size:
        raise DatabaseBackupError("backup size does not match its manifest")
    actual_digest = _sha256(backup_path)
    if actual_digest != expected_digest:
        raise DatabaseBackupError("backup SHA-256 does not match its manifest")

    _check_sqlite_integrity(backup_path)
    _validate_application_backup(model, backup_path)
    return DatabaseBackupVerification(
        path=backup_path,
        manifest_path=manifest_path,
        sha256=actual_digest,
        size_bytes=actual_size,
        created_at=created_at,
        application=model.name,
        application_version=model.version,
        database_mode=str(model.database["mode"]),
    )


def _sqlite_source_path(database_url: str | URL) -> Path:
    try:
        parsed = make_url(database_url)
    except (SQLAlchemyError, ValueError) as error:
        raise DatabaseBackupError("database URL is invalid") from error
    if parsed.get_backend_name() != "sqlite":
        raise DatabaseBackupUnsupported(
            "automated backup currently supports only path-based SQLite; "
            "use the database vendor's native backup and restore tools"
        )
    database = parsed.database
    if database in {None, "", ":memory:"}:
        raise DatabaseBackupUnsupported(
            "in-memory SQLite databases cannot be backed up by this command"
        )
    if parsed.query.get("uri") or str(database).startswith("file:"):
        raise DatabaseBackupUnsupported(
            "SQLite URI databases are not supported by the backup command"
        )
    return Path(str(database)).expanduser().resolve()


def _online_sqlite_backup(source: Path, destination: Path) -> None:
    source_uri = f"{source.as_uri()}?mode=ro"
    try:
        with closing(
            sqlite3.connect(source_uri, uri=True)
        ) as source_connection, closing(
            sqlite3.connect(destination)
        ) as destination_connection:
            source_connection.backup(destination_connection)
    except sqlite3.Error as error:
        raise DatabaseBackupError(
            f"SQLite online backup failed ({type(error).__name__})"
        ) from error


def _check_sqlite_integrity(path: Path) -> None:
    uri = f"{path.as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            results = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as error:
        raise DatabaseBackupError(
            f"SQLite integrity check failed ({type(error).__name__})"
        ) from error
    if results != [("ok",)]:
        raise DatabaseBackupError("SQLite integrity check did not return 'ok'")


def _validate_application_backup(model: ApplicationModel, path: Path) -> None:
    repository = SQLAlchemyRepository(
        model,
        URL.create("sqlite+pysqlite", database=str(path)),
    )
    try:
        repository.check_readiness()
        if str(model.database["mode"]) == "managed":
            SQLAlchemyCursorStore(repository.engine, mode="managed").validate_schema()
            SQLAlchemyActionExecutionStore(
                repository.engine,
                mode="managed",
            ).validate_schema()
    except (SQLAlchemyError, TideRuntimeError, ValueError) as error:
        raise DatabaseBackupError(
            f"backup is not compatible with the compiled application: {error}"
        ) from error
    finally:
        repository.dispose()


def _read_manifest(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatabaseBackupError("backup manifest is not valid UTF-8 JSON") from error
    if not isinstance(document, Mapping):
        raise DatabaseBackupError("backup manifest root must be an object")
    return document


def _validate_manifest(
    model: ApplicationModel,
    backup_path: Path,
    document: Mapping[str, Any],
) -> tuple[datetime, int, str]:
    if document.get("format") != BACKUP_FORMAT:
        raise DatabaseBackupError("backup manifest format is unsupported")
    if document.get("format_version") != BACKUP_FORMAT_VERSION:
        raise DatabaseBackupError("backup manifest version is unsupported")

    application = _mapping(document, "application")
    expected_application = {
        "name": model.name,
        "version": model.version,
        "schema_version": model.schema_version,
    }
    if any(application.get(key) != value for key, value in expected_application.items()):
        raise DatabaseBackupError("backup manifest targets a different application model")

    database = _mapping(document, "database")
    if database.get("dialect") != "sqlite":
        raise DatabaseBackupError("backup manifest does not describe SQLite")
    if database.get("mode") != str(model.database["mode"]):
        raise DatabaseBackupError("backup manifest database mode does not match the model")

    backup = _mapping(document, "backup")
    if backup.get("filename") != backup_path.name:
        raise DatabaseBackupError("backup filename does not match its manifest")
    size_bytes = backup.get("size_bytes")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 1:
        raise DatabaseBackupError("backup manifest size is invalid")
    digest = backup.get("sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise DatabaseBackupError("backup manifest SHA-256 is invalid")

    created_at_value = document.get("created_at")
    if not isinstance(created_at_value, str):
        raise DatabaseBackupError("backup manifest creation time is invalid")
    try:
        created_at = datetime.fromisoformat(created_at_value)
    except ValueError as error:
        raise DatabaseBackupError("backup manifest creation time is invalid") from error
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise DatabaseBackupError("backup manifest creation time must include a timezone")
    return created_at, size_bytes, digest


def _mapping(document: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = document.get(key)
    if not isinstance(value, Mapping):
        raise DatabaseBackupError(f"backup manifest {key} must be an object")
    return value


def _manifest_path(backup: Path) -> Path:
    return backup.with_name(f"{backup.name}.manifest.json")


def _temporary_path(target: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".partial",
        dir=target.parent,
    )
    os.close(descriptor)
    return Path(name)


def _reserve_new_file(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise DatabaseBackupError(
            "backup output or manifest already exists; files are never overwritten"
        ) from error
    else:
        os.close(descriptor)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise DatabaseBackupError("backup bytes could not be read") from error
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    # Windows requires a writable descriptor for FlushFileBuffers/os.fsync.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())
