"""The framework's own tables in a managed database, named in one place.

TIDE keeps state of its own beside an application's data: continuation
cursors, action idempotency and audit, and browser sessions. Four things have
to agree about what that set is -- `--create-schema` creates it, startup
validates it, `tide db diff` proposes it, and backup verification checks it --
and until now each of the four named the stores itself, as did nine test
fixtures.

That is a hand-maintained list wearing four hats, and adding the session store
on 2026-08-15 proved what it costs. The seven test fixtures failed loudly, so
they were found. The two *production* omissions did not: `tide db diff`
answered "No schema differences detected" against a database genuinely missing
both new tables, and a backup missing them verified as compatible. A list that
is silently incomplete is worse than one that is loudly wrong.

So there is one list, and everything reads it from here. Adding a store is a
field on :class:`FrameworkStores`; the four layers pick it up without being
told, and `tests/test_framework_schema.py` asserts that they do -- per table,
derived from this module rather than restated, so a fifth store is covered on
the day it is added.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Protocol

from sqlalchemy import MetaData, Table
from sqlalchemy.engine import URL, Engine

from tide.data.sqlalchemy import SchemaIssue
from tide.data.sqlalchemy_actions import SQLAlchemyActionExecutionStore
from tide.data.sqlalchemy_attachments import SQLAlchemyAttachmentRows
from tide.data.sqlalchemy_cursors import SQLAlchemyCursorStore
from tide.data.sqlalchemy_leases import SQLAlchemyServerLeaseStore
from tide.data.sqlalchemy_sessions import SQLAlchemySessionStore
from tide.data.sqlalchemy_saved_views import SQLAlchemySavedViewStore
from tide.data.sqlalchemy_view_state import SQLAlchemyViewStateStore


class ManagedStore(Protocol):
    """What a store must offer to be part of the managed framework schema.

    Not an abstraction over what the stores *do* -- they have nothing in
    common there -- but over the four questions the schema layers ask them.
    A new store satisfies this by having the same four members its siblings
    already have; if it does not, mypy says so at the point it is added to
    :class:`FrameworkStores` rather than at the point a backup silently
    verifies without it.
    """

    metadata: MetaData

    def create_schema(self) -> None: ...

    def schema_issues(self) -> tuple[SchemaIssue, ...]: ...

    def validate_schema(self) -> None: ...


@dataclass(frozen=True, slots=True)
class FrameworkStores:
    """Every store the framework owns in a managed database.

    Held by name because `open_run_storage` hands them to different callers,
    and iterated as :attr:`all` because the schema layers do not care which is
    which. **Adding a store is adding a field here, and nothing else**: `all`
    reads the fields rather than restating them, so the four layers pick it up
    without being told and there is no second list to forget.

    A field must satisfy :class:`ManagedStore`, and `framework_stores` must
    construct it. Those are the two things mypy will not let you skip.
    """

    cursors: SQLAlchemyCursorStore
    actions: SQLAlchemyActionExecutionStore
    sessions: SQLAlchemySessionStore
    leases: SQLAlchemyServerLeaseStore
    attachments: SQLAlchemyAttachmentRows
    view_state: SQLAlchemyViewStateStore
    saved_views: SQLAlchemySavedViewStore

    @property
    def all(self) -> tuple[ManagedStore, ...]:
        """Every declared store, read off the declaration itself.

        Written out by hand this was a second list beside the fields, and a
        store added to one and not the other is invisible to everything
        downstream -- the table list, the four layers, and any test derived
        from them all simply get shorter and keep passing. Deriving it from
        `fields` means the field *is* the single place, which is the whole
        claim this module makes.
        """

        return tuple(getattr(self, field.name) for field in fields(self))

    @property
    def tables(self) -> tuple[Table, ...]:
        """Every framework table, in a stable order.

        `tide db diff` compares against this, and the test that keeps the four
        layers honest parametrizes over it, so a store's tables are covered by
        virtue of the store being here.
        """

        return tuple(
            table for store in self.all for table in store.metadata.tables.values()
        )

    def create_schema(self) -> None:
        for store in self.all:
            store.create_schema()

    def validate_schema(self) -> None:
        for store in self.all:
            store.validate_schema()

    def schema_issues(self) -> tuple[SchemaIssue, ...]:
        return tuple(issue for store in self.all for issue in store.schema_issues())


def framework_stores(
    bind: str | URL | Engine,
    *,
    mode: str = "managed",
) -> FrameworkStores:
    """Open every framework store against one bind. Emits no DDL."""

    return FrameworkStores(
        cursors=SQLAlchemyCursorStore(bind, mode=mode),
        actions=SQLAlchemyActionExecutionStore(bind, mode=mode),
        sessions=SQLAlchemySessionStore(bind, mode=mode),
        leases=SQLAlchemyServerLeaseStore(bind, mode=mode),
        attachments=SQLAlchemyAttachmentRows(bind, mode=mode),
        view_state=SQLAlchemyViewStateStore(bind, mode=mode),
        saved_views=SQLAlchemySavedViewStore(bind, mode=mode),
    )
