"""Telling a server where the files live, and refusing to guess.

An application that declares a file field needs somewhere to put files.
Where that is is deployment configuration, like the database URL: the
application says it has documents, an operator says where they go. A server
that cannot answer that question does not start, because a running server
that accepts uploads and drops them is worse than one that never opened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import uvicorn

from tide import compile_project
from tide.cli import main
from tide.data import SQLAlchemyRepository
from tide.data.attachment_files import FilesystemAttachmentBytes
from tide.data.framework_schema import framework_stores
from tide.data.sqlalchemy_attachments import SQLAlchemyAttachmentRows
from tide.services.attachments import AttachmentService

ENTITY = """\
entity: docs.Note
display: title
expose:
  rest: {path: notes, operations: [list, get, create, update]}
permissions:
  list: docs.note.read
  read: docs.note.read
  create: docs.note.write
  update: docs.note.write
fields:
  id: {type: integer, primary_key: true}
  title: {type: string, length: 40, required: true}
  scan: {type: file, max_size: 1kb}
"""

PLAIN_ENTITY = """\
entity: docs.Memo
display: title
expose:
  rest: {path: memos, operations: [list, get]}
permissions:
  list: docs.note.read
  read: docs.note.read
fields:
  id: {type: integer, primary_key: true}
  title: {type: string, length: 40, required: true}
"""

POLICIES = """\
permissions: [docs.note.read, docs.note.write]
roles:
  clerk:
    grants: [docs.note.read, docs.note.write]
