"""SQLAlchemy-backed shared storage for browser sessions.

The sibling of :mod:`tide.data.sqlalchemy_cursors`, and deliberately built the
same way: construction emits no DDL, the identifier is stored only as a digest,
and ``legacy`` mode validates a table it did not create.

Times are epoch seconds throughout, because that is what the session contract
carries; the authenticator's clock is ``time.time``. Storing them as database
timestamps would mean a conversion on every comparison, and a session is kept
or ended by comparing them. An operator reading the table directly can spell
that ``datetime(expires_at, 'unixepoch')`` or the equivalent for the dialect.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import secrets
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Index,
    MetaData,
    String,
    Table,
    Unicode,
    delete,
    func,
    inspect,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine, RowMapping, URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tide.api.session_store import SessionRecord
from tide.data.sqlalchemy import (
    SchemaCompatibilityError,
    SchemaIssue,
    SchemaManagementError,
    _create_engine,
)
from tide.runtime.errors import SessionStoreError
from tide.services.action_store import (
    deserialize_action_value,
    serialize_action_value,
)

MAX_SUBJECT_CHARACTERS = 255


class SQLAlchemySessionStore:
    """Browser sessions every worker can see, and a restart does not forget.

    Construction never emits DDL. ``mode='managed'`` enables an explicit
    :meth:`create_schema`; the default ``legacy`` mode only validates and uses
    a pre-existing table, which is what a TIDE application pointed at a
    database it does not own is allowed to do.

    Reading a session writes to it, because capacity eviction is by least
    recent use and a read is a use. It is one update by primary key. If that
    ever shows up in a profile, the fix is to touch on an interval rather than
    to evict by age of creation, which would drop the sessions people are
    actually working in.
    """

    shared = True

    def __init__(
        self,
        bind: str | URL | Engine,
        *,
        mode: str = "legacy",
        schema: str | None = None,
        max_entries: int = 4096,
        max_state_bytes: int = 65_536,
    ) -> None:
        if mode not in {"managed", "legacy"}:
            raise ValueError("session store mode must be 'managed' or 'legacy'")
        for label, value in (
            ("capacity", max_entries),
            ("state size limit", max_state_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"session store {label} must be positive")
        self.mode = mode
        self.max_entries = max_entries
        self.max_state_bytes = max_state_bytes
        self._owns_engine = not isinstance(bind, Engine)
        self.engine = bind if isinstance(bind, Engine) else _create_engine(bind)
        self.metadata = MetaData()
        self.session_table = Table(
            "tide_browser_session",
            self.metadata,
            Column("session_hash", String(64), primary_key=True),
            Column("subject", String(MAX_SUBJECT_CHARACTERS), nullable=False),
            Column("state_json", Unicode(), nullable=False),
            Column("created_at", Float(), nullable=False),
            Column("touched_at", Float(), nullable=False),
            Column("expires_at", Float(), nullable=False),
            schema=schema,
        )
        Index("ix_tide_browser_session_subject", self.session_table.c.subject)
        Index("ix_tide_browser_session_expiry", self.session_table.c.expires_at)
        self.failure_table = Table(
            "tide_login_failure",
            self.metadata,
            # An opaque identifier rather than (subject, occurred_at): two
            # failures can land in the same instant, and a primary key that
            # collides there would silently under-count exactly the burst it
            # exists to notice.
            Column("failure_id", String(32), primary_key=True),
            Column("subject", String(MAX_SUBJECT_CHARACTERS), nullable=False),
            Column("occurred_at", Float(), nullable=False),
            schema=schema,
        )
        Index(
            "ix_tide_login_failure_subject",
            self.failure_table.c.subject,
            self.failure_table.c.occurred_at,
        )

    def create_schema(self) -> None:
        if self.mode != "managed":
            raise SchemaManagementError(
                "legacy session-store mode forbids creating or changing schema objects"
            )
        self.metadata.create_all(self.engine)

    def schema_issues(self) -> tuple[SchemaIssue, ...]:
        inspector = inspect(self.engine)
        issues: list[SchemaIssue] = []
        for table in (self.session_table, self.failure_table):
            object_name = (
                f"{table.schema}.{table.name}" if table.schema else table.name
            )
            entity = "tide.session-store"
            if not inspector.has_table(table.name, schema=table.schema):
                issues.append(
                    SchemaIssue(entity, object_name, "mapped table does not exist")
                )
                continue
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

    def create(self, session_id: str, record: SessionRecord, *, now: float) -> None:
        state_json = self._state_json(record)
        subject = _subject(record.subject)
        try:
            with self.engine.begin() as connection:
                self._purge_expired(connection, now)
                connection.execute(
                    insert(self.session_table).values(
                        session_hash=_session_hash(session_id),
                        subject=subject,
                        state_json=state_json,
                        created_at=now,
                        touched_at=now,
                        expires_at=record.expires_at,
                    )
                )
                self._trim_capacity(connection)
        except IntegrityError as error:
            raise SessionStoreError("browser session already exists") from error
        except SQLAlchemyError as error:
            raise SessionStoreError("could not persist the browser session") from error

    def read(self, session_id: str, *, now: float) -> SessionRecord | None:
        session_hash = _session_hash(session_id)
        try:
            with self.engine.begin() as connection:
                row = (
                    connection.execute(
                        select(self.session_table).where(
                            self.session_table.c.session_hash == session_hash
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    return None
                if float(row["expires_at"]) <= now:
                    connection.execute(
                        delete(self.session_table).where(
                            self.session_table.c.session_hash == session_hash
                        )
                    )
                    return None
                connection.execute(
                    update(self.session_table)
                    .where(self.session_table.c.session_hash == session_hash)
                    .values(touched_at=now)
                )
        except SQLAlchemyError as error:
            raise SessionStoreError("could not read the browser session") from error
        return self._record(row)

    def replace(
        self,
        session_id: str,
        expected: SessionRecord,
        record: SessionRecord,
        *,
        now: float,
    ) -> bool:
        state_json = self._state_json(record)
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(self.session_table)
                    .where(
                        self.session_table.c.session_hash == _session_hash(session_id),
                        self.session_table.c.state_json == _serialize_state(
                            expected.state
                        ),
                        self.session_table.c.expires_at == expected.expires_at,
                    )
                    .values(
                        subject=_subject(record.subject),
                        state_json=state_json,
                        touched_at=now,
                        expires_at=record.expires_at,
                    )
                )
        except SQLAlchemyError as error:
            raise SessionStoreError("could not update the browser session") from error
        return bool(result.rowcount)

    def discard(self, session_id: str, expected: SessionRecord) -> bool:
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    delete(self.session_table).where(
                        self.session_table.c.session_hash == _session_hash(session_id),
                        self.session_table.c.state_json == _serialize_state(
                            expected.state
                        ),
                        self.session_table.c.expires_at == expected.expires_at,
                    )
                )
        except SQLAlchemyError as error:
            raise SessionStoreError("could not end the browser session") from error
        return bool(result.rowcount)

    def delete(self, session_id: str) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.session_table).where(
                        self.session_table.c.session_hash == _session_hash(session_id)
                    )
                )
        except SQLAlchemyError as error:
            raise SessionStoreError("could not end the browser session") from error

    def delete_subject(self, subject: str) -> int:
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    delete(self.session_table).where(
                        self.session_table.c.subject == _subject(subject)
                    )
                )
        except SQLAlchemyError as error:
            raise SessionStoreError("could not end the browser sessions") from error
        return max(int(result.rowcount or 0), 0)

    def clear(self) -> int:
        try:
            with self.engine.begin() as connection:
                result = connection.execute(delete(self.session_table))
        except SQLAlchemyError as error:
            raise SessionStoreError("could not end the browser sessions") from error
        return max(int(result.rowcount or 0), 0)

    def update_subject(self, subject: str, changes: Mapping[str, Any]) -> int:
        owner = _subject(subject)
        changed = 0
        try:
            with self.engine.begin() as connection:
                rows = (
                    connection.execute(
                        select(self.session_table).where(
                            self.session_table.c.subject == owner
                        )
                    )
                    .mappings()
                    .all()
                )
                for row in rows:
                    record = self._record(row)
                    if record is None:
                        continue
                    connection.execute(
                        update(self.session_table)
                        .where(
                            self.session_table.c.session_hash == row["session_hash"]
                        )
                        .values(
                            state_json=_serialize_state(record.merged(changes).state)
                        )
                    )
                    changed += 1
        except SQLAlchemyError as error:
            raise SessionStoreError("could not update the browser sessions") from error
        return changed

    def count_failures(self, subject: str, *, now: float, window: float) -> int:
        owner = _subject(subject)
        try:
            with self.engine.begin() as connection:
                self._purge_failures(connection, now - window)
                return int(
                    connection.execute(
                        select(func.count())
                        .select_from(self.failure_table)
                        .where(self.failure_table.c.subject == owner)
                    ).scalar_one()
                )
        except SQLAlchemyError as error:
            raise SessionStoreError("could not read the sign-in failures") from error

    def record_failure(
        self,
        subject: str,
        *,
        now: float,
        window: float,
        limit: int,
    ) -> None:
        owner = _subject(subject)
        try:
            # SERIALIZABLE because the count and the insert decide a security
            # limit together: two workers reading four and each writing a fifth
            # is how a bound of five becomes a bound of six.
            with self.engine.connect() as base_connection:
                connection = base_connection.execution_options(
                    isolation_level="SERIALIZABLE"
                )
                with connection.begin():
                    self._purge_failures(connection, now - window)
                    counted = int(
                        connection.execute(
                            select(func.count())
                            .select_from(self.failure_table)
                            .where(self.failure_table.c.subject == owner)
                        ).scalar_one()
                    )
                    if counted >= limit:
                        return
                    connection.execute(
                        insert(self.failure_table).values(
                            failure_id=secrets.token_hex(16),
                            subject=owner,
                            occurred_at=now,
                        )
                    )
        except SQLAlchemyError as error:
            raise SessionStoreError("could not record the sign-in failure") from error

    def clear_failures(self, subject: str) -> None:
        owner = _subject(subject)
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.failure_table).where(
                        self.failure_table.c.subject == owner
                    )
                )
        except SQLAlchemyError as error:
            raise SessionStoreError("could not clear the sign-in failures") from error

    def _purge_failures(self, connection: Connection, boundary: float) -> None:
        """Drop every subject's stale failures, not only this one's.

        The table is bounded by doing it for everybody: a caller only ever
        asks about one subject, so pruning only that one would leave the rows
        of an attacker who never repeats a username to accumulate forever.
        """

        connection.execute(
            delete(self.failure_table).where(
                self.failure_table.c.occurred_at <= boundary
            )
        )

    def purge_expired(self, now: float) -> int:
        try:
            with self.engine.begin() as connection:
                result = self._purge_expired(connection, now)
        except SQLAlchemyError as error:
            raise SessionStoreError(
                "could not purge expired browser sessions"
            ) from error
        return max(int(result.rowcount or 0), 0)

    def dispose(self) -> None:
        if self._owns_engine:
            self.engine.dispose()

    def _state_json(self, record: SessionRecord) -> str:
        try:
            state_json = _serialize_state(record.state)
        except (TypeError, ValueError) as error:
            raise ValueError("session state contains an unsupported value") from error
        if len(state_json.encode("utf-8")) > self.max_state_bytes:
            raise ValueError(
                f"serialized session state exceeds {self.max_state_bytes} bytes"
            )
        return state_json

    def _record(self, row: RowMapping) -> SessionRecord | None:
        try:
            state = deserialize_action_value(str(row["state_json"]))
        except (TypeError, ValueError):
            return None
        if not isinstance(state, Mapping):
            return None
        return SessionRecord(
            subject=str(row["subject"]),
            expires_at=float(row["expires_at"]),
            state=dict(state),
        )

    def _purge_expired(self, connection: Connection, now: float) -> Any:
        return connection.execute(
            delete(self.session_table).where(self.session_table.c.expires_at <= now)
        )

    def _trim_capacity(self, connection: Connection) -> None:
        hashes = (
            connection.execute(
                select(self.session_table.c.session_hash)
                .order_by(
                    self.session_table.c.touched_at.desc(),
                    self.session_table.c.session_hash.desc(),
                )
                .offset(self.max_entries)
            )
            .scalars()
            .all()
        )
        if hashes:
            connection.execute(
                delete(self.session_table).where(
                    self.session_table.c.session_hash.in_(hashes)
                )
            )


def _serialize_state(state: Mapping[str, Any]) -> str:
    """Serialize session state into the text the swap is decided by.

    This leans on `serialize_action_value` being canonical -- it sorts mapping
    keys -- and that is load-bearing here rather than incidental. The
    compare-and-swap keeping two workers from overwriting each other compares
    this string, so text equality is standing in for state equality, and two
    workers do not agree on the key order of a dict literal. Replacing this
    with a serializer that preserved insertion order would make the loser of a
    swap a worker that had not actually lost.
    """

    return serialize_action_value(dict(state))


def _session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _subject(subject: str) -> str:
    if not isinstance(subject, str) or not subject:
        raise ValueError("session subject must be a non-empty string")
    if len(subject) > MAX_SUBJECT_CHARACTERS:
        raise ValueError(
            f"session subject exceeds {MAX_SUBJECT_CHARACTERS} characters"
        )
    return subject
