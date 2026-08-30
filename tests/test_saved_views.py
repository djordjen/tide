"""Saved views: named grid states, kept per person and per view.

The store keeps documents it does not understand; the service owns the
rules once for every transport. A saved view stores the *components* of
the screen -- named filter, funnel checks, sort, a columns snapshot --
because restoring must relight the controls, and a grid constrained by
conditions its controls do not show is lying.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect

from tide import compile_project
from tide.data import InMemoryRepository, SchemaManagementError
from tide.data.sqlalchemy_saved_views import SQLAlchemySavedViewStore
from tide.runtime import Principal, RequestContext
from tide.services import RecordsService
from tide.services.saved_views import (
    MAX_SAVED_VIEWS,
    InMemorySavedViewRows,
    SavedView,
    SavedViewError,
    SavedViewService,
    UnknownSavedViewView,
)
from tide.services.view_state import ViewStateColumn

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def sql_store(tmp_path: Path) -> SQLAlchemySavedViewStore:
    url = f"sqlite+pysqlite:///{(tmp_path / 'saved-views.db').as_posix()}"
    store = SQLAlchemySavedViewStore(url, mode="managed")
    store.create_schema()
    store.validate_schema()
    return store


OVERDUE = SavedView(
    name="Overdue invoices",
    named_filter="drafts",
    value_filters={"status": ("draft", None)},
    sort=(("total", True),),
    columns=(
        ViewStateColumn(name="number", label="No."),
        ViewStateColumn(name="total", label=None),
    ),
)


@pytest.mark.parametrize("backend", ["sql", "memory"])
def test_a_saved_view_round_trips_upserts_and_deletes(
    backend: str, tmp_path: Path
) -> None:
    rows = sql_store(tmp_path) if backend == "sql" else InMemorySavedViewRows()

    assert rows.list("user:1", "sales.Invoice.browse") == ()
    rows.put("user:1", "sales.Invoice.browse", OVERDUE)
    assert rows.list("user:1", "sales.Invoice.browse") == (OVERDUE,)

    replaced = SavedView(
        name="Overdue invoices",
        named_filter=None,
        value_filters={},
        sort=(),
        columns=None,
    )
    rows.put("user:1", "sales.Invoice.browse", replaced)
    assert rows.list("user:1", "sales.Invoice.browse") == (replaced,)

    rows.delete("user:1", "sales.Invoice.browse", "Overdue invoices")
    assert rows.list("user:1", "sales.Invoice.browse") == ()
    rows.delete("user:1", "sales.Invoice.browse", "Overdue invoices")


@pytest.mark.parametrize("backend", ["sql", "memory"])
def test_saved_views_are_private_and_listed_by_name(
    backend: str, tmp_path: Path
) -> None:
    rows = sql_store(tmp_path) if backend == "sql" else InMemorySavedViewRows()

    second = SavedView(
        name="All drafts",
        named_filter="drafts",
        value_filters={},
        sort=(),
        columns=None,
    )
    rows.put("user:1", "sales.Invoice.browse", OVERDUE)
    rows.put("user:1", "sales.Invoice.browse", second)
    assert [entry.name for entry in rows.list("user:1", "sales.Invoice.browse")] == [
        "All drafts",
        "Overdue invoices",
    ]
    assert rows.list("user:2", "sales.Invoice.browse") == ()
    assert rows.list("user:1", "catalog.Product.browse") == ()


RANGED = SavedView(
    name="July over 500",
    named_filter=None,
    value_filters={"status": ("draft",)},
    conditions=(
        ("invoice_date", "gte", "2026-07-04"),
        ("invoice_date", "lte", "2026-07-12"),
        ("total", "gte", "500"),
        ("number", "icontains", "INV"),
    ),
    sort=(),
    columns=None,
)


@pytest.mark.parametrize("backend", ["sql", "memory"])
def test_operator_conditions_round_trip_beside_the_membership_map(
    backend: str, tmp_path: Path
) -> None:
    """A range must relight on restore, so its bounds are part of the
    stored document -- beside `value_filters`, never flattened into it."""

    rows = sql_store(tmp_path) if backend == "sql" else InMemorySavedViewRows()

    rows.put("user:1", "sales.Invoice.browse", RANGED)

    assert rows.list("user:1", "sales.Invoice.browse") == (RANGED,)


def test_the_service_keeps_conditions_and_validates_their_fields() -> None:
    service = _service()
    clerk = _context("sales_clerk")

    service.put(clerk, "sales.Invoice.browse", RANGED)
    (kept,) = service.list(clerk, "sales.Invoice.browse")
    assert kept.conditions == RANGED.conditions

    with pytest.raises(SavedViewError) as refusal:
        service.put(
            clerk,
            "sales.Invoice.browse",
            SavedView(
                name="Bad bounds",
                conditions=(
                    ("signed_document", "gte", "x"),
                    ("posted_by", "icontains", "demo"),
                ),
            ),
        )
    message = "\n".join(refusal.value.issues)
    assert "'signed_document' cannot carry a condition" in message
    assert "'posted_by' cannot carry a condition" in message


@pytest.mark.parametrize("backend", ["sql", "memory"])
def test_the_whole_catalogue_lists_by_view_then_name(
    backend: str, tmp_path: Path
) -> None:
    """The dashboard's ask: everything one principal keeps, across views,
    in a stable order, and nobody else's."""

    rows = sql_store(tmp_path) if backend == "sql" else InMemorySavedViewRows()
    second = SavedView(name="All drafts")
    elsewhere = SavedView(name="Cheap items")

    rows.put("user:1", "sales.Invoice.browse", OVERDUE)
    rows.put("user:1", "sales.Invoice.browse", second)
    rows.put("user:1", "catalog.Product.browse", elsewhere)
    rows.put("user:2", "sales.Invoice.browse", SavedView(name="Not yours"))

    assert [
        (view, entry.name) for view, entry in rows.list_mine("user:1")
    ] == [
        ("catalog.Product.browse", "Cheap items"),
        ("sales.Invoice.browse", "All drafts"),
        ("sales.Invoice.browse", "Overdue invoices"),
    ]
    assert rows.list_mine("user:3") == ()


