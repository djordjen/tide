"""What an attachment is, and the two stores that hold it.

A file field stores a key. What that key names lives in two places that fail
independently: a row saying what the file is -- its name, type, size, digest,
who uploaded it and which record claimed it -- and the bytes themselves,
which are never in a database. Nothing can make a write to a filesystem part
of a database transaction, so rather than pretend, the split is explicit and
the service above orders its writes around it.

`record_id` is the whole lifecycle. A freshly uploaded row is *staged*: it
exists, it has bytes, and no record refers to it. Committing a record that
names it *claims* it. Replacing or clearing the field, or deleting the
record, *unclaims* it -- which does not delete anything, it starts a clock,
because a download may still be streaming and a crash mid-write must leave
something a sweep can reason about rather than a hole.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import io
from threading import RLock
from typing import BinaryIO, Protocol, runtime_checkable

from tide.runtime.errors import TideRuntimeError


class AttachmentStoreError(TideRuntimeError):
    code = "attachment_store_error"


class AttachmentTooLarge(TideRuntimeError):
    code = "attachment_too_large"

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"file exceeds the declared limit of {limit} bytes")


@dataclass(frozen=True, slots=True)
class AttachmentRecord:
    """One uploaded file, as the database knows it.

    The filename is display data and never a path component: what is on disk
    is the guid, so a name carrying `..` or a drive letter is a string here
    rather than a place.
    """

    guid: str
    entity: str
    field: str
    record_id: str | None
    filename: str
    extension: str
    content_type: str
    size: int
    sha256: str
    principal: str
    uploaded_at: datetime
    unclaimed_at: datetime | None

    def __post_init__(self) -> None:
        if len(self.guid) != 36:
            raise ValueError("an attachment key is 36 characters")
        if not self.entity or not self.field or not self.filename:
            raise ValueError("an attachment names its entity, field and filename")
        if self.size < 0:
            raise ValueError("an attachment size must not be negative")
        if self.uploaded_at.tzinfo is None or self.uploaded_at.utcoffset() is None:
            raise ValueError("attachment timestamps must be timezone-aware")
        if self.record_id is not None and self.unclaimed_at is not None:
            raise ValueError("a claimed attachment is not waiting to be reclaimed")


@runtime_checkable
class AttachmentRows(Protocol):
    """What the database knows about uploaded files."""

    def insert(self, record: AttachmentRecord) -> None: ...

    def get(self, guid: str) -> AttachmentRecord | None: ...

    def claim(self, guid: str, record_id: str) -> None: ...

    def unclaim(self, guid: str, *, at: datetime) -> None: ...

    def unclaim_all(
        self, entity: str, record_id: str, *, at: datetime
    ) -> tuple[str, ...]: ...

    def delete(self, guid: str) -> None: ...

    def unclaimed_before(self, moment: datetime) -> tuple[AttachmentRecord, ...]: ...

    def all_records(self) -> tuple[AttachmentRecord, ...]: ...


@runtime_checkable
class AttachmentBytes(Protocol):
    """Where the files themselves are."""

    def write(
        self, guid: str, chunks: Iterable[bytes], *, limit: int
    ) -> tuple[int, str]: ...

    def open(self, guid: str) -> BinaryIO: ...

    def delete(self, guid: str) -> None: ...

    def exists(self, guid: str) -> bool: ...

    def all_guids(self) -> tuple[str, ...]: ...


def measured(chunks: Iterable[bytes], limit: int) -> Iterator[tuple[bytes, int, str]]:
    """Yield each chunk with the running size and digest, refusing overruns.

    Shared by both byte stores so the bound and the digest are computed once,
    the same way, and while the data is passing rather than after it has all
    arrived: a file is refused for what it is on the way in.
    """

    if limit < 1:
        raise ValueError("an attachment limit must be positive")
    digest = hashlib.sha256()
    size = 0
    for chunk in chunks:
        size += len(chunk)
        if size > limit:
            raise AttachmentTooLarge(limit)
        digest.update(chunk)
        yield chunk, size, digest.hexdigest()


class InMemoryAttachmentRows:
    """Thread-safe process-local rows, for tests and `--demo`."""

    def __init__(self) -> None:
        self._records: dict[str, AttachmentRecord] = {}
        self._lock = RLock()

    def insert(self, record: AttachmentRecord) -> None:
        with self._lock:
            if record.guid in self._records:
                raise AttachmentStoreError(f"attachment {record.guid} already exists")
            self._records[record.guid] = record

    def get(self, guid: str) -> AttachmentRecord | None:
        with self._lock:
            return self._records.get(guid)

    def claim(self, guid: str, record_id: str) -> None:
        with self._lock:
            record = self._records.get(guid)
            if record is None:
                raise AttachmentStoreError(f"attachment {guid} does not exist")
            if record.record_id is not None:
                raise AttachmentStoreError(
                    f"attachment {guid} already belongs to another record"
                )
            self._records[guid] = replace(
                record, record_id=record_id, unclaimed_at=None
            )

    def unclaim(self, guid: str, *, at: datetime) -> None:
        with self._lock:
            record = self._records.get(guid)
            if record is None:
                raise AttachmentStoreError(f"attachment {guid} does not exist")
            self._records[guid] = replace(record, record_id=None, unclaimed_at=at)

    def unclaim_all(
        self, entity: str, record_id: str, *, at: datetime
    ) -> tuple[str, ...]:
        with self._lock:
            released = tuple(
                record.guid
                for record in self._records.values()
                if record.entity == entity and record.record_id == record_id
            )
            for guid in released:
                self._records[guid] = replace(
                    self._records[guid], record_id=None, unclaimed_at=at
                )
            return released

    def delete(self, guid: str) -> None:
        with self._lock:
            self._records.pop(guid, None)

    def unclaimed_before(self, moment: datetime) -> tuple[AttachmentRecord, ...]:
        with self._lock:
            return tuple(
                record
                for record in self._records.values()
                if record.unclaimed_at is not None and record.unclaimed_at < moment
            )

    def all_records(self) -> tuple[AttachmentRecord, ...]:
        with self._lock:
            return tuple(self._records.values())


class InMemoryAttachmentBytes:
    """Thread-safe process-local bytes, for tests and `--demo`."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        self._lock = RLock()

    def write(
        self, guid: str, chunks: Iterable[bytes], *, limit: int
    ) -> tuple[int, str]:
        buffer = bytearray()
        size = 0
        digest = ""
        # Accumulated outside the lock and published in one assignment: a
        # refused write must leave nothing behind, and a half-written entry
        # another thread could read is exactly that.
        for chunk, size, digest in measured(chunks, limit):
            buffer.extend(chunk)
        with self._lock:
            if guid in self._blobs:
                raise AttachmentStoreError(f"attachment {guid} already has bytes")
            self._blobs[guid] = bytes(buffer)
        return size, digest

    def open(self, guid: str) -> BinaryIO:
        with self._lock:
            blob = self._blobs.get(guid)
        if blob is None:
            raise AttachmentStoreError(f"attachment {guid} has no bytes")
        return io.BytesIO(blob)

    def delete(self, guid: str) -> None:
        with self._lock:
            self._blobs.pop(guid, None)

    def exists(self, guid: str) -> bool:
        with self._lock:
            return guid in self._blobs

    def all_guids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._blobs)
