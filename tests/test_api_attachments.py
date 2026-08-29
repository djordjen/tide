"""Uploading and downloading a file over the wire.

The routes are deliberately shaped around what already decides things.
Uploading is scoped to an entity and a field, so the field's declared bound
and kinds are enforced by the same declaration the form shows; downloading
is scoped to a *record*, so the entity permission, the row policies and the
field's own read security all run before a byte moves -- reusing the record
read rather than asking the questions again.

The application here is built for these tests rather than taken from
`applications/invoicing`: a file field on the reference application changes
what the documentation walks through, which is its own change with its own
sweep.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from tide import compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.runtime import Principal
from tide.services import RecordsService
from tide.services.attachment_store import (
    InMemoryAttachmentBytes,
    InMemoryAttachmentRows,
)
from tide.services.attachments import AttachmentService

TOKEN = "tide-development-token-that-is-long-enough"
PDF = b"%PDF-1.4 countersigned"

ENTITY = """\
entity: docs.Note
display: title
expose:
  rest: {path: notes, operations: [list, get, create, update, delete]}
permissions:
  list: docs.note.read
  read: docs.note.read
  create: docs.note.write
  update: docs.note.write
  delete: docs.note.write
fields:
  id: {type: integer, primary_key: true}
  title: {type: string, length: 40, required: true}
  scan: {type: file, max_size: 1kb, accept: [pdf], label: Signed scan}
  secret_file: {type: file, max_size: 1kb}
"""

BROWSE = """\
view: docs.Note.browse
entity: docs.Note
kind: browse
columns: [title]
search: [title]
"""

FORM = """\
view: docs.Note.form
entity: docs.Note
kind: form
layout:
  - group: Note
    rows:
      - [title]
      - [scan]
"""

DEFAULTS = """\
navigation:
  - label: Documents
    items:
      - view: docs.Note.browse
"""

POLICIES = """\
permissions: [docs.note.read, docs.note.write, docs.note.secrets]
roles:
  clerk:
    grants: [docs.note.read, docs.note.write]
  auditor:
    grants: [docs.note.read]
field_policies:
  - entity: docs.Note
    field: secret_file
    read: docs.note.secrets
    write: docs.note.secrets
