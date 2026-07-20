from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from tide import compile_project
from tide.cli import main
from tide.compiler.normalized import immutable_mapping
from tide.data import (
    DatabaseBackupError,
    DatabaseBackupUnsupported,
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemyRepository,
    create_sqlite_backup,
    verify_sqlite_backup,
)


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
CUSTOMER = {
    "id": 1,
    "code": "ACME",
    "name": "ACME Ltd",
    "email": None,
    "active": True,
}


def test_online_backup_is_a_verified_snapshot_without_source_disclosure(
    tmp_path: Path,
) -> None:
    model, source_url = _managed_database(tmp_path / "source-SUPERSECRET.db")
    source = SQLAlchemyRepository(model, source_url)
    source.seed("crm.Customer", [CUSTOMER])
    source.dispose()
    destination = tmp_path / "backups" / "invoicing.db"

    artifact = create_sqlite_backup(model, source_url, destination)

    changed_source = SQLAlchemyRepository(model, source_url)
    changed_source.seed(
        "crm.Customer",
        [{**CUSTOMER, "id": 2, "code": "LATER", "name": "Later Ltd"}],
    )
    changed_source.dispose()
    verification = verify_sqlite_backup(model, destination)
    snapshot = SQLAlchemyRepository(model, f"sqlite+pysqlite:///{destination.as_posix()}")
    try:
        customers = snapshot.all("crm.Customer")
    finally:
        snapshot.dispose()

    assert len(customers) == 1
    assert {key: customers[0][key] for key in CUSTOMER} == CUSTOMER
    assert artifact.path == destination.resolve()
    assert verification.sha256 == artifact.sha256
    assert verification.size_bytes == artifact.size_bytes
    manifest_text = artifact.manifest_path.read_text(encoding="utf-8")
    assert "SUPERSECRET" not in manifest_text
    assert source_url not in manifest_text


def test_backup_refuses_to_overwrite_either_artifact(tmp_path: Path) -> None:
    model, source_url = _managed_database(tmp_path / "source.db")
    destination = tmp_path / "backup.db"
    artifact = create_sqlite_backup(model, source_url, destination)

    with pytest.raises(DatabaseBackupError, match="never overwritten"):
        create_sqlite_backup(model, source_url, destination)

    assert artifact.path.is_file()
    assert artifact.manifest_path.is_file()


def test_backup_verification_detects_manifest_and_byte_tampering(tmp_path: Path) -> None:
    model, source_url = _managed_database(tmp_path / "source.db")
    artifact = create_sqlite_backup(model, source_url, tmp_path / "backup.db")
    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    manifest["backup"]["sha256"] = "0" * 64
    artifact.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DatabaseBackupError, match="SHA-256"):
        verify_sqlite_backup(model, artifact.path)

    artifact.manifest_path.write_text(
        json.dumps(
            {
                **manifest,
                "backup": {**manifest["backup"], "sha256": artifact.sha256},
            }
        ),
        encoding="utf-8",
    )
    artifact.path.write_bytes(artifact.path.read_bytes() + b"changed")
    with pytest.raises(DatabaseBackupError, match="size"):
        verify_sqlite_backup(model, artifact.path)


def test_backup_verification_is_bound_to_compiled_application(tmp_path: Path) -> None:
    model, source_url = _managed_database(tmp_path / "source.db")
    artifact = create_sqlite_backup(model, source_url, tmp_path / "backup.db")

    with pytest.raises(DatabaseBackupError, match="different application model"):
        verify_sqlite_backup(replace(model, version="9.9.9"), artifact.path)


def test_backup_rejects_non_file_sqlite_and_vendor_databases(tmp_path: Path) -> None:
    model = compile_project(INVOICING)
    destination = tmp_path / "backup.db"

    with pytest.raises(DatabaseBackupUnsupported, match="in-memory"):
        create_sqlite_backup(model, "sqlite+pysqlite:///:memory:", destination)
    with pytest.raises(DatabaseBackupUnsupported, match="vendor's native"):
        create_sqlite_backup(
            model,
            "mssql+pyodbc://user:SUPERSECRET@example.invalid/TIDE",
            destination,
        )

    assert not destination.exists()


def test_legacy_sqlite_backup_does_not_require_framework_tables(tmp_path: Path) -> None:
    model = replace(
        compile_project(INVOICING),
        database=immutable_mapping({"mode": "legacy"}),
    )
    source = tmp_path / "legacy.db"
    url = f"sqlite+pysqlite:///{source.as_posix()}"
    external_owner = SQLAlchemyRepository(model, url)
    external_owner.metadata.create_all(external_owner.engine)
    external_owner.dispose()

    artifact = create_sqlite_backup(model, url, tmp_path / "legacy-backup.db")
    verification = verify_sqlite_backup(model, artifact.path)

    assert verification.database_mode == "legacy"


def test_failed_backup_removes_reserved_outputs(tmp_path: Path) -> None:
    model = compile_project(INVOICING)
    source = tmp_path / "empty.db"
    source.touch()
    destination = tmp_path / "backup.db"

    with pytest.raises(DatabaseBackupError, match="not compatible"):
        create_sqlite_backup(
            model,
            f"sqlite+pysqlite:///{source.as_posix()}",
            destination,
        )

    assert not destination.exists()
    assert not Path(f"{destination}.manifest.json").exists()
    assert list(tmp_path.glob("*.partial")) == []


def test_database_backup_cli_creates_and_verifies_artifacts(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _model, source_url = _managed_database(tmp_path / "source.db")
    destination = tmp_path / "backup.db"
    monkeypatch.setenv("BACKUP_DATABASE_URL", source_url)

    backed_up = main(
        [
            "db",
            "backup",
            str(INVOICING),
            "--database-env",
            "BACKUP_DATABASE_URL",
            "--output",
            str(destination),
        ]
    )
    backup_output = capsys.readouterr()
    verified = main(
        ["db", "verify-backup", str(INVOICING), str(destination)]
    )
    verification_output = capsys.readouterr()

    assert backed_up == 0
    assert backup_output.err == ""
    assert "Database backup complete" in backup_output.out
    assert "sha256=" in backup_output.out
    assert verified == 0
    assert verification_output.err == ""
    assert "Database backup verification passed" in verification_output.out


def test_database_backup_cli_does_not_echo_vendor_url(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    secret_url = "mssql+pyodbc://user:SUPERSECRET@example.invalid/TIDE"
    monkeypatch.setenv("BACKUP_DATABASE_URL", secret_url)

    result = main(
        [
            "db",
            "backup",
            str(INVOICING),
            "--database-env",
            "BACKUP_DATABASE_URL",
            "--output",
            str(tmp_path / "backup.db"),
        ]
    )

    error = capsys.readouterr().err
    assert result == 1
    assert "vendor's native backup" in error
    assert secret_url not in error
    assert "SUPERSECRET" not in error


def _managed_database(path: Path):
    model = compile_project(INVOICING)
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    repository = SQLAlchemyRepository(model, url)
    repository.create_schema()
    SQLAlchemyCursorStore(repository.engine, mode="managed").create_schema()
    SQLAlchemyActionExecutionStore(repository.engine, mode="managed").create_schema()
    repository.dispose()
    return model, url
