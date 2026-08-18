"""Caller-requested aggregates on `RecordsService.query_page`.

A summary answers for the whole filtered set -- the same criteria, search and
row policies as the page, never the visible slice -- so the footer a renderer
draws from it is true however far the caller has paged.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import pytest

from tide import compile_project
from tide.data import (
    FilterCondition,
    InMemoryRepository,
    QuerySpec,
    SQLAlchemyRepository,
    SummaryRequest,
)
from tide.runtime import AuthorizationError, Channel, Principal, RequestContext
from tide.runtime.errors import QueryFieldError
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

INVOICES = [
    {
        "id": 1,
        "number": "INV-2026-0001",
        "invoice_date": date(2026, 7, 1),
        "currency": "EUR",
        "status": "draft",
        "posted_at": None,
        "posted_by": None,
        "version": 1,
        "customer": 1,
        "total": Decimal("10.00"),
        "lines": [],
    },
    {
        "id": 2,
        "number": "INV-2026-0002",
        "invoice_date": date(2026, 7, 15),
        "currency": "EUR",
        "status": "draft",
        "posted_at": None,
        "posted_by": None,
        # The SQL write path owns the concurrency token and stores 1 on
        # insert whatever a seed says, so the seed says what is stored.
        "version": 1,
        "customer": 2,
        "total": Decimal("20.00"),
        "lines": [],
    },
    {
        "id": 3,
        "number": "INV-2026-0003",
        "invoice_date": date(2026, 8, 2),
        "currency": "EUR",
        "status": "posted",
        "posted_at": None,
        "posted_by": None,
        "version": 1,
        "customer": 2,
        "total": Decimal("25.00"),
        "lines": [],
    },
]


def context(*roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal(
            "user:clerk", roles=frozenset(roles or ("sales_clerk",))
        ),
        channel=Channel.TUI,
    )


@pytest.fixture(params=("memory", "sql"))
def runtime(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[RecordsService, Any]]:
    model = compile_project(INVOICING)
    repository: InMemoryRepository | SQLAlchemyRepository
    if request.param == "memory":
        repository = InMemoryRepository()
    else:
        repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
        repository.create_schema()
    repository.seed("crm.Customer", CUSTOMERS)
    repository.seed("sales.Invoice", INVOICES)
    records = RecordsService(model, repository)
    yield records, repository
    if isinstance(repository, SQLAlchemyRepository):
        repository.dispose()


def test_summaries_cover_the_whole_filtered_set_not_the_page(
    runtime: tuple[RecordsService, Any],
) -> None:
    records, _repository = runtime

    page = records.query_page(
        "sales.Invoice",
        QuerySpec(
            limit=1,
            summaries=(
                SummaryRequest("total", "sum"),
                SummaryRequest("number", "count"),
            ),
        ),
        context(),
    )

    assert len(page.records) == 1
    assert page.summaries == (
        (SummaryRequest("total", "sum"), Decimal("55.00")),
        (SummaryRequest("number", "count"), 3),
    )
    assert page.next_cursor is not None

    # The summary request is not part of the cursor's shape: the next page
    # continues whether or not it asks again, and a page that does not ask
    # carries nothing.
    second = records.query_page(
        "sales.Invoice",
        QuerySpec(limit=1, cursor=page.next_cursor),
        context(),
    )
    assert second.summaries == ()


def test_summaries_respect_filters_and_row_policies(
    runtime: tuple[RecordsService, Any],
) -> None:
    records, _repository = runtime

    unfiltered = records.query_page(
        "crm.Customer",
        QuerySpec(limit=2, summaries=(SummaryRequest("id", "count"),)),
        context(),
    )
    # Five rows are seeded; the inactive one is not the caller's to count.
    assert unfiltered.summaries == ((SummaryRequest("id", "count"), 4),)

    filtered = records.query_page(
        "crm.Customer",
        QuerySpec(
            filters=(FilterCondition("code", "contains", "ALPHA"),),
            limit=10,
            summaries=(SummaryRequest("id", "count"),),
        ),
        context(),
    )
    assert filtered.summaries == ((SummaryRequest("id", "count"), 2),)


def test_count_counts_values_and_an_empty_set_is_honest(
    runtime: tuple[RecordsService, Any],
) -> None:
    records, _repository = runtime

    emails = records.query_page(
        "crm.Customer",
        QuerySpec(limit=10, summaries=(SummaryRequest("email", "count"),)),
        context(),
    )
    # A count answers for values, not rows: two of the four visible
    # customers carry an email.
    assert emails.summaries == ((SummaryRequest("email", "count"), 2),)

    nothing = records.query_page(
        "sales.Invoice",
        QuerySpec(
            filters=(FilterCondition("number", "eq", "INV-9999-0000"),),
            limit=10,
            summaries=(
                SummaryRequest("total", "sum"),
                SummaryRequest("number", "count"),
                SummaryRequest("invoice_date", "min"),
                SummaryRequest("total", "avg"),
            ),
        ),
        context(),
    )
    assert nothing.summaries == (
        (SummaryRequest("total", "sum"), None),
        (SummaryRequest("number", "count"), 0),
        (SummaryRequest("invoice_date", "min"), None),
        (SummaryRequest("total", "avg"), None),
    )


def test_avg_is_sum_over_count_at_the_field_scale(
    runtime: tuple[RecordsService, Any],
) -> None:
    records, _repository = runtime

    page = records.query_page(
        "sales.Invoice",
        QuerySpec(limit=10, summaries=(SummaryRequest("total", "avg"),)),
        context(),
    )

    # 55.00 / 3, quantized half-even to the field's declared scale of 2 --
    # one contract on both repositories, with no dialect AVG in between.
    assert page.summaries == (
        (SummaryRequest("total", "avg"), Decimal("18.33")),
    )

    drafts = records.query_page(
        "sales.Invoice",
        QuerySpec(
            filters=(FilterCondition("status", "eq", "draft"),),
            limit=10,
            summaries=(SummaryRequest("id", "avg"),),
        ),
        context(),
    )
    # An integer field's mean is still a mean, carried at two places --
    # and computed under the page's own filter.
    assert drafts.summaries == ((SummaryRequest("id", "avg"), Decimal("1.50")),)


def test_min_and_max_walk_dates_and_strings(
    runtime: tuple[RecordsService, Any],
) -> None:
    records, _repository = runtime

    page = records.query_page(
        "sales.Invoice",
        QuerySpec(
            limit=10,
            summaries=(
                SummaryRequest("invoice_date", "min"),
                SummaryRequest("invoice_date", "max"),
                SummaryRequest("number", "min"),
            ),
        ),
        context(),
    )

    assert page.summaries == (
        (SummaryRequest("invoice_date", "min"), date(2026, 7, 1)),
        (SummaryRequest("invoice_date", "max"), date(2026, 8, 2)),
        (SummaryRequest("number", "min"), "INV-2026-0001"),
    )


def test_summary_requests_are_validated(
    runtime: tuple[RecordsService, Any],
) -> None:
    records, _repository = runtime

    def query(*summaries: SummaryRequest) -> QuerySpec:
        return QuerySpec(limit=10, summaries=summaries)

    with pytest.raises(QueryFieldError):
        records.query_page(
            "sales.Invoice", query(SummaryRequest("ghost", "count")), context()
        )
    # posted_by is readable only with sales.invoice.audit; an aggregate over
    # a field the caller cannot read is the same field read.
    with pytest.raises(AuthorizationError):
        records.query_page(
            "sales.Invoice",
            query(SummaryRequest("posted_by", "count")),
            context(),
        )
    audited = records.query_page(
        "sales.Invoice",
        query(SummaryRequest("posted_by", "count")),
        context("auditor"),
    )
    assert audited.summaries == ((SummaryRequest("posted_by", "count"), 0),)
    with pytest.raises(ValueError, match="not stored"):
        records.query_page(
            "sales.Invoice", query(SummaryRequest("lines", "count")), context()
        )
    with pytest.raises(ValueError, match="cannot summarize"):
        records.query_page(
            "sales.Invoice", query(SummaryRequest("number", "sum")), context()
        )
    with pytest.raises(ValueError, match="unknown summary function"):
        records.query_page(
            "sales.Invoice", query(SummaryRequest("total", "median")), context()
        )
    # Distinct functions over one field are legitimate -- min and max of a
    # date is a period -- so only an exact duplicate is a repeated request.
    with pytest.raises(ValueError, match="repeated"):
        records.query_page(
            "sales.Invoice",
            query(
                SummaryRequest("total", "sum"),
                SummaryRequest("total", "sum"),
            ),
            context(),
        )
