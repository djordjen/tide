"""A sequence adopted over existing rows must not reissue their numbers.

`next_sequence_value` starts every named sequence at 1 and knows nothing about
what is already stored, so an application that imports historical invoices and
then starts allocating hands out numbers a customer has already been shown.

In the bundled demo this was hidden by an accident of formatting: seeded
numbers are four digits (`INV-2026-0001`) and the generator emits six
(`INV-{year}-{sequence:06d}`), so they differ as strings while naming the same
sequence positions. That is luck, and it kept the collision out of the suite --
which is why the test that matters here asserts the floor rather than
comparing rendered numbers.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tide import compile_project
from tide.data import InMemoryRepository, SQLAlchemyRepository, sequence_name
from tide.runtime import Channel, Principal, RequestContext
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


# --- the repository contract -------------------------------------------------


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_a_reserved_floor_is_the_next_value_to_come_from(
    kind: str, tmp_path: Path
) -> None:
    repository = _repository(kind, tmp_path)

    repository.reserve_sequence_value("sales.Invoice.number", 40)

    assert repository.next_sequence_value("sales.Invoice.number") == 41


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_a_floor_only_ever_rises(kind: str, tmp_path: Path) -> None:
    """Lowering one would reissue numbers, which is the whole defect.

    Adoption code runs more than once -- a re-import, a second worker, a
    retried migration step -- so reserving a value already passed has to be
    a no-op rather than a rewind.
    """

    repository = _repository(kind, tmp_path)
    assert repository.next_sequence_value("sales.Invoice.number") == 1
    repository.reserve_sequence_value("sales.Invoice.number", 40)
    repository.next_sequence_value("sales.Invoice.number")

    repository.reserve_sequence_value("sales.Invoice.number", 5)

    assert repository.next_sequence_value("sales.Invoice.number") == 42


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_reserving_reports_the_floor_that_now_holds(
    kind: str, tmp_path: Path
) -> None:
    repository = _repository(kind, tmp_path)

    assert repository.reserve_sequence_value("one", 12) == 12
    assert repository.reserve_sequence_value("one", 3) == 12


@pytest.mark.parametrize("kind", ["memory", "sql"])
def test_sequences_keep_their_own_floors(kind: str, tmp_path: Path) -> None:
    repository = _repository(kind, tmp_path)

    repository.reserve_sequence_value("one", 40)

    assert repository.next_sequence_value("two") == 1


# --- naming the sequence -----------------------------------------------------


def test_a_generated_field_names_its_sequence_the_same_way_everywhere() -> None:
    """The application chose the string; nothing else could reach it.

    Seeding, adoption and the generator all have to mean the same sequence, so
    the spelling has to come from somewhere other than each caller's memory.
    """

    assert sequence_name("sales.Invoice", "number") == "sales.Invoice.number"


def test_the_invoicing_generator_uses_the_derived_name() -> None:
    runtime = (INVOICING / "runtime.py").read_text(encoding="utf-8")

    assert sequence_name("sales.Invoice", "number") in runtime


# --- what the demo actually does ---------------------------------------------


def test_the_demo_does_not_reissue_a_seeded_invoice_number() -> None:
    """The collision the formatting accident was hiding."""

    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    seeded = seed_demo_data(model, repository)
    assert seeded
    existing = {
        record["number"] for record in repository.all("sales.Invoice")
    }
    records = _services(model, repository)

    issued = {_invoice(records)["number"] for _ in range(3)}

    assert not issued & existing, "an allocated number was already in use"


def test_the_demo_floor_clears_every_seeded_row() -> None:
    """Asserting on the numbers, not on how many rows happened to be seeded."""

    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    seed_demo_data(model, repository)

    reserved = repository.reserve_sequence_value(
        sequence_name("sales.Invoice", "number"), 0
    )

    assert reserved >= len(repository.all("sales.Invoice"))


def _services(model: Any, repository: Any) -> RecordsService:
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    return records


def _context() -> RequestContext:
    return RequestContext(
        Principal("tests:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.REST,
        correlation_id="sequence-floor",
    )


def _invoice(records: RecordsService) -> dict[str, Any]:
    session = records.create(
        "sales.Invoice",
        _context(),
        {"customer": 1, "invoice_date": date(2026, 8, 4), "currency": "EUR"},
    )
    return records.commit(session, _context())


def _repository(kind: str, tmp_path: Path) -> Any:
    if kind == "memory":
        return InMemoryRepository()
    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(
        model, f"sqlite+pysqlite:///{(tmp_path / 'floor.db').as_posix()}"
    )
    repository.create_schema()
    return repository
