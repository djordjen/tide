"""Every scalar field type must be handled by every layer that dispatches on one.

TIDE dispatches on `type:` in a dozen places -- SQL mapping, wire encoding,
OpenAPI annotation, service-boundary coercion -- and most of those chains end
in a fall-through rather than an error. A type nobody taught them about does
not announce itself: it silently becomes a string in SQL, or is accepted
unchecked at the service boundary. So the lists here are derived from
`SCALAR_FIELD_TYPES`, and a tenth type fails these tests before it can reach a
renderer.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Unicode
from sqlalchemy.dialects.mssql import base as mssql_base
from sqlalchemy.dialects.sqlite import base as sqlite_base

from tide import compile_project
from tide.api.client import _decode_field
from tide.api.openapi import _scalar_annotation
from tide.api.wire import decode_wire_value
from tide.data import InMemoryRepository, SQLAlchemyRepository
from tide.data import sqlalchemy as sqlalchemy_adapter
from tide.model.source import SCALAR_FIELD_TYPES
from tide.runtime import Principal, RequestContext
from tide.services import RecordsService
from tide.services.records import _coerce_scalar

# One YAML declaration and one natural Python value per scalar type. Both are
# keyed by type name and both are checked against `SCALAR_FIELD_TYPES` below,
# so neither can quietly fall behind the contract it is supposed to cover.
DECLARATIONS = {
    "string": "{type: string, length: 40}",
    "integer": "{type: integer}",
    "decimal": "{type: decimal, precision: 12, scale: 2}",
    "boolean": "{type: boolean}",
    "date": "{type: date}",
    "datetime": "{type: datetime}",
    "uuid": "{type: uuid}",
    "choice": "{type: choice, choices: [draft, posted]}",
}

VALUES: dict[str, object] = {
    "string": "text",
    "integer": 7,
    "decimal": Decimal("1.50"),
    "boolean": True,
    "date": date(2026, 8, 13),
    "datetime": datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
    "uuid": UUID("3f1d9c72-5b84-4a11-9d0e-6c2f8a7b4e35"),
    "choice": "draft",
}

# Types whose SQL column is legitimately the string fallback in `_sql_type`.
TEXT_BACKED = {"string", "choice"}


def _as_json(value: object) -> object:
    """The JSON form a client would actually send for this value."""

    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


@pytest.fixture(scope="module")
def model(tmp_path_factory: pytest.TempPathFactory):
    """One entity carrying a field of every scalar type."""

    project = tmp_path_factory.mktemp("field-types") / "probe"
    (project / "models").mkdir(parents=True)
    (project / "tide.yaml").write_text(
        'schema_version: "0.1"\n'
        "application: {name: Field Type Probe, version: 0.1.0}\n"
        "model: {paths: [models]}\n",
        encoding="utf-8",
    )
    fields = "\n".join(
        f"  {name}: {declaration}" for name, declaration in sorted(DECLARATIONS.items())
    )
    (project / "models" / "sample.yaml").write_text(
        "entity: probe.Sample\ndisplay: string\nfields:\n"
        "  id: {type: integer, primary_key: true}\n" + fields + "\n",
        encoding="utf-8",
    )
    return compile_project(project)


def test_the_tables_here_cover_every_declared_scalar_type() -> None:
    assert set(DECLARATIONS) == set(SCALAR_FIELD_TYPES)
    assert set(VALUES) == set(SCALAR_FIELD_TYPES)


@pytest.mark.parametrize("field_type", SCALAR_FIELD_TYPES)
def test_the_service_boundary_checks_a_value_of_every_scalar_type(
    field_type: str,
) -> None:
    """`_coerce_scalar` reports 'accepted' for any type it does not name."""

    _, accepted = _coerce_scalar(field_type, object())

    assert not accepted, (
        f"a {field_type} field accepts a value of any Python type at the "
        "service boundary"
    )


@pytest.mark.parametrize("field_type", SCALAR_FIELD_TYPES)
def test_every_scalar_field_type_maps_to_its_own_sql_type(
    field_type: str, model
) -> None:
    """`_sql_type` ends in a string fallback, so an unmapped type becomes text."""

    column = sqlalchemy_adapter._sql_type(
        model, model.entity("probe.Sample").field(field_type)
    )

    if field_type in TEXT_BACKED:
        assert isinstance(column, Unicode)
    else:
        assert not isinstance(column, Unicode), (
            f"a {field_type} field fell through to the string column type"
        )


@pytest.mark.parametrize("field_type", SCALAR_FIELD_TYPES)
def test_every_scalar_field_type_has_an_openapi_annotation(
    field_type: str, model
) -> None:
    assert _scalar_annotation(model.entity("probe.Sample").field(field_type)) is not None


@pytest.mark.parametrize("field_type", SCALAR_FIELD_TYPES)
def test_every_scalar_field_type_survives_the_wire(field_type: str, model) -> None:
    """A value that goes out as JSON has to come back as the same Python value."""

    field = model.entity("probe.Sample").field(field_type)
    value = VALUES[field_type]

    restored = decode_wire_value(model, field, _as_json(value))

    assert restored == value, f"a {field_type} value did not survive the wire"
    assert type(restored) is type(value)


@pytest.mark.parametrize("field_type", SCALAR_FIELD_TYPES)
def test_every_scalar_field_type_survives_the_remote_client(
    field_type: str, model
) -> None:
    """The client decodes a second time, from its own chain of type branches.

    A type missing here is not an error: the branch chain simply ends and the
    raw JSON string is handed back as if it were the value.
    """

    field = model.entity("probe.Sample").field(field_type)
    value = VALUES[field_type]

    restored = _decode_field(model, field, _as_json(value))

    assert restored == value, f"a {field_type} value did not survive the client"
    assert type(restored) is type(value)


def _uuid_keyed_project(tmp_path, *, server_default: bool):
    """An application whose one entity is keyed by a uuid."""

    project = tmp_path / "guid"
    (project / "models").mkdir(parents=True)
    (project / "security").mkdir()
    (project / "tide.yaml").write_text(
        'schema_version: "0.1"\n'
        "application: {name: Guid Probe, version: 0.1.0}\n"
        "model: {paths: [models]}\n"
        "security: {paths: [security]}\n",
        encoding="utf-8",
    )
    declared = ", server_default: NEWSEQUENTIALID()" if server_default else ""
    (project / "models" / "thing.yaml").write_text(
        "entity: guid.Thing\n"
        "display: name\n"
        "permissions: {list: guid.thing.read, read: guid.thing.read, "
        "create: guid.thing.write, update: guid.thing.write, "
        "delete: guid.thing.write}\n"
        "fields:\n"
        f"  id: {{type: uuid, primary_key: true{declared}}}\n"
        "  name: {type: string, length: 20, required: true}\n",
        encoding="utf-8",
    )
    (project / "security" / "policies.yaml").write_text(
        "permissions: [guid.thing.read, guid.thing.write]\n"
        "roles:\n  operator:\n    grants: [guid.thing.read, guid.thing.write]\n",
        encoding="utf-8",
    )
    return compile_project(project)


@pytest.mark.parametrize("server_default", [False, True])
def test_a_uuid_key_is_generated_here_unless_the_database_declares_its_own(
    tmp_path, server_default: bool
) -> None:
    """Which side supplies the key is the whole design decision, so pin it.

    A legacy `NEWSEQUENTIALID()` column exists to keep its clustered index from
    fragmenting; generating a random uuid4 for it would defeat exactly the
    thing it was declared for.
    """

    model = _uuid_keyed_project(tmp_path, server_default=server_default)
    records = RecordsService(model, InMemoryRepository())
    values: dict[str, object] = {"name": "first"}

    records._assign_generated_identity(model.entity("guid.Thing"), values)

    if server_default:
        assert values.get("id") is None, (
            "the database's own generator was overridden by a client-side uuid"
        )
    else:
        assert isinstance(values.get("id"), UUID)


def test_a_record_keyed_by_a_uuid_round_trips_through_sqlite(tmp_path) -> None:
    """The key has to survive storage, not just be assigned.

    SQLite has no GUID type, so this stores a `CHAR(32)` and reads it back;
    proving it returns a `UUID` rather than the 32 characters it was written as
    is what makes the field type worth having.
    """

    model = _uuid_keyed_project(tmp_path, server_default=False)
    repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
    repository.create_schema()
    records = RecordsService(model, repository)
    context = RequestContext(Principal("user:probe", roles=frozenset({"operator"})))

    session = records.create("guid.Thing", context)
    session.values["name"] = "first"
    stored = records.commit(session, context)

    assert isinstance(stored["id"], UUID)
    assert records.get("guid.Thing", stored["id"], context)["name"] == "first"
    repository.dispose()


def test_the_sql_column_for_a_uuid_field_is_dialect_portable(model) -> None:
    """One declared type has to reach both supported dialects.

    SQL Server stores a GUID as `UNIQUEIDENTIFIER` and SQLite has no such type
    at all, so the portability is the whole reason this is a field type rather
    than a hand-written column.
    """

    column = sqlalchemy_adapter._sql_type(
        model, model.entity("probe.Sample").field("uuid")
    )

    assert column.compile(dialect=mssql_base.dialect()) == "UNIQUEIDENTIFIER"
    assert column.compile(dialect=sqlite_base.dialect()) == "CHAR(32)"
