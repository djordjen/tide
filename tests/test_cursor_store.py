from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, update

from tide import compile_project
from tide.data import (
    FilterCondition,
    InMemoryRepository,
    QuerySpec,
    SQLAlchemyCursorStore,
    SchemaManagementError,
    SortField,
)
from tide.runtime import (
    Channel,
    CursorStoreError,
    InvalidQueryCursor,
    Principal,
    RequestContext,
)
from tide.services import RecordsService
from tide.services.cursors import CURSOR_VERSION, CursorShape, CursorState

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def cursor_state(
    *, value: object = Decimal("12345678901234567890.123456789")
) -> CursorState:
    occurred_at = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
    return CursorState(
        version=CURSOR_VERSION,
        shape=CursorShape(
            model=("Test", "0.1.0", "0.1"),
            entity="sales.Invoice",
            filters=(
                FilterCondition("total", "gte", Decimal("0.000000001")),
                FilterCondition("invoice_date", "eq", date(2026, 7, 15)),
            ),
            sort=(SortField("total", descending=True), SortField("id")),
            limit=25,
            principal=("user:1", ("sales.invoice.read",)),
        ),
        values=(value, occurred_at),
    )


def test_sql_cursor_store_persists_typed_state_without_raw_token(
    tmp_path: Path,
) -> None:
    database = tmp_path / "shared-cursors.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    store = SQLAlchemyCursorStore(url, mode="managed")
    assert inspect(store.engine).get_table_names() == []
    store.create_schema()
    store.validate_schema()

    state = cursor_state()
    token = store.issue(state)
    with store.engine.connect() as connection:
        row = connection.execute(select(store.cursor_table)).mappings().one()

    assert token not in repr(dict(row))
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    store.dispose()

    restarted = SQLAlchemyCursorStore(url)
    restarted.validate_schema()
    assert restarted.resolve(token) == state
    restarted.dispose()


def test_records_pagination_continues_after_cursor_store_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "records-cursors.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    repository.seed(
        "crm.Customer",
        [
            {"id": 1, "code": "A", "name": "Alpha", "active": True},
            {"id": 2, "code": "B", "name": "Beta", "active": True},
        ],
    )
    request = RequestContext(
        principal=Principal("user:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )
    store = SQLAlchemyCursorStore(url, mode="managed")
    store.create_schema()
    first_service = RecordsService(model, repository, cursor_store=store)
    first = first_service.query_page(
        "crm.Customer",
        QuerySpec(sort=(SortField("name"),), limit=1),
        request,
    )
    assert [record["name"] for record in first.records] == ["Alpha"]
    assert first.next_cursor is not None
    store.dispose()

    restarted = SQLAlchemyCursorStore(url)
    second_service = RecordsService(model, repository, cursor_store=restarted)
    second = second_service.query_page(
        "crm.Customer",
        QuerySpec(
            sort=(SortField("name"),),
            limit=1,
            cursor=first.next_cursor,
        ),
        request,
    )

    assert [record["name"] for record in second.records] == ["Beta"]
    assert second.next_cursor is None
    restarted.dispose()


def test_sql_cursor_store_expires_and_bounds_entries() -> None:
    now = [datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)]
    tokens = iter(("token-1", "token-2", "token-3"))
    store = SQLAlchemyCursorStore(
        "sqlite+pysqlite:///:memory:",
        mode="managed",
        ttl_seconds=10,
        max_entries=2,
        clock=lambda: now[0],
        token_factory=lambda: next(tokens),
    )
    store.create_schema()

    first = store.issue(cursor_state(value=1))
    now[0] += timedelta(seconds=1)
    second = store.issue(cursor_state(value=2))
    now[0] += timedelta(seconds=1)
    third = store.issue(cursor_state(value=3))

    with pytest.raises(InvalidQueryCursor):
        store.resolve(first)
    assert store.resolve(second).values[0] == 2
    assert store.resolve(third).values[0] == 3

    now[0] += timedelta(seconds=10)
    with pytest.raises(InvalidQueryCursor, match="invalid or expired"):
        store.resolve(third)
    assert store.purge_expired() == 1
    store.dispose()


def test_sql_cursor_store_rejects_corrupted_state() -> None:
    store = SQLAlchemyCursorStore(
        "sqlite+pysqlite:///:memory:",
        mode="managed",
        token_factory=lambda: "corrupted-token",
    )
    store.create_schema()
    token = store.issue(cursor_state())
    with store.engine.begin() as connection:
        connection.execute(update(store.cursor_table).values(state_json="not-json"))

    with pytest.raises(CursorStoreError, match="stored query cursor state is invalid"):
        store.resolve(token)
    store.dispose()


def test_sql_cursor_store_bounds_serialized_state_size() -> None:
    store = SQLAlchemyCursorStore(
        "sqlite+pysqlite:///:memory:",
        mode="managed",
        max_state_bytes=128,
    )
    store.create_schema()

    with pytest.raises(ValueError, match="serialized cursor state exceeds 128 bytes"):
        store.issue(cursor_state(value="x" * 1_000))
    with store.engine.connect() as connection:
        assert connection.execute(select(store.cursor_table)).first() is None
    store.dispose()


def test_sql_cursor_store_defaults_to_no_ddl() -> None:
    store = SQLAlchemyCursorStore("sqlite+pysqlite:///:memory:")

    assert inspect(store.engine).get_table_names() == []
    with pytest.raises(SchemaManagementError):
        store.create_schema()
    assert len(store.schema_issues()) == 1
    assert inspect(store.engine).get_table_names() == []
    store.dispose()