"""


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory) -> Any:
    project = tmp_path_factory.mktemp("wire") / "notes"
    (project / "models").mkdir(parents=True)
    (project / "security").mkdir()
    (project / "views").mkdir()
    (project / "presentation").mkdir()
    (project / "tide.yaml").write_text(
        'schema_version: "0.1"\n'
        "application: {name: Notes, version: 0.1.0}\n"
        "model: {paths: [models]}\n"
        "views: {paths: [views]}\n"
        "presentation: {defaults: presentation/defaults.yaml}\n"
        "security: {paths: [security]}\n",
        encoding="utf-8",
    )
    (project / "models" / "note.yaml").write_text(ENTITY, encoding="utf-8")
    (project / "views" / "note-browse.yaml").write_text(BROWSE, encoding="utf-8")
    (project / "views" / "note-form.yaml").write_text(FORM, encoding="utf-8")
    (project / "presentation" / "defaults.yaml").write_text(
        DEFAULTS, encoding="utf-8"
    )
    (project / "security" / "policies.yaml").write_text(POLICIES, encoding="utf-8")
    return compile_project(project)


def _app(model: Any, role: str = "clerk") -> tuple[Any, AttachmentService]:
    repository = InMemoryRepository()
    attachments = AttachmentService(
        model, InMemoryAttachmentRows(), InMemoryAttachmentBytes()
    )
    records = RecordsService(model, repository, attachments=attachments)
    app = build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN, Principal("api:test", roles=frozenset({role}))
        ),
        attachments=attachments,
    )
    return app, attachments


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def _authorization() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _upload_headers(filename: str = "confirmation.pdf") -> dict[str, str]:
    return {
        **_authorization(),
        "X-Tide-Filename": filename,
        "Content-Type": "application/pdf",
    }


def test_uploading_answers_what_the_record_will_say_about_the_file(model: Any) -> None:
    app, attachments = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            anonymous = await client.post(
                "/api/v1/notes/_files/scan", content=PDF
            )
            staged = await client.post(
                "/api/v1/notes/_files/scan",
                headers=_upload_headers(),
                content=PDF,
            )

        assert anonymous.status_code == 401
        assert staged.status_code == 201
        body = staged.json()
        assert set(body) == {"identity", "filename", "size", "content_type"}
        assert body["filename"] == "confirmation.pdf"
        assert body["size"] == len(PDF)
        assert body["content_type"] == "application/pdf"
        record = attachments.rows.get(body["identity"])
        assert record is not None and record.record_id is None

    asyncio.run(exercise())


def test_uploading_refuses_a_field_that_holds_no_file(model: Any) -> None:
    """Absent rather than denied: `title` has no upload to refuse."""

    app, _ = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            scalar = await client.post(
                "/api/v1/notes/_files/title",
                headers=_upload_headers(),
                content=PDF,
            )
            unknown = await client.post(
                "/api/v1/notes/_files/nothing",
                headers=_upload_headers(),
                content=PDF,
            )

        assert scalar.status_code == 404
        assert unknown.status_code == 404

    asyncio.run(exercise())


def test_uploading_refuses_a_kind_the_field_does_not_accept(model: Any) -> None:
    app, attachments = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            refused = await client.post(
                "/api/v1/notes/_files/scan",
                headers=_upload_headers("notes.txt"),
                content=b"plain",
            )

        assert refused.status_code == 422
        assert refused.json()["issues"][0]["fields"] == ["scan"]
        assert attachments.rows.all_records() == ()

    asyncio.run(exercise())


def test_uploading_refuses_more_than_the_field_declared(model: Any) -> None:
    """Refused while the body is arriving, not after it has all landed."""

    app, attachments = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            refused = await client.post(
                "/api/v1/notes/_files/scan",
                headers=_upload_headers(),
                content=b"%PDF" + b"x" * 4096,
            )

        assert refused.status_code == 413
        assert attachments.rows.all_records() == ()
        assert attachments.bytes.all_guids() == ()

    asyncio.run(exercise())


def test_uploading_needs_the_name_the_file_had(model: Any) -> None:
    app, _ = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            nameless = await client.post(
                "/api/v1/notes/_files/scan",
                headers=_authorization(),
                content=PDF,
            )

        assert nameless.status_code == 400

    asyncio.run(exercise())


def test_a_reader_may_not_upload(model: Any) -> None:
    app, _ = _app(model, role="auditor")

    async def exercise() -> None:
        async with _client(app) as client:
            refused = await client.post(
                "/api/v1/notes/_files/scan",
                headers=_upload_headers(),
                content=PDF,
            )

        assert refused.status_code == 403

    asyncio.run(exercise())


def test_a_field_this_identity_may_not_write_offers_no_upload(model: Any) -> None:
    app, _ = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            refused = await client.post(
                "/api/v1/notes/_files/secret_file",
                headers=_upload_headers(),
                content=PDF,
            )

        assert refused.status_code == 403

    asyncio.run(exercise())


def test_a_record_names_its_file_and_hands_it_back(model: Any) -> None:
    app, attachments = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            staged = await client.post(
                "/api/v1/notes/_files/scan",
                headers=_upload_headers(),
                content=PDF,
            )
            guid = staged.json()["identity"]
            created = await client.post(
                "/api/v1/notes",
                headers=_authorization(),
                json={"title": "a note", "scan": guid},
            )
            identity = created.json()["id"]
            fetched = await client.get(
                f"/api/v1/notes/{identity}", headers=_authorization()
            )
            downloaded = await client.get(
                f"/api/v1/notes/{identity}/_files/scan", headers=_authorization()
            )

        assert created.status_code == 201
        assert fetched.status_code == 200
        assert fetched.json()["scan"] == {
            "identity": guid,
            "filename": "confirmation.pdf",
            "size": len(PDF),
            "content_type": "application/pdf",
        }
        assert downloaded.status_code == 200
        assert downloaded.content == PDF
        assert downloaded.headers["content-type"] == "application/pdf"
        assert downloaded.headers["content-length"] == str(len(PDF))
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert "confirmation.pdf" in downloaded.headers["content-disposition"]
        assert downloaded.headers["content-disposition"].startswith("attachment")
        assert attachments.rows.get(guid).record_id == str(identity)

    asyncio.run(exercise())


def test_every_page_names_files_the_way_the_record_does(model: Any) -> None:
    """A grid has to be able to show a filename.

    Both page shapes resolve their own projections; the first version of
    this only did it on one of them, and the other answered every file
    field as empty -- which reads as "no document" rather than as a bug.
    """

    app, _ = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            staged = await client.post(
                "/api/v1/notes/_files/scan",
                headers=_upload_headers(),
                content=PDF,
            )
            await client.post(
                "/api/v1/notes",
                headers=_authorization(),
                json={"title": "a note", "scan": staged.json()["identity"]},
            )
            listed = await client.get("/api/v1/notes", headers=_authorization())
            queried = await client.post(
                "/api/v1/notes/_query", headers=_authorization(), json={}
            )

        for page in (listed, queried):
            assert page.status_code == 200
            assert page.json()["records"][0]["scan"] == {
                "identity": staged.json()["identity"],
                "filename": "confirmation.pdf",
                "size": len(PDF),
                "content_type": "application/pdf",
            }

    asyncio.run(exercise())


def test_a_record_cannot_claim_a_file_another_record_holds(model: Any) -> None:
    app, _ = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            staged = await client.post(
                "/api/v1/notes/_files/scan",
                headers=_upload_headers(),
                content=PDF,
            )
            guid = staged.json()["identity"]
            await client.post(
                "/api/v1/notes",
                headers=_authorization(),
                json={"title": "first", "scan": guid},
            )
            second = await client.post(
                "/api/v1/notes",
                headers=_authorization(),
                json={"title": "second", "scan": guid},
            )

        assert second.status_code == 422
        assert second.json()["issues"][0]["fields"] == ["scan"]

    asyncio.run(exercise())


def test_downloading_answers_the_way_reading_the_record_answers(model: Any) -> None:
    """No new way to learn a record exists.

    The download reads the record first, so a record this identity may not
    see and a record that is not there answer exactly as they do on the
    record route -- the file route adds no oracle of its own.
    """

    app, _ = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            created = await client.post(
                "/api/v1/notes",
                headers=_authorization(),
                json={"title": "no file yet"},
            )
            identity = created.json()["id"]
            empty = await client.get(
                f"/api/v1/notes/{identity}/_files/scan", headers=_authorization()
            )
            missing = await client.get(
                "/api/v1/notes/4242/_files/scan", headers=_authorization()
            )
            anonymous = await client.get(f"/api/v1/notes/{identity}/_files/scan")
            protected = await client.get(
                f"/api/v1/notes/{identity}/_files/secret_file",
                headers=_authorization(),
            )

        assert empty.status_code == 404
        assert missing.status_code == 404
        assert anonymous.status_code == 401
        assert protected.status_code == 403

    asyncio.run(exercise())


def test_the_openapi_document_describes_both_directions(model: Any) -> None:
    """A file reads as what it is and writes as the key that names it."""

    app, _ = _app(model)
    schema = app.openapi()

    record = schema["components"]["schemas"]["DocsNoteRecord"]
    assert record["properties"]["scan"]["anyOf"][0]["$ref"].endswith(
        "TideAttachmentValue"
    )
    attachment = schema["components"]["schemas"]["TideAttachmentValue"]
    assert set(attachment["required"]) == {
        "identity",
        "filename",
        "size",
        "content_type",
    }
    create = schema["components"]["schemas"]["DocsNoteCreateInput"]
    assert create["properties"]["scan"]["anyOf"][0]["type"] == "string"
    assert create["properties"]["scan"]["anyOf"][0]["maxLength"] == 36
    assert "/api/v1/notes/_files/{field_name}" in schema["paths"]
    assert "/api/v1/notes/{id}/_files/{field_name}" in schema["paths"]


def test_the_form_says_what_a_picker_may_offer(model: Any) -> None:
    app, _ = _app(model)

    async def exercise() -> None:
        async with _client(app) as client:
            manifest = await client.get(
                "/api/v1/_tide/presentation", headers=_authorization()
            )

        assert manifest.status_code == 200
        scan = manifest.json()["forms"]["docs.Note.form"]["fields"]["scan"]
        assert scan["field_type"] == "file"
        assert scan["accept"] == ["pdf"]
        assert scan["max_size_bytes"] == 1024
        assert scan["upload_path"] == "/api/v1/notes/_files/scan"

    asyncio.run(exercise())
