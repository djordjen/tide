"""Uploading a file, and the rules a record must satisfy to claim it.

An upload cannot wait for the record it belongs to: creating one has no
record yet, and a form the person may still cancel is not a place to write
business data. So a file is staged -- bytes stored, row written, nothing
referring to it -- and the record claims it at commit.

Everything this service exists to decide lives in that gap. A staged key is
effectively a bearer token until it is claimed, so claiming asks four
questions: does the key name an upload, is it still unclaimed, was it staged
for *this* entity and field, and was it staged by the identity now committing.
Guessing a key is not the threat (it is 122 bits); an identity that has seen
one -- over a shoulder, in a log, in a shared browser -- is.

The service owns no permissions of its own. Whether this principal may write
the field at all was decided before anything was staged, and whether the
record may be saved is decided by `RecordsService` around the claim.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from tide.compiler.normalized import ApplicationModel, NormalizedField
from tide.model.source import parse_size_literal
from tide.runtime.errors import ValidationFailed, ValidationIssue
from tide.services.attachment_store import (
    AttachmentBytes,
    AttachmentRecord,
    AttachmentRows,
)

DEFAULT_GRACE = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class AttachmentCheckReport:
    """Every way the two stores can disagree, counted separately.

    Three directions rather than one number, because they mean different
    things to an operator: a row without bytes is a document somebody will
    ask for and not receive, bytes without a row are only disk, and a
    digest that moved is the one that says something is wrong beyond TIDE.
    """

    rows_without_bytes: tuple[AttachmentRecord, ...] = ()
    bytes_without_rows: tuple[str, ...] = ()
    digest_mismatches: tuple[AttachmentRecord, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not (
            self.rows_without_bytes or self.bytes_without_rows or self.digest_mismatches
        )


class AttachmentService:
    """Stage uploads, decide claims, and reconcile the two stores."""

    def __init__(
        self,
        model: ApplicationModel,
        rows: AttachmentRows,
        bytes_store: AttachmentBytes,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.model = model
        self.rows = rows
        self.bytes = bytes_store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def stage(
        self,
        entity_name: str,
        field_name: str,
        *,
        filename: str,
        content_type: str,
        chunks: Iterable[bytes],
        principal: str,
    ) -> AttachmentRecord:
        """Store an uploaded file that no record refers to yet."""

        field = self.file_field(entity_name, field_name)
        name = _display_filename(filename)
        if not name:
            raise ValidationFailed(
                [
                    ValidationIssue(
                        "attachment", "an uploaded file must have a name", (field_name,)
                    )
                ]
            )
        extension = _extension(name)
        accept = tuple(str(value) for value in field.metadata.get("accept", ()))
        if accept and extension not in accept:
            raise ValidationFailed(
                [
                    ValidationIssue(
                        "attachment",
                        f"{name} is not one of the kinds this field accepts: "
                        f"{', '.join(accept)}",
                        (field_name,),
                    )
                ]
            )
        limit = parse_size_literal(str(field.metadata["max_size"]))
        guid = str(uuid4())
        size, digest = self.bytes.write(guid, chunks, limit=limit)
        moment = self.clock()
        record = AttachmentRecord(
            guid=guid,
            entity=entity_name,
            field=field_name,
            record_id=None,
            filename=name,
            extension=extension,
            content_type=content_type or "application/octet-stream",
            size=size,
            sha256=digest,
            principal=principal,
            uploaded_at=moment,
            unclaimed_at=moment,
        )
        try:
            self.rows.insert(record)
        except BaseException:
            # Bytes without a row are invisible to everything above and would
            # only ever be found by a sweep; the row is what makes a file a
            # file, so failing to write one un-does the upload here.
            self.bytes.delete(guid)
            raise
        return record

    def file_field(self, entity_name: str, field_name: str) -> NormalizedField:
        entity = self.model.entity(entity_name)
        if field_name not in entity.fields:
            raise ValueError(f"{entity_name} has no field {field_name!r}")
        field = entity.field(field_name)
        if field.metadata["type"] != "file":
            raise ValueError(f"{entity_name}.{field_name} does not hold a file")
        return field

    def projection(self, record: AttachmentRecord) -> dict[str, Any]:
        """What a record says about its file: enough to name it, never a path."""

        return {
            "identity": record.guid,
            "filename": record.filename,
            "size": record.size,
            "content_type": record.content_type,
        }

    def projections_for(
        self, guids: Iterable[str | None]
    ) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for guid in guids:
            if not isinstance(guid, str) or guid in found:
                continue
            record = self.rows.get(guid)
            if record is not None:
                found[guid] = self.projection(record)
        return found

    def projections_for_records(
        self,
        entity: Any,
        rows: Iterable[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Resolve every file a page of records names, in one pass.

        Walks into collections as well: a record's children carry their own
        file fields, and the wire projects the whole tree from one map.
        """

        guids: list[str] = []

        def walk(current: Any, row: Mapping[str, Any]) -> None:
            for name, field in current.fields.items():
                kind = field.metadata["type"]
                if kind == "file":
                    value = row.get(name)
                    if isinstance(value, str):
                        guids.append(value)
                elif kind == "collection" and field.target_entity:
                    target = self.model.entity(field.target_entity)
                    for child in row.get(name) or ():
                        if isinstance(child, Mapping):
                            walk(target, child)

        for row in rows:
            walk(entity, row)
        return self.projections_for(guids)

    def open_download(self, guid: str) -> tuple[AttachmentRecord, BinaryIO]:
        record = self.rows.get(guid)
        if record is None:
            raise ValueError(f"no attachment named {guid!r}")
        return record, self.bytes.open(guid)

    def claim_issues(
        self,
        entity_name: str,
        field_name: str,
        guid: str,
        *,
        principal: str,
    ) -> list[ValidationIssue]:
        """Whether this identity may attach this upload to this field.

        Answered as validation issues rather than as an authorization error:
        from where the person is standing, a key that cannot be claimed is
        something wrong with the file they picked, not a locked door.
        """

        record = self.rows.get(guid)
        if record is None:
            return [
                ValidationIssue(
                    "attachment", "names no uploaded file", (field_name,)
                )
            ]
        if record.record_id is not None:
            return [
                ValidationIssue(
                    "attachment",
                    "that file already belongs to another record",
                    (field_name,),
                )
            ]
        if record.entity != entity_name or record.field != field_name:
            return [
                ValidationIssue(
                    "attachment",
                    "that file was uploaded for a different field",
                    (field_name,),
                )
            ]
        if record.principal != principal:
            return [
                ValidationIssue(
                    "attachment",
                    "that file was uploaded by someone else",
                    (field_name,),
                )
            ]
        return []

    def claim(self, guid: str, record_id: str) -> None:
        self.rows.claim(guid, record_id)

    def release(self, guids: Iterable[str]) -> None:
        moment = self.clock()
        for guid in guids:
            self.rows.unclaim(guid, at=moment)

    def release_record(self, entity_name: str, record_id: str) -> tuple[str, ...]:
        return self.rows.unclaim_all(entity_name, record_id, at=self.clock())

    def sweep(self, *, grace: timedelta = DEFAULT_GRACE) -> tuple[AttachmentRecord, ...]:
        """Forget files nothing has referred to for longer than the grace.

        Bytes go after the row, not before: a row without bytes is a
        document somebody will ask for and not receive, while bytes without
        a row are disk this will reclaim on its next run.
        """

        reclaimable = self.rows.unclaimed_before(self.clock() - grace)
        for record in reclaimable:
            self.rows.delete(record.guid)
            self.bytes.delete(record.guid)
        return reclaimable

    def check(self) -> AttachmentCheckReport:
        records = self.rows.all_records()
        stored = set(self.bytes.all_guids())
        missing: list[AttachmentRecord] = []
        mismatched: list[AttachmentRecord] = []
        for record in records:
            if record.guid not in stored:
                missing.append(record)
                continue
            if _digest(self.bytes.open(record.guid)) != record.sha256:
                mismatched.append(record)
        known = {record.guid for record in records}
        return AttachmentCheckReport(
            rows_without_bytes=tuple(missing),
            bytes_without_rows=tuple(sorted(stored - known)),
            digest_mismatches=tuple(mismatched),
        )


