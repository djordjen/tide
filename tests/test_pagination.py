from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import event

from tide import compile_project
from tide.data import FilterCondition, InMemoryRepository, QuerySpec, SQLAlchemyRepository, SortField
from tide.runtime import Channel, InvalidQueryCursor, Principal, RequestContext
from tide.services import RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
CUSTOMERS = [
    {
        "id": 1,
        "code": "BETA",
        "name": "Beta",
        "email": None,
        "active": True,
        "invoices": [],
    },
    {
        "id": 2,
        "code": "ALPHA-1",
        "name": "Alpha",
        "email": "a@example.test",
        "active": True,
        "invoices": [],
    },
    {
        "id": 3,
        "code": "ALPHA-2",
        "name": "Alpha",
        "email": None,
        "active": True,
        "invoices": [],
    },
    {
        "id": 4,
        "code": "GAMMA",
        "name": "Gamma",
        "email": "b@example.test",
        "active": True,
        "invoices": [],
    },
    {
        "id": 5,
        "code": "HIDDEN",
        "name": "Hidden",
        "email": "hidden@example.test",
        "active": False,
        "invoices": [],
    },
]


def context(identifier: str = "user:clerk") -> RequestContext:
    return RequestContext(
        principal=Principal(identifier, roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )


@pytest.fixture(params=("memory", "sql"))
def paged_runtime(request: pytest.FixtureRequest) -> Iterator[tuple[RecordsService, object]]:
    model = compile_project(INVOICING)
    if request.param == "memory":
        repository: InMemoryRepository | SQLAlchemyRepository = InMemoryRepository()
    else:
        repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
        repository.create_schema()
    repository.seed("crm.Customer", CUSTOMERS)
    records = RecordsService(model, repository)
    yield records, repository
    if isinstance(repository, SQLAlchemyRepository):
        repository.dispose()


def collect_ids(
    records: RecordsService,
    *,
    sort: tuple[SortField, ...],
    limit: int,
) -> list[int]:
    result: list[int] = []
    cursor = None
    while True:
        page = records.query_page(
            "crm.Customer",
            QuerySpec(sort=sort, limit=limit, cursor=cursor),
            context(),
        )
        result.extend(record["id"] for record in page.records)
        if page.next_cursor is None:
            return result
        cursor = page.next_cursor


def test_keyset_pages_are_equivalent_with_duplicate_sort_values(
    paged_runtime: tuple[RecordsService, object],
) -> None:
    records, _repository = paged_runtime
    query = QuerySpec(sort=(SortField("name"),), limit=2)

    first = records.query_page("crm.Customer", query, context())
    assert [record["id"] for record in first.records] == [2, 3]
    assert first.next_cursor is not None
    assert "Alpha" not in first.next_cursor

    second = records.query_page(
        "crm.Customer",
        QuerySpec(
            sort=query.sort,
            limit=query.limit,
            cursor=first.next_cursor,
        ),
        context(),
    )
    assert [record["id"] for record in second.records] == [1, 4]
    assert second.next_cursor is None


def test_nullable_sort_order_is_stable_in_both_directions(
    paged_runtime: tuple[RecordsService, object],
) -> None:
    records, _repository = paged_runtime

    ascending = collect_ids(records, sort=(SortField("email"),), limit=1)
    descending = collect_ids(
        records,
        sort=(SortField("email", descending=True),),
        limit=1,
    )

    assert ascending == [2, 4, 1, 3]
    assert descending == [1, 3, 4, 2]


def test_primary_key_order_is_added_when_no_sort_is_requested(
    paged_runtime: tuple[RecordsService, object],
) -> None:
    records, _repository = paged_runtime

    assert collect_ids(records, sort=(), limit=2) == [1, 2, 3, 4]


def test_cursor_is_bound_to_query_shape_and_principal(
    paged_runtime: tuple[RecordsService, object],
) -> None:
    records, _repository = paged_runtime
    first = records.query_page(
        "crm.Customer",
        QuerySpec(sort=(SortField("name"),), limit=1),
        context(),
    )
    assert first.next_cursor is not None

    invalid_queries = (
        QuerySpec(sort=(SortField("code"),), limit=1, cursor=first.next_cursor),
        QuerySpec(sort=(SortField("name"),), limit=2, cursor=first.next_cursor),
        QuerySpec(
            filters=(FilterCondition("code", "contains", "A"),),
            sort=(SortField("name"),),
            limit=1,
            cursor=first.next_cursor,
        ),
        QuerySpec(sort=(SortField("name"),), limit=1, cursor=first.next_cursor + "x"),
    )
    for query in invalid_queries:
        with pytest.raises(InvalidQueryCursor):
            records.query_page("crm.Customer", query, context())

    with pytest.raises(InvalidQueryCursor):
        records.query_page(
            "crm.Customer",
            QuerySpec(
                sort=(SortField("name"),),
                limit=1,
                cursor=first.next_cursor,
            ),
            context("user:other"),
        )

    elevated_context = RequestContext(
        principal=Principal(
            "user:clerk",
            roles=frozenset({"sales_clerk"}),
            permissions=frozenset({"cursor.extra"}),
        ),
        channel=Channel.TUI,
    )
    with pytest.raises(InvalidQueryCursor):
        records.query_page(
            "crm.Customer",
            QuerySpec(
                sort=(SortField("name"),),
                limit=1,
                cursor=first.next_cursor,
            ),
            elevated_context,
        )


def test_public_queries_reject_internal_or_ambiguous_boundaries(
    paged_runtime: tuple[RecordsService, object],
) -> None:
    records, _repository = paged_runtime

    with pytest.raises(ValueError, match="internal"):
        records.query_page(
            "crm.Customer",
            QuerySpec(sort=(SortField("id"),), after=(1,)),
            context(),
        )
    with pytest.raises(ValueError, match="must not be repeated"):
        records.query_page(
            "crm.Customer",
            QuerySpec(sort=(SortField("name"), SortField("name"))),
            context(),
        )

def test_sql_second_page_uses_a_bound_keyset_predicate(
    paged_runtime: tuple[RecordsService, object],
) -> None:
    records, repository = paged_runtime
    if not isinstance(repository, SQLAlchemyRepository):
        pytest.skip("SQL statement capture applies only to SQLAlchemy")
    statements: list[str] = []

    @event.listens_for(repository.engine, "before_cursor_execute")
    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.upper())

    first = records.query_page(
        "crm.Customer",
        QuerySpec(sort=(SortField("name"),), limit=2),
        context(),
    )
    assert first.next_cursor is not None
    statements.clear()

    records.query_page(
        "crm.Customer",
        QuerySpec(
            sort=(SortField("name"),),
            limit=2,
            cursor=first.next_cursor,
        ),
        context(),
    )

    root_query = next(
        statement
        for statement in statements
        if "FROM CRM_CUSTOMER" in statement and "ORDER BY" in statement
    )
    assert "CASE WHEN" in root_query
    assert " OR " in root_query
    assert "CRM_CUSTOMER.NAME >" in root_query
    assert "LIMIT" in root_query
