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
    SQLAlchemyActionExecutionStore,
    SQLAlchemyCursorStore,
    SQLAlchemyRepository,
)
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
        cursor_store: SQLAlchemyCursorStore | None = None
        execution_store: SQLAlchemyActionExecutionStore | None = None
        if mode == "managed":
            cursor_store = SQLAlchemyCursorStore(repository.engine, mode="managed")
            execution_store = SQLAlchemyActionExecutionStore(
                repository.engine,
                mode="managed",
            )

        if arguments.create_schema:
            repository.create_schema()
            if cursor_store is not None and execution_store is not None:
                cursor_store.create_schema()
                execution_store.create_schema()

        repository.validate_schema()
        repository.validate_query_support()
        if cursor_store is not None and execution_store is not None:
            cursor_store.validate_schema()
            execution_store.validate_schema()

        state_label = "durable state" if mode == "managed" else "process-local state"
        return RunStorage(
            repository,
            f"database via {environment_name} ({state_label})",
            cursor_store=cursor_store,
            execution_store=execution_store,
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
