"""Attachment rows in the application's own database.

The sibling of :mod:`tide.data.sqlalchemy_sessions`, and deliberately built
the same way: construction emits no DDL, and ``legacy`` mode validates a
table it did not create.

These rows live beside the application's data rather than beside the files
they describe. The GUID columns are in the application database either way,
so a metadata store kept elsewhere would mean a restore had to align three
units instead of two -- and the one database backup already has a verified
contract. It also keeps a SQL Server deployment from acquiring a second
engine on a disk path somebody will eventually put on a network share.

Times are stored as epoch floats, like the session store: the comparisons
this table exists for are "older than the grace period", and a stored
timestamp would mean a conversion on each one. An operator reading the table
directly can spell that ``datetime(unclaimed_at, 'unixepoch')``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Index,
    MetaData,
    String,
    Table,
    Unicode,
    delete,
    inspect,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine, RowMapping, URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tide.data.sqlalchemy import (
    SchemaCompatibilityError,
    SchemaIssue,
    SchemaManagementError,
    _create_engine,
)
from tide.services.attachment_store import AttachmentRecord, AttachmentStoreError

MAX_FILENAME_CHARACTERS = 255


class SQLAlchemyAttachmentRows:
    """What every process can see about the files this application holds.

    Construction never emits DDL. ``mode='managed'`` enables an explicit
    :meth:`create_schema`; the default ``legacy`` mode only validates and
    uses a pre-existing table.
    """

    def __init__(
        self,
        bind: str | URL | Engine,
        *,
        mode: str = "legacy",
        schema: str | None = None,
    ) -> None:
        if mode not in {"managed", "legacy"}:
            raise ValueError("attachment store mode must be 'managed' or 'legacy'")
        self.mode = mode
        self._owns_engine = not isinstance(bind, Engine)
        self.engine = bind if isinstance(bind, Engine) else _create_engine(bind)
        self.metadata = MetaData()
        self.attachment_table = Table(
            "tide_attachment",
            self.metadata,
            Column("guid", String(36), primary_key=True),
            Column("entity", String(255), nullable=False),
            Column("field", String(255), nullable=False),
            # Null while staged. The record's own key rendered as text: one
            # column has to hold an integer identity and a uuid one alike,
            # and nothing here compares it as anything but a key.
            Column("record_id", String(255), nullable=True),
            Column("filename", Unicode(MAX_FILENAME_CHARACTERS), nullable=False),
            Column("extension", String(32), nullable=False),
            Column("content_type", String(255), nullable=False),
            Column("size", BigInteger(), nullable=False),
            Column("sha256", String(64), nullable=False),
            Column("principal", String(255), nullable=False),
            Column("uploaded_at", Float(), nullable=False),
            Column("unclaimed_at", Float(), nullable=True),
            schema=schema,
        )
        Index(
            "ix_tide_attachment_claim",
            self.attachment_table.c.entity,
            self.attachment_table.c.record_id,
        )
        Index(
            "ix_tide_attachment_unclaimed",
            self.attachment_table.c.unclaimed_at,
        )

    def create_schema(self) -> None:
        if self.mode != "managed":
            raise SchemaManagementError(
                "legacy attachment-store mode forbids creating or changing "
                "schema objects"
            )
        self.metadata.create_all(self.engine)

    def schema_issues(self) -> tuple[SchemaIssue, ...]:
        inspector = inspect(self.engine)
        issues: list[SchemaIssue] = []
        table = self.attachment_table
        object_name = f"{table.schema}.{table.name}" if table.schema else table.name
        entity = "tide.attachment-store"
        if not inspector.has_table(table.name, schema=table.schema):
            return (SchemaIssue(entity, object_name, "mapped table does not exist"),)
        actual_columns = {
            str(column["name"])
            for column in inspector.get_columns(table.name, schema=table.schema)
        }
        issues.extend(
            SchemaIssue(
                entity,
                f"{object_name}.{column.name}",
                "mapped column does not exist",
            )
            for column in table.columns
            if column.name not in actual_columns
        )
        return tuple(issues)

    def validate_schema(self) -> None:
        issues = self.schema_issues()
        if issues:
            raise SchemaCompatibilityError(issues)

    def insert(self, record: AttachmentRecord) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(self.attachment_table).values(
                        guid=record.guid,
                        entity=record.entity,
                        field=record.field,
                        record_id=record.record_id,
                        filename=record.filename[:MAX_FILENAME_CHARACTERS],
                        extension=record.extension,
                        content_type=record.content_type,
                        size=record.size,
                        sha256=record.sha256,
                        principal=record.principal,
                        uploaded_at=_epoch(record.uploaded_at),
                        unclaimed_at=_epoch(record.unclaimed_at),
                    )
                )
        except IntegrityError as error:
            raise AttachmentStoreError(
                f"attachment {record.guid} already exists"
            ) from error
        except SQLAlchemyError as error:
            raise AttachmentStoreError("could not record the attachment") from error

    def get(self, guid: str) -> AttachmentRecord | None:
        try:
            with self.engine.begin() as connection:
                row = (
                    connection.execute(
                        select(self.attachment_table).where(
                            self.attachment_table.c.guid == guid
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as error:
            raise AttachmentStoreError("could not read the attachment") from error
        return None if row is None else _record(row)

    def claim(self, guid: str, record_id: str) -> None:
        """Stamp the record onto a staged row, or refuse.

        One statement, conditional on the row still being unclaimed: two
        processes committing records that name the same staged upload must
        not both win, and a read followed by a write is exactly how they
        would. The rowcount is the answer.
        """

        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(self.attachment_table)
                    .where(
                        self.attachment_table.c.guid == guid,
                        self.attachment_table.c.record_id.is_(None),
                    )
                    .values(record_id=record_id, unclaimed_at=None)
                )
                if result.rowcount == 1:
                    return
                exists = (
                    connection.execute(
                        select(self.attachment_table.c.guid).where(
                            self.attachment_table.c.guid == guid
                        )
                    ).one_or_none()
                    is not None
                )
        except SQLAlchemyError as error:
            raise AttachmentStoreError("could not claim the attachment") from error
        if exists:
            raise AttachmentStoreError(
                f"attachment {guid} already belongs to another record"
            )
        raise AttachmentStoreError(f"attachment {guid} does not exist")

    def unclaim(self, guid: str, *, at: datetime) -> None:
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(self.attachment_table)
                    .where(self.attachment_table.c.guid == guid)
                    .values(record_id=None, unclaimed_at=_epoch(at))
                )
        except SQLAlchemyError as error:
            raise AttachmentStoreError("could not release the attachment") from error
        if result.rowcount != 1:
            raise AttachmentStoreError(f"attachment {guid} does not exist")

    def unclaim_all(
        self, entity: str, record_id: str, *, at: datetime
    ) -> tuple[str, ...]:
        try:
            with self.engine.begin() as connection:
                held = tuple(
                    str(row[0])
                    for row in connection.execute(
                        select(self.attachment_table.c.guid).where(
                            self.attachment_table.c.entity == entity,
                            self.attachment_table.c.record_id == record_id,
                        )
                    )
                )
                if held:
                    connection.execute(
                        update(self.attachment_table)
                        .where(self.attachment_table.c.guid.in_(held))
                        .values(record_id=None, unclaimed_at=_epoch(at))
                    )
        except SQLAlchemyError as error:
            raise AttachmentStoreError(
                "could not release the record's attachments"
            ) from error
        return held

    def delete(self, guid: str) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.attachment_table).where(
                        self.attachment_table.c.guid == guid
                    )
                )
        except SQLAlchemyError as error:
            raise AttachmentStoreError("could not forget the attachment") from error

    def unclaimed_before(self, moment: datetime) -> tuple[AttachmentRecord, ...]:
        try:
            with self.engine.begin() as connection:
                rows = (
                    connection.execute(
                        select(self.attachment_table).where(
                            self.attachment_table.c.unclaimed_at.is_not(None),
                            self.attachment_table.c.unclaimed_at < _epoch(moment),
                        )
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as error:
            raise AttachmentStoreError(
                "could not list reclaimable attachments"
            ) from error
        return tuple(_record(row) for row in rows)

    def all_records(self) -> tuple[AttachmentRecord, ...]:
        try:
            with self.engine.begin() as connection:
                rows = (
                    connection.execute(select(self.attachment_table)).mappings().all()
                )
        except SQLAlchemyError as error:
            raise AttachmentStoreError("could not list attachments") from error
        return tuple(_record(row) for row in rows)

    def dispose(self) -> None:
        if self._owns_engine:
            self.engine.dispose()


def _epoch(moment: datetime | None) -> float | None:
    return None if moment is None else moment.timestamp()


def _moment(value: float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(float(value), timezone.utc)


def _record(row: RowMapping) -> AttachmentRecord:
    uploaded_at = _moment(row["uploaded_at"])
    if uploaded_at is None:
        raise AttachmentStoreError("stored attachment has no upload time")
    record_id = row["record_id"]
    return AttachmentRecord(
        guid=str(row["guid"]),
        entity=str(row["entity"]),
        field=str(row["field"]),
        record_id=None if record_id is None else str(record_id),
        filename=str(row["filename"]),
        extension=str(row["extension"]),
        content_type=str(row["content_type"]),
        size=int(row["size"]),
        sha256=str(row["sha256"]),
        principal=str(row["principal"]),
        uploaded_at=uploaded_at,
        unclaimed_at=_moment(row["unclaimed_at"]),
    )


__all__ = ["SQLAlchemyAttachmentRows"]
