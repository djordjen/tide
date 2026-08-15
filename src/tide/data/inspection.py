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

from dataclasses import dataclass, fields as dataclass_fields
import fnmatch
import re
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Uuid, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.sql.type_api import TypeEngine

from .repository import RelationshipLoadPlan
from .sqlalchemy import _as_comparable, _create_engine

__all__ = [
    "DemotedReference",
    "InspectedCollection",
    "InspectedEntity",
    "InspectionProposal",
    "SkippedObject",
    "inspect_schema",
    "synthesize_collections",
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
class DemotedReference:
    """A foreign key mapped as a plain column because its target is absent.

    Reflecting part of a schema, or one whose neighbour cannot be mapped, must
    not produce a reference to an entity the proposal does not contain: that
    model would not compile. The column exists in the database either way and
    may well be required, so it keeps its physical mapping and loses only the
    navigation.
    """

    name: str
    target: str
    reason: str

    def __str__(self) -> str:
        return f"{self.name}: {self.reason}"


@dataclass(frozen=True, slots=True)
class InspectedCollection:
    """A foreign key seen from the entity it points at.

    Reflection can only find the half of a relationship that holds the column.
    Proposing that half alone gives every child a picker and every parent
    nothing -- no way to see, from a record, what points at it.
    """

    owner: str
    name: str
    target: str
    inverse: str

    @property
    def view(self) -> str:
        # Qualified by the owner: two entities can hold a same-named
        # collection of the same child, and one view cannot serve both
        # because each hides a different key.
        return f"{self.owner}.{self.name}.inline"

    @property
    def declaration(self) -> str:
        return (
            f"{{type: collection, target: {self.target}, inverse: {self.inverse}}}"
        )


@dataclass(frozen=True, slots=True)
class InspectedEntity:
    name: str
    schema: str | None
    table: str
    display: str
    fields: tuple[tuple[str, str], ...]
    references: tuple[tuple[str, str], ...] = ()

    def document(
        self,
        permissions: dict[str, str] | None = None,
        collections: tuple[InspectedCollection, ...] = (),
    ) -> str:
        storage = (
            f"storage: {{schema: {self.schema}, table: {self.table}}}"
            if self.schema
            else f"storage: {{table: {self.table}}}"
        )
        lines = [
            f"entity: {self.name}",
            storage,
            f"display: {self.display}",
        ]
        if permissions is not None:
            lines.extend(
                [
                    "expose:",
                    "  tui: true",
                    "  rest:",
                    f"    operations: [{', '.join(REST_OPERATIONS)}]",
                    "  mcp:",
                    f"    resources: [{', '.join(MCP_RESOURCES)}]",
                    f"    tools: [{', '.join(MCP_TOOLS)}]",
                    "permissions:",
                ]
            )
            lines.extend(
                f"  {operation}: {permission}"
                for operation, permission in permissions.items()
            )
        lines.extend(self.value_map_stub)
        lines.append("fields:")
        lines.extend(f"  {name}: {declaration}" for name, declaration in self.fields)
        lines.extend(
            f"  {collection.name}: {collection.declaration}"
            for collection in collections
        )
        return "\n".join(lines) + "\n"

    @property
    def value_map_stub(self) -> tuple[str, ...]:
        """A commented reminder naming this table's likely enumerations.

        An integer column very often carries one, and reflection cannot read
        its members: they live in the application that wrote the rows. The
        candidates are the plain integers -- not the key, which identifies,
        and not a foreign key, which already points somewhere.
        """

        candidates = tuple(
            name
            for name, declaration in self.fields
            if declaration.startswith("{type: integer")
            and "primary_key" not in declaration
        )
        if not candidates:
            return ()
        return (
            "# An integer column often stands for an enumeration whose member",
            "# names live in the application that wrote the rows, not in the",
            "# database, so reflection cannot read them. Caption one and its",
            "# labels are shown everywhere and are the only values accepted:",
            f"#   {candidates[0]}:",
            "#     type: integer",
            "#     column: ...",
            "#     values:",
            "#     - {value: 0, label: First}",
            "#     - {value: 1, label: Second}",
            f"# Candidates here: {', '.join(candidates)}",
        )

    @property
    def slug(self) -> str:
        return self.name.split(".")[-1]

    @property
    def filename(self) -> str:
        return f"{_snake(self.slug)}.yaml"

    @property
    def column_order(self) -> tuple[str, ...]:
        """Field names with the display field first, and nothing dropped.

        A wide legacy table makes a wide browse view, which is easier to read
        and delete from than a short one is to discover the omissions in.
        """

        names = [name for name, _ in self.fields]
        if self.display in names:
            names.insert(0, names.pop(names.index(self.display)))
        return tuple(names)


@dataclass(frozen=True, slots=True)
class InspectionProposal:
    entities: tuple[InspectedEntity, ...]
    skipped: tuple[SkippedObject, ...]
    demoted: tuple[DemotedReference, ...] = ()
    deselected: tuple[str, ...] = ()
    unmatched: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _FieldPlan:
    """One reflected column, decided but not yet written out.

    `scalar` is the declaration the column gets on its own; a column that also
    carries a foreign key keeps both, because whether it can stay a reference
    is not known until every table has been planned.
    """

    name: str
    physical: str
    scalar: str | None
    reference: Any | None = None


@dataclass(frozen=True, slots=True)
class _EntityPlan:
    name: str
    schema: str | None
    table: str
    key_name: str
    fields: tuple[_FieldPlan, ...]


def inspect_schema(
    bind: str | Engine,
    *,
    schema: str | None = None,
    namespace: str = "legacy",
    tables: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> InspectionProposal:
    """Reflect a live schema and propose one legacy entity per usable table.

    `tables` and `exclude` accept exact names or glob patterns. Planning runs
    over every selected table before anything is written out, so a foreign key
    is resolved against the entities the proposal will actually contain rather
    than against the ones the database happens to have.
    """

    engine = bind if isinstance(bind, Engine) else _create_engine(bind)
    owns_engine = not isinstance(bind, Engine)
    try:
        inspector = inspect(engine)
        available = tuple(inspector.get_table_names(schema=schema))
        selected, deselected, unmatched = _select(available, tables, exclude)

        plans: list[_EntityPlan] = []
        skipped: list[SkippedObject] = []
        for table in selected:
            plan, reasons = _plan_entity(
                inspector, table, schema=schema, namespace=namespace
            )
            skipped.extend(reasons)
            if plan is not None:
                plans.append(plan)

        resolved = {(plan.schema, plan.table): plan.name for plan in plans}
        entities: list[InspectedEntity] = []
        demoted: list[DemotedReference] = []
        for plan in plans:
            entity, losses = _render_entity(plan, resolved)
            entities.append(entity)
            demoted.extend(losses)

        return InspectionProposal(
            tuple(entities),
            tuple(skipped),
            tuple(demoted),
            deselected,
            unmatched,
        )
    finally:
        if owns_engine:
            engine.dispose()


def _select(
    available: tuple[str, ...],
    tables: tuple[str, ...],
    exclude: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Split the schema's tables into selected and deselected, plus dead patterns.

    A `tables` pattern nothing matches is returned rather than ignored: a typo
    would otherwise hand back a smaller project that still looks complete.
    """

    unmatched = tuple(
        pattern
        for pattern in tables
        if not any(_matches(pattern, name) for name in available)
    )
    selected = tuple(
        name
        for name in available
        if (not tables or any(_matches(pattern, name) for pattern in tables))
        and not any(_matches(pattern, name) for pattern in exclude)
    )
    return (
        tuple(sorted(selected)),
        tuple(sorted(set(available) - set(selected))),
        unmatched,
    )


def _matches(pattern: str, name: str) -> bool:
    """Case-insensitively, and the same way on every platform.

    `fnmatch.fnmatch` folds case according to the host operating system, so it
    would make one command select different tables on Windows and on Linux.
    """

    return fnmatch.fnmatchcase(name.casefold(), pattern.casefold())


def _plan_entity(
    inspector: Any,
    table: str,
    *,
    schema: str | None,
    namespace: str,
) -> tuple[_EntityPlan | None, list[SkippedObject]]:
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

    fields: list[_FieldPlan] = []
    used: set[str] = set()
    for column in inspector.get_columns(table, schema=schema):
        physical = str(column["name"])
        name = _field_name(physical, used)
        is_key = physical in key_columns
        reference = None if is_key else references.get(physical)
        declaration = _scalar_declaration(column, physical, is_key)
        if declaration is None and reference is None:
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
        fields.append(_FieldPlan(name, physical, declaration, reference))
        used.add(name)

    if not fields:
        return None, [*skipped, SkippedObject(qualified, "no column could be mapped")]

    key_name = _field_name(str(key_columns[0]), set())
    if not any(field.name == key_name for field in fields):
        return None, [
            *skipped,
            SkippedObject(qualified, f"primary key column {key_columns[0]} is unmapped"),
        ]

    return (
        _EntityPlan(
            name=_entity_name(table, schema, namespace),
            schema=schema,
            table=table,
            key_name=key_name,
            fields=tuple(fields),
        ),
        skipped,
    )


def _render_entity(
    plan: _EntityPlan,
    resolved: dict[tuple[str | None, str], str],
) -> tuple[InspectedEntity, list[DemotedReference]]:
    """Write one planned entity out, resolving its foreign keys as it goes."""

    qualified = f"{plan.schema}.{plan.table}" if plan.schema else plan.table
    fields: list[tuple[str, str]] = []
    references: list[tuple[str, str]] = []
    demoted: list[DemotedReference] = []
    for field in plan.fields:
        if field.reference is None:
            assert field.scalar is not None  # planning admits nothing else
            fields.append((field.name, field.scalar))
            continue
        referred_table = str(field.reference["referred_table"])
        target = resolved.get(
            (field.reference.get("referred_schema") or plan.schema, referred_table)
        )
        if target is not None:
            fields.append(
                (
                    field.name,
                    f"{{type: reference, target: {target}, "
                    f"storage: {field.physical}, on_delete: restrict}}",
                )
            )
            references.append((field.name, target))
            continue
        if field.scalar is not None:
            fields.append((field.name, field.scalar))
        demoted.append(
            DemotedReference(
                f"{qualified}.{field.physical}",
                referred_table,
                f"{referred_table} is not in this proposal, so the column is "
                + (
                    "mapped without its reference"
                    if field.scalar is not None
                    else "unmapped: no field type carries it on its own"
                ),
            )
        )

    return (
        InspectedEntity(
            name=plan.name,
            schema=plan.schema,
            table=plan.table,
            display=_display_field(fields, plan.key_name),
            fields=tuple(fields),
            references=tuple(references),
        ),
        demoted,
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
    joined = "".join(_capitalized(part) for part in parts)
    return joined if joined[:1].isalpha() else f"T{joined}"


def _capitalized(part: str) -> str:
    """Uppercase the first letter, and fold the rest only if it is shouted.

    `CUSTOMER` is a single shouted word and reads better as `Customer`, but
    `EquipmentInstance` is already a name: lowercasing its tail produces
    `Equipmentinstance`, which is nobody's idea of the entity.
    """

    if part.isupper():
        return part[:1] + part[1:].lower()
    return part[:1].upper() + part[1:]


def _snake(name: str) -> str:
    """`EquipmentInstancesTasks` -> `equipment_instances_tasks`.

    Every proposed name goes through this, because the boundary between words
    is only in the capitals and lowercasing the run destroys it for good:
    `SerialNo` becomes `serialno`, which labels as `Serialno` on every surface
    and cannot be split back apart by any layer downstream.
    """

    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return _identifier(re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", spaced))


def _identifier(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower()
    return cleaned if cleaned[:1].isalpha() else f"s_{cleaned}"


def _field_name(physical: str, used: set[str]) -> str:
    candidate = _snake(physical) or "field"
    if candidate not in used:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in used:
        suffix += 1
    return f"{candidate}_{suffix}"


#: The entity operations a runnable proposal declares a permission for.
OPERATIONS: tuple[str, ...] = ("list", "read", "create", "update", "delete")

#: What a runnable proposal opens on each surface. `runnable` that meant one
#: surface would not be what the flag says -- and the Web UI in particular is
#: a REST client, so exposing the TUI alone leaves the browser reading an
#: application with no routes in it. Every line is deletable afterwards.
REST_OPERATIONS: tuple[str, ...] = ("list", "get", "create", "update", "delete")
MCP_RESOURCES: tuple[str, ...] = ("schema", "record")
MCP_TOOLS: tuple[str, ...] = ("search", "create", "update", "delete")


def render_project(
    proposal: InspectionProposal,
    *,
    application: str,
    version: str = "0.1.0",
    runnable: bool = False,
    role: str = "operator",
) -> dict[str, str]:
    """The proposal as a path-to-text map, ready to be reviewed and saved.

    Without `runnable` this is metadata: a model that compiles and matches the
    database, exposed to nothing and readable by nobody. With it, the proposal
    also carries what every surface needs before it can open a record -- the
    channel, the permissions, a role holding them, and a view per entity --
    because a model that compiles is not the same thing as an application.
    """

    collections = (
        synthesize_collections(proposal.entities)[0] if runnable else {}
    )
    children = {entity.name: entity for entity in proposal.entities}
    documents: dict[str, str] = {}
    for entity in proposal.entities:
        permissions = _permissions(entity) if runnable else None
        owned = collections.get(entity.name, ())
        documents[f"models/{entity.filename}"] = entity.document(permissions, owned)
        if not runnable:
            continue
        stem = entity.filename.removesuffix(".yaml")
        documents[f"views/{stem}-browse.yaml"] = _list_view(entity, "browse")
        documents[f"views/{stem}-lookup.yaml"] = _list_view(entity, "lookup")
        documents[f"views/{stem}-edit.yaml"] = _edit_view(entity, owned)
        for collection in owned:
            documents[f"views/{stem}-{collection.name}-inline.yaml"] = _inline_view(
                collection, children[collection.target]
            )

    manifest = [
        'schema_version: "0.1"',
        f"application: {{name: {application}, version: {version}}}",
        "database: {mode: legacy}",
        "model: {paths: [models]}",
    ]
    if runnable:
        manifest.append("views: {paths: [views]}")
        manifest.append("security: {paths: [security]}")
        documents["security/policies.yaml"] = _security_document(
            proposal.entities, role
        )
    documents["tide.yaml"] = "\n".join(manifest) + "\n"
    return documents


def _permissions(entity: InspectedEntity) -> dict[str, str]:
    """One permission per operation, so any of them can be withheld later."""

    namespace = entity.name.split(".")[0]
    subject = _snake(entity.slug)
    return {
        operation: f"{namespace}.{subject}.{operation}" for operation in OPERATIONS
    }


def _list_view(entity: InspectedEntity, kind: str) -> str:
    lines = [
        f"view: {entity.name}.{kind}",
        f"entity: {entity.name}",
        f"kind: {kind}",
        "columns:",
        *(f"- {name}" for name in entity.column_order),
    ]
    searchable = tuple(
        name
        for name, declaration in entity.fields
        if declaration.startswith("{type: string")
    )
    if searchable:
        lines.append("search:")
        lines.extend(f"- {name}" for name in searchable)
    return "\n".join(lines) + "\n"


#: How many collection hops hydration will make before it refuses. Read from
#: the load plan rather than restated, because a proposal that exceeds it does
#: not degrade -- `RelationshipExpansionLimit` fails the whole list, so a
#: browse over the entity at the top of too long a chain shows nothing at all.
MAX_COLLECTION_CHAIN: int = next(
    int(item.default)
    for item in dataclass_fields(RelationshipLoadPlan)
    if item.name == "max_depth" and isinstance(item.default, int)
)


def synthesize_collections(
    entities: tuple[InspectedEntity, ...],
) -> tuple[dict[str, tuple[InspectedCollection, ...]], tuple[SkippedObject, ...]]:
    """Turn every proposed reference around, as far as hydration can follow.

    Collections are loaded eagerly and with no cycle guard, so the graph these
    make has to stay a shallow DAG: an entity pointing at itself would recurse
    forever, and a chain longer than the load plan follows fails the list
    rather than truncating it. What cannot be turned around is returned rather
    than dropped, because a missing tab is not something a reader would think
    to look for.
    """

    incoming: dict[str, list[tuple[str, str]]] = {}
    for child in entities:
        for field_name, target in child.references:
            incoming.setdefault(target, []).append((child.name, field_name))

    edges: dict[str, set[str]] = {entity.name: set() for entity in entities}
    owned: dict[str, tuple[InspectedCollection, ...]] = {}
    declined: list[SkippedObject] = []
    for entity in entities:
        accepted: list[tuple[str, str]] = []
        for child_name, inverse in incoming.get(entity.name, ()):
            origin = f"{child_name}.{inverse}"
            if child_name == entity.name:
                declined.append(
                    SkippedObject(
                        origin,
                        "a collection of its own entity would be hydrated "
                        "into itself without end",
                    )
                )
                continue
            if _reaches(edges, child_name, entity.name):
                declined.append(
                    SkippedObject(
                        origin,
                        f"a collection here would close a cycle back to "
                        f"{entity.name}",
                    )
                )
                continue
            edges[entity.name].add(child_name)
            if _longest_chain(edges) > MAX_COLLECTION_CHAIN:
                edges[entity.name].discard(child_name)
                declined.append(
                    SkippedObject(
                        origin,
                        "a collection here would put a record more than "
                        f"{MAX_COLLECTION_CHAIN} hops from a list that loads it",
                    )
                )
                continue
            accepted.append((child_name, inverse))

        if not accepted:
            continue
        # Naming a collection after the child alone collides the moment a child
        # points at the same parent twice, so a repeated child keeps its key.
        repeated = {
            candidate
            for candidate in {name for name, _ in accepted}
            if sum(name == candidate for name, _ in accepted) > 1
        }
        used = {name for name, _ in entity.fields}
        collections: list[InspectedCollection] = []
        for child_name, inverse in accepted:
            base = _snake(child_name.split(".")[-1])
            name = _field_name(
                f"{base}_{inverse}" if child_name in repeated else base, used
            )
            used.add(name)
            collections.append(
                InspectedCollection(entity.name, name, child_name, inverse)
            )
        owned[entity.name] = tuple(collections)
    return owned, tuple(declined)


def _reaches(edges: dict[str, set[str]], start: str, goal: str) -> bool:
    stack, seen = [start], set()
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return False


def _longest_chain(edges: dict[str, set[str]]) -> int:
    """The most collection hops any list would make. The graph is acyclic."""

    memo: dict[str, int] = {}

    def downstream(node: str) -> int:
        if node not in memo:
            memo[node] = max(
                (1 + downstream(child) for child in edges.get(node, ())),
                default=0,
            )
        return memo[node]

    return max((downstream(node) for node in edges), default=0)


def _edit_view(
    entity: InspectedEntity,
    collections: tuple[InspectedCollection, ...] = (),
) -> str:
    """A form over every field, with each reference given its picker.

    A reference field with no `lookup_view` is inert -- the form answers "No
    lookup view is configured" and there is no way to set the value -- so the
    lookup views exist to be pointed at from here.
    """

    lines = [
        f"view: {entity.name}.edit",
        f"entity: {entity.name}",
        "kind: form",
    ]
    if entity.references:
        lines.append("fields:")
        lines.extend(
            f"  {name}: {{editor: lookup, lookup_view: {target}.lookup}}"
            for name, target in entity.references
        )
    lines.extend(["layout:", f"- group: {entity.slug}", "  rows:"])
    order = entity.column_order
    for index in range(0, len(order), 2):
        row = order[index : index + 2]
        lines.append(f"  - - {row[0]}")
        lines.extend(f"    - {name}" for name in row[1:])
    for collection in collections:
        lines.extend(
            [
                f"- collection: {collection.name}",
                f"  view: {collection.view}",
                "  actions: [add, apply, remove]",
            ]
        )
    return "\n".join(lines) + "\n"


def _inline_view(collection: InspectedCollection, child: InspectedEntity) -> str:
    """The row editor for one collection, minus the key that placed it there.

    The reference tying a row to the record it is already displayed inside
    carries no information on that screen, and the collection sets it anyway.
    """

    lines = [
        f"view: {collection.view}",
        f"entity: {child.name}",
        "kind: inline_edit",
    ]
    pickers = tuple(
        (name, target)
        for name, target in child.references
        if name != collection.inverse
    )
    if pickers:
        lines.append("fields:")
        lines.extend(
            f"  {name}: {{editor: lookup, lookup_view: {target}.lookup}}" for name, target in pickers
        )
    lines.append("columns:")
    lines.extend(
        f"- {name}" for name in child.column_order if name != collection.inverse
    )
    return "\n".join(lines) + "\n"


def _security_document(entities: tuple[InspectedEntity, ...], role: str) -> str:
    """Every declared permission, granted to one role.

    Granting all of them is the only honest starting point: what an account
    should be able to do is a decision about the business, not something a
    reflection pass can observe.
    """

    permissions = sorted(
        {
            permission
            for entity in entities
            for permission in _permissions(entity).values()
        }
    )
    lines = [
        "permissions:",
        *(f"- {permission}" for permission in permissions),
        "roles:",
        f"  {role}:",
        "    grants:",
        *(f"    - {permission}" for permission in permissions),
    ]
    return "\n".join(lines) + "\n"
