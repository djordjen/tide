"""A lease saying which process is serving an application right now.

A TIDE process cannot see its siblings. It can see their rows, and that is
enough for the one question worth asking at startup: is another server already
running in a mode whose sessions live in the process that issued them?

Where sessions are shared this is unnecessary, and no lease is taken. Where
they are not -- `--auth oidc` always, and every mode against a legacy database
-- a second process behind one address does not fail cleanly. It fails as
intermittent 401s, because a user's requests land on whichever process the
proxy picked and only one of them knows them. That is the shape this refuses.

The lease is deliberately short-lived and renewed, rather than held until a
clean shutdown. A server that is killed, panics, or loses power would
otherwise lock its own application out until somebody found the row and
deleted it, and an operator restarting a crashed service at three in the
morning is exactly who must not meet that.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    Column,
    Float,
    Index,
    MetaData,
    String,
    Table,
    delete,
    insert,
    inspect,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine, URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tide.data.sqlalchemy import (
    SchemaCompatibilityError,
    SchemaIssue,
    SchemaManagementError,
    _create_engine,
)
from tide.runtime.errors import ServerLeaseError

MAX_NAME_CHARACTERS = 255


@dataclass(frozen=True, slots=True)
class LeaseResult:
    """The answer to "may I serve?", and enough to explain a refusal."""

    granted: bool
    holder: str
    held_since: float
    renewed_at: float


class SQLAlchemyServerLeaseStore:
    """Which process holds the right to serve one application and scope.

    Construction never emits DDL, and `legacy` mode validates a table it did
    not create, exactly like its sibling stores.

    Times are epoch seconds, matching the session store and the `time.time`
    clock the server runs on. Every process reads and writes the same rows, so
    a clock that disagrees with its siblings' shortens or lengthens its own
    lease rather than corrupting anybody else's -- the comparison is always
    between one process's `now` and a timestamp another process wrote, which
    is a real limitation and the reason the TTL is minutes rather than
    seconds.
    """

    def __init__(
        self,
        bind: str | URL | Engine,
        *,
        mode: str = "legacy",
        schema: str | None = None,
    ) -> None:
        if mode not in {"managed", "legacy"}:
            raise ValueError("lease store mode must be 'managed' or 'legacy'")
        self.mode = mode
        self._owns_engine = not isinstance(bind, Engine)
        self.engine = bind if isinstance(bind, Engine) else _create_engine(bind)
        self.metadata = MetaData()
        self.lease_table = Table(
            "tide_server_lease",
            self.metadata,
            # Keyed by what the lease is *over*, not by who holds it: one row
            # per application and scope, so taking it is an insert that either
            # wins or collides rather than a read followed by a hopeful write.
            Column("application", String(MAX_NAME_CHARACTERS), primary_key=True),
            Column("scope", String(64), primary_key=True),
            Column("lease_id", String(64), nullable=False),
            Column("acquired_at", Float(), nullable=False),
            Column("renewed_at", Float(), nullable=False),
            schema=schema,
        )
        Index("ix_tide_server_lease_renewed", self.lease_table.c.renewed_at)

    def create_schema(self) -> None:
        if self.mode != "managed":
            raise SchemaManagementError(
                "legacy lease-store mode forbids creating or changing schema objects"
            )
        self.metadata.create_all(self.engine)

    def schema_issues(self) -> tuple[SchemaIssue, ...]:
        inspector = inspect(self.engine)
        table = self.lease_table
        object_name = f"{table.schema}.{table.name}" if table.schema else table.name
        entity = "tide.lease-store"
        if not inspector.has_table(table.name, schema=table.schema):
            return (SchemaIssue(entity, object_name, "mapped table does not exist"),)
        actual_columns = {
            str(column["name"])
            for column in inspector.get_columns(table.name, schema=table.schema)
        }
        return tuple(
            SchemaIssue(
                entity,
                f"{object_name}.{column.name}",
                "mapped column does not exist",
            )
            for column in table.columns
            if column.name not in actual_columns
        )

    def validate_schema(self) -> None:
        issues = self.schema_issues()
        if issues:
            raise SchemaCompatibilityError(issues)

    def acquire(
        self,
        lease_id: str,
        *,
        application: str,
        scope: str,
        now: float,
        ttl: float,
    ) -> LeaseResult:
        """Take the lease, or report who already has it.

        Asking again as the current holder renews rather than refuses, so a
        retry inside one startup is not this process losing to itself.
        """

        owner = _name(lease_id, "lease identifier")
        application = _name(application, "application")
        scope = _name(scope, "scope")
        if ttl <= 0:
            raise ValueError("lease TTL must be positive")
        try:
            # SERIALIZABLE because the read and the write together decide
            # whether a second server may run: two processes starting at the
            # same instant must not both find the row absent.
            with self.engine.connect() as base_connection:
                connection = base_connection.execution_options(
                    isolation_level="SERIALIZABLE"
                )
                with connection.begin():
                    self._purge_stale(connection, now - ttl)
                    row = (
                        connection.execute(
                            select(self.lease_table).where(
                                self.lease_table.c.application == application,
                                self.lease_table.c.scope == scope,
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if row is not None and str(row["lease_id"]) != owner:
                        return LeaseResult(
                            granted=False,
                            holder=str(row["lease_id"]),
                            held_since=float(row["acquired_at"]),
                            renewed_at=float(row["renewed_at"]),
                        )
                    if row is None:
                        connection.execute(
                            insert(self.lease_table).values(
                                application=application,
                                scope=scope,
                                lease_id=owner,
                                acquired_at=now,
                                renewed_at=now,
                            )
                        )
                        return LeaseResult(
                            granted=True,
                            holder=owner,
                            held_since=now,
                            renewed_at=now,
                        )
                    connection.execute(
                        update(self.lease_table)
                        .where(
                            self.lease_table.c.application == application,
                            self.lease_table.c.scope == scope,
                            self.lease_table.c.lease_id == owner,
                        )
                        .values(renewed_at=now)
                    )
                    return LeaseResult(
                        granted=True,
                        holder=owner,
                        held_since=float(row["acquired_at"]),
                        renewed_at=now,
                    )
        except IntegrityError:
            # Somebody inserted between the read and the write. They are the
            # holder; this process is the one that must stand down.
            return self._holder(application, scope)
        except SQLAlchemyError as error:
            raise ServerLeaseError("could not read or take the server lease") from error

    def renew(self, lease_id: str, *, now: float) -> bool:
        """Say this process is still here. False means it no longer holds it."""

        owner = _name(lease_id, "lease identifier")
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(self.lease_table)
                    .where(self.lease_table.c.lease_id == owner)
                    .values(renewed_at=now)
                )
        except SQLAlchemyError as error:
            raise ServerLeaseError("could not renew the server lease") from error
        return bool(result.rowcount)

    def release(self, lease_id: str) -> None:
        """Give up a lease on the way out, if it is still this process's."""

        owner = _name(lease_id, "lease identifier")
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.lease_table).where(
                        self.lease_table.c.lease_id == owner
                    )
                )
        except SQLAlchemyError as error:
            raise ServerLeaseError("could not release the server lease") from error

    def dispose(self) -> None:
        if self._owns_engine:
            self.engine.dispose()

    def _holder(self, application: str, scope: str) -> LeaseResult:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(self.lease_table).where(
                        self.lease_table.c.application == application,
                        self.lease_table.c.scope == scope,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:  # pragma: no cover - the row was removed in between
            raise ServerLeaseError("the server lease vanished while being taken")
        return LeaseResult(
            granted=False,
            holder=str(row["lease_id"]),
            held_since=float(row["acquired_at"]),
            renewed_at=float(row["renewed_at"]),
        )

    def _purge_stale(self, connection: Connection, boundary: float) -> None:
        connection.execute(
            delete(self.lease_table).where(
                self.lease_table.c.renewed_at <= boundary
            )
        )


def _name(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"lease {label} must be a non-empty string")
    if len(value) > MAX_NAME_CHARACTERS:
        raise ValueError(f"lease {label} exceeds {MAX_NAME_CHARACTERS} characters")
    return value
