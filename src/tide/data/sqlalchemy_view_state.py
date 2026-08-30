"""SQLAlchemy-backed storage for per-user browse arrangements.

A sibling of :mod:`tide.data.sqlalchemy_cursors`, built the same way:
construction emits no DDL, ``legacy`` mode validates a table it did not
create, and the store knows nothing about the model — validation is the
service's, this is rows.

One row per (principal, view). The document is one JSON column rather than a
child table per chosen column because the arrangement is only ever read and
replaced whole: nothing queries inside it, so giving each column a row would
buy joins and ordering bookkeeping for no reader.
"""

from __future__ import annotations

import json

from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    Unicode,
    delete,
    insert,
    inspect,
    select,
    update,
)
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from tide.data.sqlalchemy import (
    SchemaCompatibilityError,
    SchemaIssue,
    SchemaManagementError,
    _create_engine,
)
from tide.services.view_state import ViewStateColumn

MAX_DOCUMENT_BYTES = 65_536


class ViewStateStoreError(Exception):
    """The database refused to read or keep an arrangement."""


class SQLAlchemyViewStateStore:
    """Durable per-user browse arrangements every process can see.

    Construction never emits DDL. ``mode='managed'`` enables an explicit
    :meth:`create_schema`; the default ``legacy`` mode only validates and
    uses a pre-existing table.
    """

    shared = True

    def __init__(
        self,
        bind: str | URL | Engine,
        *,
        mode: str = "legacy",
        schema: str | None = None,
    ) -> None:
        if mode not in {"managed", "legacy"}:
            raise ValueError(
                "view-state store mode must be 'managed' or 'legacy'"
            )
        self.mode = mode
        self._owns_engine = not isinstance(bind, Engine)
        self.engine = bind if isinstance(bind, Engine) else _create_engine(bind)
        self.metadata = MetaData()
        self.state_table = Table(
            "tide_view_state",
            self.metadata,
            Column("principal", Unicode(255), primary_key=True),
            Column("view", String(255), primary_key=True),
            Column("document", Unicode(), nullable=False),
            schema=schema,
        )

    def create_schema(self) -> None:
        if self.mode != "managed":
            raise SchemaManagementError(
                "legacy view-state mode forbids creating or changing "
                "schema objects"
            )
        self.metadata.create_all(self.engine)

    def schema_issues(self) -> tuple[SchemaIssue, ...]:
        inspector = inspect(self.engine)
        table = self.state_table
        object_name = (
            f"{table.schema}.{table.name}" if table.schema else table.name
        )
        entity = "tide.view-state-store"
        if not inspector.has_table(table.name, schema=table.schema):
            return (
                SchemaIssue(entity, object_name, "mapped table does not exist"),
            )
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

    def get(
        self, principal: str, view: str
    ) -> tuple[ViewStateColumn, ...] | None:
        try:
            with self.engine.connect() as connection:
                row = (
                    connection.execute(
                        select(self.state_table.c.document).where(
                            self.state_table.c.principal == principal,
                            self.state_table.c.view == view,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as error:
            raise ViewStateStoreError(
                "could not read the stored arrangement"
            ) from error
        if row is None:
            return None
        return _deserialize(str(row["document"]))

    def put(
        self,
        principal: str,
        view: str,
        columns: tuple[ViewStateColumn, ...],
    ) -> None:
        document = _serialize(columns)
        if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError(
                f"serialized arrangement exceeds {MAX_DOCUMENT_BYTES} bytes"
            )
        try:
            with self.engine.begin() as connection:
                updated = connection.execute(
                    update(self.state_table)
                    .where(
                        self.state_table.c.principal == principal,
                        self.state_table.c.view == view,
                    )
                    .values(document=document)
                )
                if updated.rowcount == 0:
                    try:
                        connection.execute(
                            insert(self.state_table).values(
                                principal=principal,
                                view=view,
                                document=document,
                            )
                        )
                    except IntegrityError:
                        # A sibling process inserted between the two
                        # statements; their row exists, so update it.
                        connection.execute(
                            update(self.state_table)
                            .where(
                                self.state_table.c.principal == principal,
                                self.state_table.c.view == view,
                            )
                            .values(document=document)
                        )
        except SQLAlchemyError as error:
            raise ViewStateStoreError(
                "could not keep the arrangement"
            ) from error

    def delete(self, principal: str, view: str) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.state_table).where(
                        self.state_table.c.principal == principal,
                        self.state_table.c.view == view,
                    )
                )
        except SQLAlchemyError as error:
            raise ViewStateStoreError(
                "could not remove the arrangement"
            ) from error

    def dispose(self) -> None:
        if self._owns_engine:
            self.engine.dispose()


def _serialize(columns: tuple[ViewStateColumn, ...]) -> str:
    return json.dumps(
        {
            "columns": [
                {"name": column.name}
                if column.label is None
                else {"name": column.name, "label": column.label}
                for column in columns
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _deserialize(document: str) -> tuple[ViewStateColumn, ...]:
    try:
        parsed = json.loads(document)
        return tuple(
            ViewStateColumn(
                name=str(item["name"]),
                label=(
                    str(item["label"]) if item.get("label") is not None else None
                ),
            )
            for item in parsed["columns"]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ViewStateStoreError(
            "stored arrangement document is invalid"
        ) from error
