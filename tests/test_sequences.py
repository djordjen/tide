"""A number handed to one record is never handed to another.

Invoice numbers were derived from `MAX(primary_key) + 1`, read on its own
connection outside any transaction. Two concurrent creates read the same value
and collided, and deleting the newest record rewound the sequence so the next
invoice was issued a number a customer had already been shown.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tide import compile_project
from tide.data import InMemoryRepository, SQLAlchemyRepository
from tide.runtime import Channel, Principal, RequestContext
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _context() -> RequestContext:
    return RequestContext(
        Principal("tests:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.REST,
        correlation_id="sequences",
    )


def _services(repository: Any) -> RecordsService:
    model = compile_project(INVOICING)
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    return records


def _customer(records: RecordsService) -> Any:
    session = records.create(
        "crm.Customer", _context(), {"code": "SEQ", "name": "Sequence"}
    )
    return records.commit(session, _context())["id"]


def _invoice(records: RecordsService, customer: Any) -> dict[str, Any]:
    session = records.create(
        "sales.Invoice",
        _context(),
        {"customer": customer, "invoice_date": date(2026, 8, 4), "currency": "EUR"},
    )
    return records.commit(session, _context())


# --- the repository contract -------------------------------------------------


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_a_sequence_never_hands_out_a_value_twice(kind: str, tmp_path: Path) -> None:
    repository = _repository(kind, tmp_path)

    values = [repository.next_sequence_value("sales.Invoice.number") for _ in range(5)]

    assert values == [1, 2, 3, 4, 5]


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_sequences_are_independent_of_each_other(kind: str, tmp_path: Path) -> None:
    repository = _repository(kind, tmp_path)

    assert repository.next_sequence_value("one") == 1
    assert repository.next_sequence_value("two") == 1
    assert repository.next_sequence_value("one") == 2


def test_concurrent_callers_never_receive_the_same_value() -> None:
    """The whole point: two creates in flight must not agree on a number."""

    repository = InMemoryRepository()

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(
            pool.map(
                lambda _: repository.next_sequence_value("sales.Invoice.number"),
                range(200),
            )
        )

    assert sorted(values) == list(range(1, 201))


# --- what the application does with it ---------------------------------------


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_a_sequence_is_not_a_row_count(kind: str, tmp_path: Path) -> None:
    """`MAX(pk) + 1` rewound whenever the newest rows went away.

    A number is shown to a customer and kept in the audit history, so it may go
    unused but must never name two different records. Deriving it from what is
    currently stored cannot promise that; a sequence only ever climbs.
    """

    repository = _repository(kind, tmp_path)
    issued = [repository.next_sequence_value("sales.Invoice.number") for _ in range(3)]

    assert repository.peek_next_identity("sales.Invoice") == 1, "no rows exist"
    assert repository.next_sequence_value("sales.Invoice.number") == issued[-1] + 1


def test_numbers_stay_distinct_across_a_run_of_invoices() -> None:
    records = _services(InMemoryRepository())
    customer = _customer(records)

    numbers = [_invoice(records, customer)["number"] for _ in range(5)]

    assert len(set(numbers)) == 5


def _repository(kind: str, tmp_path: Path) -> Any:
    if kind == "memory":
        return InMemoryRepository()
    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(
        model, f"sqlite+pysqlite:///{(tmp_path / 'sequence.db').as_posix()}"
    )
    repository.create_schema()
    return repository