def file_fields(entity: Any) -> tuple[str, ...]:
    """The names of an entity's file fields, in declaration order."""

    return tuple(
        name
        for name, field in entity.fields.items()
        if field.metadata["type"] == "file"
    )


def claim_plan(
    entity: Any,
    values: Mapping[str, Any],
    original: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Which file fields changed, as `(field, guid)` pairs to claim.

    An unchanged value is not a new claim: saving a record twice must not
    ask the store to claim what this record already holds.
    """

    changed: list[tuple[str, str]] = []
    for name in file_fields(entity):
        new = values.get(name)
        if new is None or new == original.get(name):
            continue
        changed.append((name, str(new)))
    return tuple(changed)


def released_guids(
    entity: Any,
    values: Mapping[str, Any],
    original: Mapping[str, Any],
) -> tuple[str, ...]:
    """The files this save let go of: replaced, or cleared."""

    return tuple(
        str(original[name])
        for name in file_fields(entity)
        if original.get(name) and values.get(name) != original.get(name)
    )


def _display_filename(filename: str) -> str:
    """The name a person sees, with anything that looks like a path removed.

    Never used to build a path -- what is on disk is the key -- but a name
    carrying `..` or a drive letter would still be repeated back in a
    download header and read by something else as a location.
    """

    name = PureWindowsPath(PurePosixPath(str(filename)).name).name.strip()
    return "" if name in {".", ".."} else name[:255]


def _extension(filename: str) -> str:
    suffix = PurePosixPath(filename).suffix
    return suffix[1:].lower() if suffix else ""


def _digest(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    with stream:
        while True:
            chunk = stream.read(65_536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


__all__: Sequence[str] = [
    "AttachmentCheckReport",
    "AttachmentService",
    "claim_plan",
    "file_fields",
    "released_guids",
]