def test_a_saved_view_survives_a_restart(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{(tmp_path / 'saved-views.db').as_posix()}"
    store = SQLAlchemySavedViewStore(url, mode="managed")
    store.create_schema()
    store.put("user:1", "sales.Invoice.browse", OVERDUE)
    store.dispose()

    restarted = SQLAlchemySavedViewStore(url)
    restarted.validate_schema()
    assert restarted.list("user:1", "sales.Invoice.browse") == (OVERDUE,)
    restarted.dispose()


def test_the_sql_store_defaults_to_no_ddl(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{(tmp_path / 'saved-views.db').as_posix()}"
    store = SQLAlchemySavedViewStore(url)
    assert inspect(store.engine).get_table_names() == []
    with pytest.raises(SchemaManagementError):
        store.create_schema()
    issues = store.schema_issues()
    assert issues and "does not exist" in issues[0].message
    store.dispose()


def _service() -> SavedViewService:
    model = compile_project(INVOICING)
    records = RecordsService(model, InMemoryRepository())
    return SavedViewService(model, records.security, InMemorySavedViewRows())


def _context(*roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal("local:test", roles=frozenset(roles))
    )


def test_the_service_keeps_and_lists_a_valid_view() -> None:
    service = _service()
    clerk = _context("sales_clerk")

    assert service.list(clerk, "sales.Invoice.browse") == ()
    service.put(clerk, "sales.Invoice.browse", OVERDUE)
    assert service.list(clerk, "sales.Invoice.browse") == (OVERDUE,)
    service.delete(clerk, "sales.Invoice.browse", "Overdue invoices")
    assert service.list(clerk, "sales.Invoice.browse") == ()


def test_the_service_catalogue_drops_views_that_no_longer_exist() -> None:
    """A saved view outlives the application changing under it; the
    catalogue answers only for views that are still browses, so a renamed
    or removed view leaves a dormant row rather than a broken tile."""

    service = _service()
    clerk = _context("sales_clerk")
    service.put(clerk, "sales.Invoice.browse", OVERDUE)
    service.rows.put(
        clerk.principal.identifier,
        "gone.View.browse",
        SavedView(name="Orphaned"),
    )

    catalogue = service.list_mine(clerk)

    assert [(view, entry.name) for view, entry in catalogue] == [
        ("sales.Invoice.browse", "Overdue invoices")
    ]


def test_every_refusal_reason_is_named_at_once() -> None:
    service = _service()
    clerk = _context("sales_clerk")
    with pytest.raises(SavedViewError) as refused:
        service.put(
            clerk,
            "sales.Invoice.browse",
            SavedView(
                name="   ",
                named_filter="no_such_filter",
                value_filters={"lines": ("x",), "posted_by": ("y",)},
                sort=(("signed_document", False),),
                columns=(ViewStateColumn(name="lines"),),
            ),
        )
    issues = "\n".join(refused.value.issues)
    assert "name must be 1 to" in issues
    assert "unknown named filter 'no_such_filter'" in issues
    assert "'lines' cannot carry a value filter" in issues
    assert "'posted_by' cannot carry a value filter" in issues
    assert "'signed_document' cannot be sorted" in issues
    assert "'lines' is a collection" in issues


def test_the_columns_snapshot_may_be_absent() -> None:
    service = _service()
    clerk = _context("sales_clerk")
    entry = SavedView(
        name="Drafts only",
        named_filter="drafts",
        value_filters={},
        sort=(),
        columns=None,
    )
    service.put(clerk, "sales.Invoice.browse", entry)
    assert service.list(clerk, "sales.Invoice.browse") == (entry,)


def test_the_cap_refuses_the_twenty_first_view() -> None:
    service = _service()
    clerk = _context("sales_clerk")
    for index in range(MAX_SAVED_VIEWS):
        service.put(
            clerk,
            "sales.Invoice.browse",
            SavedView(
                name=f"View {index}",
                named_filter=None,
                value_filters={},
                sort=(),
                columns=None,
            ),
        )
    with pytest.raises(SavedViewError) as refused:
        service.put(
            clerk,
            "sales.Invoice.browse",
            SavedView(
                name="One too many",
                named_filter=None,
                value_filters={},
                sort=(),
                columns=None,
            ),
        )
    assert f"at most {MAX_SAVED_VIEWS}" in refused.value.issues[0]
    # Replacing an existing name is not an addition, so it still lands.
    service.put(
        clerk,
        "sales.Invoice.browse",
        SavedView(
            name="View 0",
            named_filter="drafts",
            value_filters={},
            sort=(),
            columns=None,
        ),
    )


@pytest.mark.parametrize("view_name", ["no.such.view", "sales.Invoice.edit"])
def test_only_a_real_browse_view_carries_saved_views(view_name: str) -> None:
    service = _service()
    clerk = _context("sales_clerk")
    with pytest.raises(UnknownSavedViewView):
        service.list(clerk, view_name)
    with pytest.raises(UnknownSavedViewView):
        service.put(clerk, view_name, OVERDUE)
    with pytest.raises(UnknownSavedViewView):
        service.delete(clerk, view_name, "x")
