"""Propose TIDE metadata from an existing database schema.

Legacy mode requires every table and column to be named explicitly, which is
the right rule -- a naming convention must never silently select a different
table -- and also the reason adopting a large existing schema by hand is
tedious enough to stop people trying. This turns one reflection pass into
reviewable source files.

It is read-only in the strongest sense the adapter offers: connect, reflect,
disconnect. Nothing here writes to the database it read, and what it cannot map
it reports rather than guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Uuid, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.sql.type_api import TypeEngine

from .sqlalchemy import _as_comparable, _create_engine

__all__ = [
    "InspectedEntity",
    "InspectionProposal",
    "SkippedObject",
    "inspect_schema",
    "render_project",
]


@dataclass(frozen=True, slots=True)
class SkippedObject:
    """Something the inspector declined to propose, and why."""

    name: str
    reason: str

    def __str__(self) -> str:
        return f"{self.name}: {self.reason}"


@dataclass(frozen=True, slots=True)
class InspectedEntity:
    name: str
    schema: str | None
    table: str
    display: str
    fields: tuple[tuple[str, str], ...]

    @property
    def document(self) -> str:
        storage = (
            f"storage: {{schema: {self.schema}, table: {self.table}}}"
            if self.schema
            else f"storage: {{table: {self.table}}}"
        )
        lines = [
            f"entity: {self.name}",
            storage,
            f"display: {self.display}",
            "fields:",
            *(f"  {name}: {declaration}" for name, declaration in self.fields),
        ]
        return "\n".join(lines) + "\n"

    @property
    def filename(self) -> str:
        return f"{self.name.split('.')[-1].lower()}.yaml"


@dataclass(frozen=True, slots=True)
class InspectionProposal:
    entities: tuple[InspectedEntity, ...]
    skipped: tuple[SkippedObject, ...]


def inspect_schema(
    bind: str | Engine,
    *,
    schema: str | None = None,
    namespace: str = "legacy",
    tables: tuple[str, ...] = (),
) -> InspectionProposal:
    """Reflect a live schema and propose one legacy entity per usable table."""

    engine = bind if isinstance(bind, Engine) else _create_engine(bind)
    owns_engine = not isinstance(bind, Engine)
    try:
        inspector = inspect(engine)
        available = tuple(inspector.get_table_names(schema=schema))
        wanted = tuple(tables) or available
        entities: list[InspectedEntity] = []
        skipped: list[SkippedObject] = []
        for table in sorted(wanted):
            if table not in available:
                skipped.append(SkippedObject(table, "table does not exist"))
                continue
            entity, reasons = _propose_entity(
                inspector, table, schema=schema, namespace=namespace
            )
            skipped.extend(reasons)
            if entity is not None:
                entities.append(entity)
        return InspectionProposal(tuple(entities), tuple(skipped))
    finally:
        if owns_engine:
            engine.dispose()


def _propose_entity(
    inspector: Any,
    table: str,
    *,
    schema: str | None,
    namespace: str,
) -> tuple[InspectedEntity | None, list[SkippedObject]]:
    qualified = f"{schema}.{table}" if schema else table
    skipped: list[SkippedObject] = []

    key_columns = tuple(
        inspector.get_pk_constraint(table, schema=schema).get("constrained_columns")
        or ()
    )
    if not key_columns:
        return None, [SkippedObject(qualified, "table declares no primary key")]
    if len(key_columns) > 1:
        return None, [
            SkippedObject(
                qualified,
                "composite primary key "
                f"({', '.join(key_columns)}); schema v0.1 maps one key column",
            )
        ]

    references = {
        constraint["constrained_columns"][0]: constraint
        for constraint in inspector.get_foreign_keys(table, schema=schema)
        if len(constraint.get("constrained_columns") or ()) == 1
        and len(constraint.get("referred_columns") or ()) == 1
    }

    fields: list[tuple[str, str]] = []
    used: set[str] = set()
    for column in inspector.get_columns(table, schema=schema):
        physical = str(column["name"])
        name = _field_name(physical, used)
        reference = references.get(physical)
        if reference is not None and physical not in key_columns:
            target = _entity_name(
                str(reference["referred_table"]),
                reference.get("referred_schema") or schema,
                namespace,
            )
            fields.append(
                (
                    name,
                    f"{{type: reference, target: {target}, "
                    f"storage: {physical}, on_delete: restrict}}",
                )
            )
            used.add(name)
            continue

        declaration = _scalar_declaration(column, physical, physical in key_columns)
        if declaration is None:
            skipped.append(
                SkippedObject(
                    f"{qualified}.{physical}",
                    f"no field type maps {column['type']}"
                    + (
                        "; the column requires a value, so writes will fail "
                        "until it is mapped by hand"
                        if not column.get("nullable", True)
                        and not _has_default(column)
                        else ""
                    ),
                )
            )
            continue
        fields.append((name, declaration))
        used.add(name)

    if not fields:
        return None, [*skipped, SkippedObject(qualified, "no column could be mapped")]

    key_name = _field_name(str(key_columns[0]), set())
    if not any(name == key_name for name, _ in fields):
        return None, [
            *skipped,
            SkippedObject(qualified, f"primary key column {key_columns[0]} is unmapped"),
        ]

    return (
        InspectedEntity(
            name=_entity_name(table, schema, namespace),
            schema=schema,
            table=table,
            display=_display_field(fields, key_name),
            fields=tuple(fields),
        ),
        skipped,
    )


def _scalar_declaration(
    column: Any, physical: str, is_key: bool
) -> str | None:
    """One field declaration, or None when no TIDE type carries this column."""

    parts = _type_parts(column["type"])
    if parts is None:
        return None
    if is_key:
        parts.append("primary_key: true")
    elif not column.get("nullable", True):
        parts.append("required: true")
    parts.append(f"column: {physical}")
    return "{" + ", ".join(parts) + "}"


def _type_parts(column_type: TypeEngine[Any]) -> list[str] | None:
    """Map a reflected column type onto a TIDE field type and its shape.

    `_as_comparable` is the same restatement the schema check uses, so a money
    column is proposed as exactly the decimal that validation will later accept
    -- rather than the two forming their own opinions and disagreeing.
    """

    resolved = _as_comparable(column_type)
    if isinstance(resolved, Uuid):
        return ["type: uuid"]
    if isinstance(resolved, Boolean):
        return ["type: boolean"]
    if isinstance(resolved, Numeric) and not isinstance(resolved, Integer):
        parts = ["type: decimal"]
        if resolved.precision:
            parts.append(f"precision: {resolved.precision}")
        if resolved.scale is not None:
            parts.append(f"scale: {resolved.scale}")
        return parts
    if isinstance(resolved, Integer):
        return ["type: integer"]
    if isinstance(resolved, DateTime):
        return ["type: datetime"]
    if isinstance(resolved, Date):
        return ["type: date"]
    if isinstance(resolved, String):
        parts = ["type: string"]
        if resolved.length:
            parts.append(f"length: {resolved.length}")
        return parts
    return None


def _has_default(column: Any) -> bool:
    return any(
        column.get(name) is not None
        for name in ("default", "computed", "identity")
    ) or bool(column.get("autoincrement"))


def _display_field(fields: list[tuple[str, str]], key_name: str) -> str:
    """What a record calls itself: the first text column, else its key."""

    for name, declaration in fields:
        if name != key_name and declaration.startswith("{type: string"):
            return name
    return key_name


def _entity_name(table: str, schema: str | None, namespace: str) -> str:
    return f"{_identifier(schema or namespace)}.{_camel(table)}"


def _camel(name: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", name) if part]
    if not parts:
        return "Table"
    joined = "".join(part[:1].upper() + part[1:].lower() for part in parts)
    return joined if joined[:1].isalpha() else f"T{joined}"


def _identifier(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower()
    return cleaned if cleaned[:1].isalpha() else f"s_{cleaned}"


def _field_name(physical: str, used: set[str]) -> str:
    candidate = _identifier(physical) or "field"
    if candidate not in used:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in used:
        suffix += 1
    return f"{candidate}_{suffix}"


def render_project(
    proposal: InspectionProposal,
    *,
    application: str,
    version: str = "0.1.0",
) -> dict[str, str]:
    """The proposal as a path-to-text map, ready to be reviewed and saved."""

    documents = {
        f"models/{entity.filename}": entity.document for entity in proposal.entities
    }
    documents["tide.yaml"] = (
        'schema_version: "0.1"\n'
        f"application: {{name: {application}, version: {version}}}\n"
        "database: {mode: legacy}\n"
        "model: {paths: [models]}\n"
    )
    return documents
