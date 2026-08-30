"""The per-user view-state store: rows either backend must keep identically.

The stored document is a person's own arrangement of a browse grid — chosen
columns, their order, their renamed labels. It is state layered over the
declared view, never a second declaration of it, so the store knows nothing
about entities or fields: validation lives in the service, and these tests
only prove that what was put is what comes back, per principal and per view.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect

from tide.data import SchemaManagementError
from tide.data.sqlalchemy_view_state import SQLAlchemyViewStateStore
from tide.services.view_state import InMemoryViewStateRows, ViewStateColumn


def sql_store(tmp_path: Path) -> SQLAlchemyViewStateStore:
    url = f"sqlite+pysqlite:///{(tmp_path / 'view-state.db').as_posix()}"
    store = SQLAlchemyViewStateStore(url, mode="managed")
    store.create_schema()
    store.validate_schema()
    return store


ARRANGEMENT = (
    ViewStateColumn(name="number", label="No."),
    ViewStateColumn(name="total", label=None),
    ViewStateColumn(name="posted_by", label="Posted"),
)


@pytest.mark.parametrize("backend", ["sql", "memory"])
def test_an_arrangement_round_trips_with_order_and_labels(
    backend: str, tmp_path: Path
) -> None:
    rows = sql_store(tmp_path) if backend == "sql" else InMemoryViewStateRows()

    assert rows.get("user:1", "sales.Invoice.browse") is None
    rows.put("user:1", "sales.Invoice.browse", ARRANGEMENT)
    assert rows.get("user:1", "sales.Invoice.browse") == ARRANGEMENT

    replaced = (ViewStateColumn(name="total", label=None),)
    rows.put("user:1", "sales.Invoice.browse", replaced)
    assert rows.get("user:1", "sales.Invoice.browse") == replaced

    rows.delete("user:1", "sales.Invoice.browse")
    assert rows.get("user:1", "sales.Invoice.browse") is None
    rows.delete("user:1", "sales.Invoice.browse")


@pytest.mark.parametrize("backend", ["sql", "memory"])
def test_arrangements_are_private_to_a_principal_and_a_view(
    backend: str, tmp_path: Path
) -> None:
    rows = sql_store(tmp_path) if backend == "sql" else InMemoryViewStateRows()

    rows.put("user:1", "sales.Invoice.browse", ARRANGEMENT)
    assert rows.get("user:2", "sales.Invoice.browse") is None
    assert rows.get("user:1", "catalog.Product.browse") is None

    other = (ViewStateColumn(name="name", label=None),)
    rows.put("user:2", "sales.Invoice.browse", other)
    assert rows.get("user:1", "sales.Invoice.browse") == ARRANGEMENT
    assert rows.get("user:2", "sales.Invoice.browse") == other

    rows.delete("user:2", "sales.Invoice.browse")
    assert rows.get("user:1", "sales.Invoice.browse") == ARRANGEMENT


def test_an_arrangement_survives_a_restart(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{(tmp_path / 'view-state.db').as_posix()}"
    store = SQLAlchemyViewStateStore(url, mode="managed")
    store.create_schema()
    store.put("user:1", "sales.Invoice.browse", ARRANGEMENT)
    store.dispose()

    restarted = SQLAlchemyViewStateStore(url)
    restarted.validate_schema()
    assert restarted.get("user:1", "sales.Invoice.browse") == ARRANGEMENT
    restarted.dispose()


def test_the_sql_store_defaults_to_no_ddl(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{(tmp_path / 'view-state.db').as_posix()}"
    store = SQLAlchemyViewStateStore(url)
    assert inspect(store.engine).get_table_names() == []
    with pytest.raises(SchemaManagementError):
        store.create_schema()
    issues = store.schema_issues()
    assert issues and "does not exist" in issues[0].message
    store.dispose()
