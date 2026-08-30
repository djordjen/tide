"""Opening the data source a command was pointed at."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import sys

from sqlalchemy.exc import SQLAlchemyError

from tide.compiler.normalized import ApplicationModel
from tide.data import (
    InMemoryRepository,
    Repository,
    SQLAlchemyRepository,
    framework_stores,
)
from tide.data.sqlalchemy_leases import SQLAlchemyServerLeaseStore
from tide.data.sqlalchemy_attachments import SQLAlchemyAttachmentRows
from tide.data.sqlalchemy_sessions import SQLAlchemySessionStore
from tide.data.sqlalchemy_view_state import SQLAlchemyViewStateStore
from tide.runtime import TideRuntimeError
from tide.services import (
    ActionExecutionStore,
    CursorStore,
)


@dataclass(slots=True)
class RunStorage:
    repository: Repository
    source_label: str
    cursor_store: CursorStore | None = None
    execution_store: ActionExecutionStore | None = None
    session_store: SQLAlchemySessionStore | None = None
    attachment_rows: SQLAlchemyAttachmentRows | None = None
    view_state_rows: SQLAlchemyViewStateStore | None = None
    lease_store: SQLAlchemyServerLeaseStore | None = None
    """Browser sessions every process can see, when there is a place to put them.

    Present for a managed database and absent for a legacy one, on the same
    reasoning as its two siblings: a database TIDE does not own is a database
    TIDE may not create a table in. Built here rather than in `tide serve`
    because `--create-schema` may equally have been run from `tide run`, and a
    table only one of the two commands creates is a table the other one fails
    to validate.
    """

    def dispose(self) -> None:
        if isinstance(self.repository, SQLAlchemyRepository):
            self.repository.dispose()


def open_run_storage(
    arguments: argparse.Namespace,
    model: ApplicationModel,
    *,
    purpose: str = "TUI",
) -> RunStorage | None:
    environment_name = arguments.database_env
    if environment_name is None:
        if arguments.create_schema:
            print(
                f"{purpose} startup failed: --create-schema requires --database-env",
                file=sys.stderr,
            )
            return None
        return RunStorage(InMemoryRepository(), "empty in-memory data")

    database_url = os.environ.get(environment_name)
    if not database_url:
        print(
            f"{purpose} database startup failed: environment variable "
            f"{environment_name!r} is not set",
            file=sys.stderr,
        )
        return None

    repository: SQLAlchemyRepository | None = None
    try:
        repository = SQLAlchemyRepository(model, database_url)
        mode = str(model.database["mode"])
        # One list, so a store added later is created and validated here
        # without this function being told about it. The named fields below
        # are for consumers that want a particular store; a store nothing
        # consumes by name needs no field, and still gets its schema.
        stores = (
            framework_stores(repository.engine) if mode == "managed" else None
        )

        if arguments.create_schema:
            repository.create_schema()
            if stores is not None:
                stores.create_schema()

        repository.validate_schema()
        repository.validate_query_support()
        if stores is not None:
            stores.validate_schema()

        state_label = "durable state" if mode == "managed" else "process-local state"
        return RunStorage(
            repository,
            f"database via {environment_name} ({state_label})",
            cursor_store=stores.cursors if stores is not None else None,
            execution_store=stores.actions if stores is not None else None,
            session_store=stores.sessions if stores is not None else None,
            attachment_rows=stores.attachments if stores is not None else None,
            view_state_rows=stores.view_state if stores is not None else None,
            lease_store=stores.leases if stores is not None else None,
        )
    except (SQLAlchemyError, TideRuntimeError, ValueError) as error:
        if repository is not None:
            repository.dispose()
        detail = str(error) if isinstance(error, TideRuntimeError) else type(error).__name__
        print(
            f"{purpose} database startup failed via {environment_name!r}: {detail}",
            file=sys.stderr,
        )
        return None
