"""Uploading a file, and the record that claims it.

An upload cannot wait for the record: a create has no record yet. So a file
is staged first and claimed at commit, and everything interesting is in that
gap -- who may claim what, what happens to the file a save replaced, and
which of those a workflow lock still permits.

The rules are proved through `RecordsService` rather than against the
attachment service alone, because the claim is part of committing a record.
A test that called `claim` directly would prove the service can claim and
say nothing about whether saving a record does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from tide import compile_project
from tide.data import InMemoryRepository
from tide.runtime import Principal, RequestContext
from tide.runtime.errors import ImmutableFieldError, ValidationFailed
from tide.services import MutationSource, RecordsService
from tide.services.attachment_store import (
    AttachmentTooLarge,
    InMemoryAttachmentBytes,
    InMemoryAttachmentRows,
)
from tide.services.attachments import AttachmentService

HANDLERS = "def run(record, context, payload):\n    return record\n"

ENTITY = [
    "entity: docs.Note",
    "display: title",
    "permissions: {list: docs.note.read, read: docs.note.read, "
    "create: docs.note.write, update: docs.note.write, delete: docs.note.write}",
    "fields:",
    "  id: {type: integer, primary_key: true}",
    "  title: {type: string, length: 40}",
    "  status:",
    "    type: choice",
    "    choices: [draft, posted]",
    "    default: draft",
    "    readonly: true",
    "    write: action_only",
    "  scan: {type: file, max_size: 1kb, accept: [pdf], audit: values}",
    "  warranty: {type: file, max_size: 1kb}",
    "actions:",
    "  post:",
    "    label: Post",
    "    unrestricted: true",
    "    execute: handlers.run",
    "    transition:",
    "      field: status",
    "      from: draft",
    "      to: posted",
    "      locks_record: true",
]


@pytest.fixture
def model(tmp_path_factory: pytest.TempPathFactory) -> Any:
    project = tmp_path_factory.mktemp("attachments") / "notes"
    (project / "models").mkdir(parents=True)
    (project / "security").mkdir()
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Notes, version: 0.1.0}",
                "model: {paths: [models]}",
                "security: {paths: [security]}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "security" / "policies.yaml").write_text(
        "permissions: [docs.note.read, docs.note.write]\n"
        "roles:\n  clerk:\n    grants: [docs.note.read, docs.note.write]\n",
        encoding="utf-8",
    )
    (project / "handlers.py").write_text(HANDLERS, encoding="utf-8")
    (project / "models" / "note.yaml").write_text(
        "\n".join(ENTITY) + "\n", encoding="utf-8"
    )
    return compile_project(project)


@pytest.fixture
def runtime(model: Any) -> tuple[RecordsService, AttachmentService]:
    attachments = AttachmentService(
        model, InMemoryAttachmentRows(), InMemoryAttachmentBytes()
    )
    records = RecordsService(model, InMemoryRepository(), attachments=attachments)
    return records, attachments


def _context(identifier: str = "local:clerk") -> RequestContext:
    return RequestContext(principal=Principal(identifier, roles=frozenset({"clerk"})))


def _stage(
    attachments: AttachmentService,
    *,
    field: str = "scan",
    filename: str = "confirmation.pdf",
    principal: str = "local:clerk",
    payload: bytes = b"%PDF-1.4 signed",
) -> str:
    record = attachments.stage(
        "docs.Note",
        field,
        filename=filename,
        content_type="application/pdf",
        chunks=[payload],
        principal=principal,
    )
    return record.guid


def _saved_note(
    records: RecordsService,
    context: RequestContext,
    **values: Any,
) -> dict[str, Any]:
    session = records.create("docs.Note", context)
    session.values.update({"title": "a note", **values})
    return records.commit(session, context)


def test_staging_records_what_the_file_is(runtime: Any) -> None:
    _, attachments = runtime

    record = attachments.stage(
        "docs.Note",
        "scan",
        filename="confirmation.pdf",
        content_type="application/pdf",
        chunks=[b"%PDF-1.4 signed"],
        principal="local:clerk",
    )

    assert record.entity == "docs.Note"
    assert record.field == "scan"
    assert record.record_id is None
    assert record.filename == "confirmation.pdf"
    assert record.extension == "pdf"
    assert record.size == len(b"%PDF-1.4 signed")
    assert attachments.projection(record) == {
        "identity": record.guid,
        "filename": "confirmation.pdf",
        "size": len(b"%PDF-1.4 signed"),
        "content_type": "application/pdf",
    }


def test_staging_refuses_a_kind_the_field_does_not_accept(runtime: Any) -> None:
    """`accept` is what the picker offers and what the server insists on."""

    _, attachments = runtime

    with pytest.raises(ValidationFailed) as caught:
        attachments.stage(
            "docs.Note",
            "scan",
            filename="notes.txt",
            content_type="text/plain",
            chunks=[b"plain"],
            principal="local:clerk",
        )

    assert [issue.fields for issue in caught.value.issues] == [("scan",)]


def test_staging_refuses_more_than_the_field_declared(runtime: Any) -> None:
    _, attachments = runtime

    with pytest.raises(AttachmentTooLarge):
        attachments.stage(
            "docs.Note",
            "scan",
            filename="huge.pdf",
            content_type="application/pdf",
            chunks=[b"x" * 2048],
            principal="local:clerk",
        )

    assert attachments.rows.all_records() == ()
    assert attachments.bytes.all_guids() == ()


def test_staging_refuses_a_field_that_holds_no_file(runtime: Any) -> None:
    _, attachments = runtime

    with pytest.raises(ValueError):
        attachments.stage(
            "docs.Note",
            "title",
            filename="confirmation.pdf",
            content_type="application/pdf",
            chunks=[b"%PDF"],
            principal="local:clerk",
        )


def test_committing_a_record_claims_the_file_it_names(runtime: Any) -> None:
    records, attachments = runtime
    context = _context()
    guid = _stage(attachments)

    stored = _saved_note(records, context, scan=guid)

    assert stored["scan"] == guid
    assert attachments.rows.get(guid).record_id == str(stored["id"])


def test_a_file_another_record_already_holds_cannot_be_claimed(runtime: Any) -> None:
    """The invariant, reached the way it would really be reached.

    Nothing stops a second record from naming the first record's key; what
    stops it is the commit, and it has to be refused as this field's problem
    rather than as a server fault.
    """

    records, attachments = runtime
    context = _context()
    guid = _stage(attachments)
    _saved_note(records, context, scan=guid)

    with pytest.raises(ValidationFailed) as caught:
        _saved_note(records, context, scan=guid)

    assert [issue.fields for issue in caught.value.issues] == [("scan",)]


def test_a_file_someone_else_staged_cannot_be_claimed(runtime: Any) -> None:
    """A staged key is a bearer token until it is claimed.

    Guessing one is not the threat -- it is 122 bits of randomness -- but
    an identity that has *seen* one, from a shared screen or a log, must
    not be able to attach somebody else's document to its own record.
    """

    records, attachments = runtime
    guid = _stage(attachments, principal="local:someone-else")

    with pytest.raises(ValidationFailed) as caught:
        _saved_note(records, _context("local:clerk"), scan=guid)

    assert [issue.fields for issue in caught.value.issues] == [("scan",)]
    assert attachments.rows.get(guid).record_id is None


def test_a_key_naming_no_upload_is_refused(runtime: Any) -> None:
    records, _ = runtime

    with pytest.raises(ValidationFailed) as caught:
        _saved_note(records, _context(), scan="ab3f9c72-5b84-4a11-9d0e-6c2f8a7b4e35")

    assert [issue.fields for issue in caught.value.issues] == [("scan",)]


def test_a_file_staged_for_another_field_is_refused(runtime: Any) -> None:
    records, attachments = runtime
    guid = _stage(attachments, field="warranty", filename="warranty.pdf")

    with pytest.raises(ValidationFailed) as caught:
        _saved_note(records, _context(), scan=guid)

    assert [issue.fields for issue in caught.value.issues] == [("scan",)]


def test_saving_a_record_again_keeps_the_file_it_already_held(runtime: Any) -> None:
    """An unchanged value is not a new claim."""

    records, attachments = runtime
    context = _context()
    guid = _stage(attachments)
    stored = _saved_note(records, context, scan=guid)

    session = records.begin_edit("docs.Note", stored["id"], context)
    session.values["title"] = "renamed"
    records.commit(session, context)

    assert attachments.rows.get(guid).record_id == str(stored["id"])


def test_replacing_a_file_releases_the_one_it_replaced(runtime: Any) -> None:
    """The better scan arrives; the first is let go but not destroyed.

    Released rather than deleted because a download may still be streaming
    it, and because a save that fails after this point must not have taken
    anything with it.
    """

    records, attachments = runtime
    context = _context()
    first = _stage(attachments)
    stored = _saved_note(records, context, scan=first)
    second = _stage(attachments, filename="better-scan.pdf")

    session = records.begin_edit("docs.Note", stored["id"], context)
    session.values["scan"] = second
    records.commit(session, context)

    assert attachments.rows.get(second).record_id == str(stored["id"])
    assert attachments.rows.get(first).record_id is None
    assert attachments.rows.get(first).unclaimed_at is not None
    assert attachments.bytes.exists(first)


def test_deleting_a_file_from_a_record_releases_it(runtime: Any) -> None:
    records, attachments = runtime
    context = _context()
    guid = _stage(attachments)
    stored = _saved_note(records, context, scan=guid)

    session = records.begin_edit("docs.Note", stored["id"], context)
    session.values["scan"] = None
    records.commit(session, context)

    assert attachments.rows.get(guid).record_id is None


def test_a_lock_freezes_a_file_field_only_once_it_holds_a_file(runtime: Any) -> None:
    """The countersigned document arrives after the record is posted.

    A posted record refuses every ordinary write, and this is the one
    carve-out: an empty file field may still be filled, because that is the
    real order of events. What it may not do afterwards is change its mind.
    """

    records, attachments = runtime
    context = _context()
    stored = _saved_note(records, context)
    posted = records.begin_action("docs.Note", stored["id"], context)
    posted.values["status"] = "posted"
    records.commit(posted, context, source=MutationSource.ACTION)

    session = records.begin_edit("docs.Note", stored["id"], context)
    session.values["scan"] = _stage(attachments)
    records.commit(session, context)

    locked = records.begin_edit("docs.Note", stored["id"], context)
    locked.values["scan"] = _stage(attachments, filename="another.pdf")
    with pytest.raises(ImmutableFieldError):
        records.commit(locked, context)

    cleared = records.begin_edit("docs.Note", stored["id"], context)
    cleared.values["scan"] = None
    with pytest.raises(ImmutableFieldError):
        records.commit(cleared, context)

    renamed = records.begin_edit("docs.Note", stored["id"], context)
    renamed.values["title"] = "still locked"
    with pytest.raises(ImmutableFieldError):
        records.commit(renamed, context)


def test_deleting_a_record_releases_every_file_it_held(runtime: Any) -> None:
    records, attachments = runtime
    context = _context()
    scan = _stage(attachments)
    warranty = _stage(attachments, field="warranty", filename="warranty.pdf")
    stored = _saved_note(records, context, scan=scan, warranty=warranty)

    records.delete("docs.Note", stored["id"], context)

    assert attachments.rows.get(scan).record_id is None
    assert attachments.rows.get(warranty).record_id is None


def test_the_sweep_reclaims_only_what_has_waited_out_its_grace(runtime: Any) -> None:
    """An abandoned draft's upload is somebody's open form for a while."""

    records, attachments = runtime
    guid = _stage(attachments)

    kept = attachments.sweep(grace=timedelta(hours=24))
    assert kept == ()
    assert attachments.bytes.exists(guid)

    attachments.clock = lambda: datetime.now(timezone.utc) + timedelta(hours=25)
    swept = attachments.sweep(grace=timedelta(hours=24))

    assert [record.guid for record in swept] == [guid]
    assert not attachments.bytes.exists(guid)
    assert attachments.rows.get(guid) is None


def test_check_sees_each_way_the_two_stores_can_disagree(runtime: Any) -> None:
    """Rows and bytes fail apart, so the reconciliation asks both ways."""

    records, attachments = runtime
    context = _context()
    claimed = _stage(attachments)
    _saved_note(records, context, scan=claimed)
    orphan = _stage(attachments, filename="orphan.pdf")

    assert attachments.check().is_clean

    attachments.bytes.delete(claimed)
    attachments.rows.delete(orphan)
    attachments.bytes.write(
        "ffffffff-ffff-4fff-8fff-ffffffffffff", [b"stray"], limit=100
    )

    report = attachments.check()

    assert [record.guid for record in report.rows_without_bytes] == [claimed]
    assert set(report.bytes_without_rows) == {
        orphan,
        "ffffffff-ffff-4fff-8fff-ffffffffffff",
    }
    assert not report.is_clean


def test_check_notices_bytes_that_changed_behind_the_row(runtime: Any) -> None:
    records, attachments = runtime
    context = _context()
    guid = _stage(attachments)
    _saved_note(records, context, scan=guid)

    attachments.bytes.delete(guid)
    attachments.bytes.write(guid, [b"something else entirely"], limit=100)

    report = attachments.check()

    assert [record.guid for record in report.digest_mismatches] == [guid]


def test_a_download_answers_the_file_and_what_it_is(runtime: Any) -> None:
    records, attachments = runtime
    context = _context()
    guid = _stage(attachments)
    _saved_note(records, context, scan=guid)

    record, stream = attachments.open_download(guid)
    with stream:
        assert stream.read() == b"%PDF-1.4 signed"
    assert record.filename == "confirmation.pdf"


def test_projections_answer_only_for_the_keys_asked_about(runtime: Any) -> None:
    _, attachments = runtime
    guid = _stage(attachments)

    projections = attachments.projections_for([guid, "not-a-key", None])

    assert set(projections) == {guid}
    assert projections[guid]["filename"] == "confirmation.pdf"
