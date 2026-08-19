"""Distinct values of one column, for the column filter's checkbox list.

The list is the server's to answer: the browser only ever holds a page,
row policies must hold, and a legacy column can carry more distinct values
than any popup should receive -- so the answer is bounded and says when it
was cut.
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
    SQLAlchemyRepository,
)
from tide.runtime import AuthorizationError, Channel, Principal, RequestContext
from tide.runtime.errors import QueryFieldError
from tide.services import RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"

CUSTOMERS = [
    {
        "id": 1,
        "code": "ACME",
        "name": "ACME Ltd",
        "email": None,
        "active": True,
        "invoices": [],
    },
    {
        "id": 2,
        "code": "MORA",
        "name": "Mora Trade",
        "email": "mora@example.test",
        "active": True,
        "invoices": [],
    },
    {
        "id": 3,
        "code": "HIDDEN",
        "name": "Hidden",
        "email": "hidden@example.test",
        "active": False,
        "invoices": [],
    },
]

INVOICES = [
    {
        "id": index,
        "number": f"INV-2026-{index:04d}",
        "invoice_date": date(2026, 7, index),
        "currency": "EUR",
        "status": status,
        "posted_at": None,
        "posted_by": None,
        "version": 1,
        "customer": customer,
        "total": Decimal("10.00"),
        "lines": [],
    }
    for index, (status, customer) in enumerate(
        [
            ("draft", 1),
            ("draft", 2),
            ("posted", 1),
            ("cancelled", 2),
            ("draft", 1),
        ],
        start=1,
    )
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


def test_distinct_values_answer_ordered_deduplicated_and_policy_bound(
    runtime: tuple[RecordsService, Any],
) -> None:
    records, _repository = runtime

    statuses = records.distinct_values("sales.Invoice", "status", (), context())
    assert statuses.values == (
        ("cancelled", None),
        ("draft", None),
        ("posted", None),
    )
    assert statuses.truncated is False

    # The ask carries the same conditions the page would: under a customer
    # filter, only that customer's statuses answer.
    filtered = records.distinct_values(
        "sales.Invoice",
        "status",
        (FilterCondition("customer", "eq", 2),),
        context(),
    )
    assert filtered.values == (("cancelled", None), ("draft", None))

    # Row policies hold: the inactive customer's email never appears, and a
    # stored null answers as a value of its own, ordered last.
    emails = records.distinct_values("crm.Customer", "email", (), context())
    assert emails.values == (("mora@example.test", None), (None, None))


def test_distinct_reference_values_carry_their_display_names(
    runtime: tuple[RecordsService, Any],
) -> None:
    records, _repository = runtime

    customers = records.distinct_values(
        "sales.Invoice", "customer", (), context()
    )
    assert customers.values == (
        (1, "ACME - ACME Ltd"),
        (2, "MORA - Mora Trade"),
    )


def test_distinct_values_are_bounded_and_say_so(
    runtime: tuple[RecordsService, Any],
) -> None:
    records, _repository = runtime

    bounded = records.distinct_values(
        "sales.Invoice", "status", (), context(), limit=2
    )
    assert len(bounded.values) == 2
    assert bounded.truncated is True


def test_distinct_asks_are_validated_like_any_read(
    runtime: tuple[RecordsService, Any],
) -> None:
    records, _repository = runtime

    with pytest.raises(QueryFieldError):
        records.distinct_values("sales.Invoice", "ghost", (), context())
    with pytest.raises(AuthorizationError):
        records.distinct_values("sales.Invoice", "posted_by", (), context())
    with pytest.raises(ValueError, match="not stored"):
        records.distinct_values("sales.Invoice", "lines", (), context())
    # The carried filters are normalized the way a page's are.
    with pytest.raises(ValueError, match="must be a integer"):
        records.distinct_values(
            "sales.Invoice",
            "status",
            (FilterCondition("customer", "eq", "x"),),
            context(),
        )
