"""What an attachment store promises, proved against every implementation.

Two stores stand behind one field: rows saying what a file is, bytes saying
what it contains. They are separate because they fail separately -- a
database transaction cannot roll back a write to a disk -- and the service
above them is built around exactly that, so the promises each makes on its
own are what the recovery ordering rests on.

Both are parametrized over their implementations, the way the repository
conformance suite is: the in-memory pair is what tests and `--demo` run on,
and a promise proved against only one of them is a promise about that one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any

import pytest

from tide.data.sqlalchemy_attachments import SQLAlchemyAttachmentRows
from tide.services.attachment_store import (
    AttachmentRecord,
    AttachmentStoreError,
    AttachmentTooLarge,
    InMemoryAttachmentBytes,
    InMemoryAttachmentRows,
)

GUID = "ab3f9c72-5b84-4a11-9d0e-6c2f8a7b4e35"
OTHER = "cd7e1a04-2f66-4b90-8c31-5d9a0e6b7f12"
MOMENT = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _record(guid: str = GUID, **overrides: Any) -> AttachmentRecord:
    values: dict[str, Any] = {
        "guid": guid,
        "entity": "docs.Note",
        "field": "scan",
        "record_id": None,
        "filename": "confirmation.pdf",
        "extension": "pdf",
        "content_type": "application/pdf",
        "size": 3,
        "sha256": hashlib.sha256(b"abc").hexdigest(),
        "principal": "local:clerk",
        "uploaded_at": MOMENT,
        "unclaimed_at": MOMENT,
    }
    values.update(overrides)
    return AttachmentRecord(**values)


@pytest.fixture(params=["memory", "sqlalchemy"])
def rows(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    if request.param == "memory":
        yield InMemoryAttachmentRows()
        return
    database = tmp_path / "attachments.sqlite"
    store = SQLAlchemyAttachmentRows(
        f"sqlite+pysqlite:///{database.as_posix()}", mode="managed"
    )
    store.create_schema()
    try:
        yield store
    finally:
        store.dispose()


@pytest.fixture(params=["memory"])
def blobs(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    return InMemoryAttachmentBytes()


def test_a_staged_row_is_read_back_as_it_was_written(rows: Any) -> None:
    rows.insert(_record())

    stored = rows.get(GUID)

    assert stored == _record()
    assert stored.record_id is None
    assert rows.get(OTHER) is None


def test_claiming_stamps_the_record_and_clears_the_reclaim_clock(rows: Any) -> None:
    rows.insert(_record())

    rows.claim(GUID, "7")

    stored = rows.get(GUID)
    assert stored.record_id == "7"
    assert stored.unclaimed_at is None


def test_a_file_already_claimed_cannot_be_claimed_again(rows: Any) -> None:
    """The invariant the whole design rests on: one file, one record.

    Two records referencing one attachment would make deleting either an
    unanswerable question, and a staged upload claimed twice is how a
    second record gets a document somebody else uploaded.
    """

    rows.insert(_record())
    rows.claim(GUID, "7")

    with pytest.raises(AttachmentStoreError):
        rows.claim(GUID, "8")

    assert rows.get(GUID).record_id == "7"


def test_claiming_what_was_never_staged_is_refused(rows: Any) -> None:
    with pytest.raises(AttachmentStoreError):
        rows.claim(GUID, "7")


def test_unclaiming_starts_the_reclaim_clock(rows: Any) -> None:
    rows.insert(_record())
    rows.claim(GUID, "7")

    rows.unclaim(GUID, at=MOMENT)

    stored = rows.get(GUID)
    assert stored.record_id is None
    assert stored.unclaimed_at == MOMENT


def test_a_deleted_record_releases_every_file_it_held(rows: Any) -> None:
    rows.insert(_record(GUID))
    rows.insert(_record(OTHER, field="warranty"))
    rows.claim(GUID, "7")
    rows.claim(OTHER, "7")
    rows.insert(_record("9f1e2d3c-4b5a-4968-8770-615243342526", record_id=None))

    released = rows.unclaim_all("docs.Note", "7", at=MOMENT)

    assert set(released) == {GUID, OTHER}
    assert rows.get(GUID).record_id is None
    assert rows.get(OTHER).unclaimed_at == MOMENT


def test_only_what_has_been_unclaimed_long_enough_is_offered_for_reclaim(
    rows: Any,
) -> None:
    """A staged upload is somebody's open form until the grace runs out."""

    rows.insert(_record(GUID))
    rows.insert(_record(OTHER))
    rows.claim(OTHER, "7")

    fresh = rows.unclaimed_before(MOMENT)
    stale = rows.unclaimed_before(MOMENT + timedelta(hours=25))

    assert [record.guid for record in fresh] == []
    assert [record.guid for record in stale] == [GUID]


def test_deleting_a_row_forgets_it(rows: Any) -> None:
    rows.insert(_record())

    rows.delete(GUID)

    assert rows.get(GUID) is None
    assert rows.all_records() == ()


def test_bytes_report_what_they_stored(blobs: Any) -> None:
    size, digest = blobs.write(GUID, [b"ab", b"c"], limit=10)

    assert (size, digest) == (3, hashlib.sha256(b"abc").hexdigest())
    assert blobs.exists(GUID)
    with blobs.open(GUID) as handle:
        assert handle.read() == b"abc"


def test_a_write_past_the_limit_is_refused_and_leaves_nothing(blobs: Any) -> None:
    """The bound is enforced while receiving, not after.

    A file is refused because of what it is on the way in; storing it first
    and measuring afterwards would mean the limit only decides what to
    delete, having already spent the disk.
    """

    with pytest.raises(AttachmentTooLarge):
        blobs.write(GUID, [b"x" * 8, b"y" * 8], limit=10)

    assert not blobs.exists(GUID)
    assert blobs.all_guids() == ()


def test_reading_bytes_that_are_not_there_is_an_error(blobs: Any) -> None:
    with pytest.raises(AttachmentStoreError):
        blobs.open(GUID)


def test_deleting_bytes_is_forgiving_of_what_is_already_gone(blobs: Any) -> None:
    """A sweep runs after a crash, where half a deletion is ordinary."""

    blobs.write(GUID, [b"abc"], limit=10)

    blobs.delete(GUID)
    blobs.delete(GUID)

    assert not blobs.exists(GUID)


def test_bytes_can_list_what_they_hold(blobs: Any) -> None:
    blobs.write(GUID, [b"abc"], limit=10)
    blobs.write(OTHER, [b"de"], limit=10)

    assert set(blobs.all_guids()) == {GUID, OTHER}