"""


@pytest.fixture(autouse=True)
def development_token(monkeypatch: Any) -> None:
    """Every case here is about files, so the token is never the reason."""

    monkeypatch.setenv(
        "TIDE_API_TOKEN", "a-development-token-that-is-long-enough-to-pass"
    )


def _project(tmp_path: Path, entity: str = ENTITY) -> Path:
    project = tmp_path / "notes"
    (project / "models").mkdir(parents=True)
    (project / "security").mkdir()
    (project / "tide.yaml").write_text(
        'schema_version: "0.1"\n'
        "application: {name: Notes, version: 0.1.0}\n"
        "model: {paths: [models]}\n"
        "security: {paths: [security]}\n",
        encoding="utf-8",
    )
    (project / "models" / "note.yaml").write_text(entity, encoding="utf-8")
    (project / "security" / "policies.yaml").write_text(POLICIES, encoding="utf-8")
    return project


def _serve(*arguments: str) -> int:
    return main(["serve", *arguments])


def test_serving_files_without_saying_where_they_go_refuses(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    monkeypatch.delenv("TIDE_ATTACHMENTS_ROOT", raising=False)
    monkeypatch.setenv(
        "NOTES_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'db.sqlite').as_posix()}"
    )

    result = _serve(
        str(_project(tmp_path)),
        "--database-env",
        "NOTES_DATABASE_URL",
        "--create-schema",
        "--role",
        "clerk",
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: docs.Note declares a file field, so this server "
        "needs somewhere to keep files: pass --attachments-root or set "
        "TIDE_ATTACHMENTS_ROOT\n"
    )


def test_an_application_without_file_fields_needs_no_root(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    """The requirement is the application's, not the framework's."""

    monkeypatch.delenv("TIDE_ATTACHMENTS_ROOT", raising=False)
    launched: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda app, **options: launched.update(app=app, options=options),
    )
    monkeypatch.setenv(
        "MEMOS_DATABASE_URL",
        f"sqlite+pysqlite:///{(tmp_path / 'memos.sqlite').as_posix()}",
    )

    result = _serve(
        str(_project(tmp_path, PLAIN_ENTITY)),
        "--database-env",
        "MEMOS_DATABASE_URL",
        "--create-schema",
        "--role",
        "clerk",
    )

    assert result == 0
    assert launched["app"].state.tide.attachments is None


def test_a_run_that_keeps_no_records_keeps_no_files_either(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """An in-memory run throws its database away when it stops.

    Files go the same way and for the same reason, so this is the one case
    that needs no root: there is nothing here to keep.
    """

    monkeypatch.delenv("TIDE_ATTACHMENTS_ROOT", raising=False)
    launched: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda app, **options: launched.update(app=app, options=options),
    )

    result = _serve(str(_project(tmp_path)), "--role", "clerk")

    assert result == 0
    attachments = launched["app"].state.tide.attachments
    assert attachments is not None
    assert attachments.bytes.all_guids() == ()


def test_a_root_is_prepared_where_it_is_named(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv(
        "NOTES_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'db.sqlite').as_posix()}"
    )
    launched: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda app, **options: launched.update(app=app, options=options),
    )
    root = tmp_path / "files"

    result = _serve(
        str(_project(tmp_path)),
        "--database-env",
        "NOTES_DATABASE_URL",
        "--create-schema",
        "--attachments-root",
        str(root),
        "--role",
        "clerk",
    )

    assert result == 0
    assert (root / "tmp").is_dir()
    assert launched["app"].state.tide.attachments is not None


def _reconciliation(tmp_path: Path, monkeypatch: Any) -> tuple[Path, Path, str]:
    """An application, a database and a root, with one file already in them."""

    project = _project(tmp_path)
    root = tmp_path / "files"
    url = f"sqlite+pysqlite:///{(tmp_path / 'db.sqlite').as_posix()}"
    monkeypatch.setenv("NOTES_DATABASE_URL", url)

    model = compile_project(project)
    repository = SQLAlchemyRepository(model, url)
    repository.create_schema()
    repository.dispose()
    stores = framework_stores(url)
    stores.create_schema()
    rows = SQLAlchemyAttachmentRows(url, mode="managed")
    attachments = AttachmentService(
        model, rows, FilesystemAttachmentBytes(root)
    )
    record = attachments.stage(
        "docs.Note",
        "scan",
        filename="confirmation.pdf",
        content_type="application/pdf",
        chunks=[b"%PDF-1.4"],
        principal="local:clerk",
    )
    rows.dispose()
    return project, root, record.guid


def _check(project: Path, root: Path, *extra: str) -> int:
    return main(
        [
            "attachments",
            "check",
            str(project),
            "--database-env",
            "NOTES_DATABASE_URL",
            "--attachments-root",
            str(root),
            *extra,
        ]
    )


def test_a_store_that_agrees_with_itself_passes(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    project, root, _ = _reconciliation(tmp_path, monkeypatch)

    result = _check(project, root)

    assert result == 0
    assert "1 file" in capsys.readouterr().out


def test_a_row_whose_file_is_gone_is_reported(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    """The direction that matters most: somebody will ask for that document."""

    project, root, guid = _reconciliation(tmp_path, monkeypatch)
    (root / guid[:2] / guid).unlink()

    result = _check(project, root)
    output = capsys.readouterr()

    assert result == 1
    assert guid in output.out
    assert "no file" in output.out


def test_bytes_no_row_names_are_reported(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    project, root, _ = _reconciliation(tmp_path, monkeypatch)
    stray = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    (root / stray[:2]).mkdir(parents=True, exist_ok=True)
    (root / stray[:2] / stray).write_bytes(b"left behind")

    result = _check(project, root)
    output = capsys.readouterr()

    assert result == 1
    assert stray in output.out
    assert "no row" in output.out


def test_bytes_that_changed_under_their_row_are_reported(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    """The one that says something went wrong outside TIDE."""

    project, root, guid = _reconciliation(tmp_path, monkeypatch)
    (root / guid[:2] / guid).write_bytes(b"not what was uploaded")

    result = _check(project, root)
    output = capsys.readouterr()

    assert result == 1
    assert guid in output.out
    assert "changed" in output.out


def test_a_sweep_reclaims_only_what_has_waited(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    """A staged upload is somebody's open form until the grace runs out."""

    project, root, guid = _reconciliation(tmp_path, monkeypatch)

    kept = _check(project, root, "--sweep")
    assert kept == 0
    assert (root / guid[:2] / guid).exists()

    swept = _check(project, root, "--sweep", "--grace", "0")
    output = capsys.readouterr()

    assert swept == 0
    assert not (root / guid[:2] / guid).exists()
    assert "reclaimed 1" in output.out


def test_the_environment_can_name_the_root_instead(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """The same shape as the database URL: configuration, not YAML."""

    root = tmp_path / "from-environment"
    monkeypatch.setenv("TIDE_ATTACHMENTS_ROOT", str(root))
    monkeypatch.setenv(
        "NOTES_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'db.sqlite').as_posix()}"
    )
    launched: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda app, **options: launched.update(app=app, options=options),
    )

    result = _serve(
        str(_project(tmp_path)),
        "--database-env",
        "NOTES_DATABASE_URL",
        "--create-schema",
        "--role",
        "clerk",
    )

    assert result == 0
    assert (root / "tmp").is_dir()
