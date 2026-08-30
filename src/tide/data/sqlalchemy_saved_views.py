"""SQLAlchemy-backed storage for saved views.

A sibling of :mod:`tide.data.sqlalchemy_view_state`, built the same way:
construction emits no DDL, ``legacy`` mode validates a table it did not
create, and the store keeps documents whose rules live in the service.

One row per (principal, view, name); the rest of the state is one JSON
column, read and replaced whole, listed in name order so a menu is stable
without the client sorting.
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
from tide.services.saved_views import SavedView
from tide.services.view_state import ViewStateColumn

MAX_DOCUMENT_BYTES = 65_536


class SavedViewStoreError(Exception):
    """The database refused to read or keep a saved view."""


class SQLAlchemySavedViewStore:
    """Durable saved views every process can see.

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
                "saved-view store mode must be 'managed' or 'legacy'"
            )
        self.mode = mode
        self._owns_engine = not isinstance(bind, Engine)
        self.engine = bind if isinstance(bind, Engine) else _create_engine(bind)
        self.metadata = MetaData()
        self.saved_table = Table(
            "tide_saved_view",
            self.metadata,
            Column("principal", Unicode(255), primary_key=True),
            Column("view", String(255), primary_key=True),
            Column("name", Unicode(60), primary_key=True),
            Column("document", Unicode(), nullable=False),
            schema=schema,
        )

    def create_schema(self) -> None:
        if self.mode != "managed":
            raise SchemaManagementError(
                "legacy saved-view mode forbids creating or changing "
                "schema objects"
            )
        self.metadata.create_all(self.engine)

    def schema_issues(self) -> tuple[SchemaIssue, ...]:
        inspector = inspect(self.engine)
        table = self.saved_table
        object_name = (
            f"{table.schema}.{table.name}" if table.schema else table.name
        )
        entity = "tide.saved-view-store"
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

    def list(self, principal: str, view: str) -> tuple[SavedView, ...]:
        try:
            with self.engine.connect() as connection:
                rows = (
                    connection.execute(
                        select(
                            self.saved_table.c.name,
                            self.saved_table.c.document,
                        )
                        .where(
                            self.saved_table.c.principal == principal,
                            self.saved_table.c.view == view,
                        )
                        .order_by(self.saved_table.c.name)
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as error:
            raise SavedViewStoreError(
                "could not read the saved views"
            ) from error
        return tuple(
            _deserialize(str(row["name"]), str(row["document"])) for row in rows
        )

    def put(self, principal: str, view: str, entry: SavedView) -> None:
        document = _serialize(entry)
        if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError(
                f"serialized saved view exceeds {MAX_DOCUMENT_BYTES} bytes"
            )
        try:
            with self.engine.begin() as connection:
                updated = connection.execute(
                    update(self.saved_table)
                    .where(
                        self.saved_table.c.principal == principal,
                        self.saved_table.c.view == view,
                        self.saved_table.c.name == entry.name,
                    )
                    .values(document=document)
                )
                if updated.rowcount == 0:
                    try:
                        connection.execute(
                            insert(self.saved_table).values(
                                principal=principal,
                                view=view,
                                name=entry.name,
                                document=document,
                            )
                        )
                    except IntegrityError:
                        connection.execute(
                            update(self.saved_table)
                            .where(
                                self.saved_table.c.principal == principal,
                                self.saved_table.c.view == view,
                                self.saved_table.c.name == entry.name,
                            )
                            .values(document=document)
                        )
        except SQLAlchemyError as error:
            raise SavedViewStoreError(
                "could not keep the saved view"
            ) from error

    def delete(self, principal: str, view: str, name: str) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    delete(self.saved_table).where(
                        self.saved_table.c.principal == principal,
                        self.saved_table.c.view == view,
                        self.saved_table.c.name == name,
                    )
                )
        except SQLAlchemyError as error:
            raise SavedViewStoreError(
                "could not remove the saved view"
            ) from error

    def dispose(self) -> None:
        if self._owns_engine:
            self.engine.dispose()


def _serialize(entry: SavedView) -> str:
    return json.dumps(
        {
            "named_filter": entry.named_filter,
            "value_filters": {
                name: list(values)
                for name, values in entry.value_filters.items()
            },
            "sort": [
                [field_name, descending]
                for field_name, descending in entry.sort
            ],
            "columns": (
                [
                    {"name": column.name}
                    if column.label is None
                    else {"name": column.name, "label": column.label}
                    for column in entry.columns
                ]
                if entry.columns is not None
                else None
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _deserialize(name: str, document: str) -> SavedView:
    try:
        parsed = json.loads(document)
        columns = parsed.get("columns")
        return SavedView(
            name=name,
            named_filter=(
                str(parsed["named_filter"])
                if parsed.get("named_filter") is not None
                else None
            ),
            value_filters={
                str(field_name): tuple(values)
                for field_name, values in parsed.get(
                    "value_filters", {}
                ).items()
            },
            sort=tuple(
                (str(field_name), bool(descending))
                for field_name, descending in parsed.get("sort", ())
            ),
            columns=(
                tuple(
                    ViewStateColumn(
                        name=str(item["name"]),
                        label=(
                            str(item["label"])
                            if item.get("label") is not None
                            else None
                        ),
                    )
                    for item in columns
                )
                if columns is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SavedViewStoreError(
            "stored saved-view document is invalid"
        ) from error
