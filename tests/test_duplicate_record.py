"""Duplicate record: a new unsaved draft of what a person could have typed.

The rule lives once, in the records service: writable scalars, chosen
references and owned collection rows copy; identity, readonly and
system/action-written fields, computed values, file bytes, and anything
field security protects do not. The draft then goes through the ordinary
create path, so defaults, generators, validation and authorization apply
to it exactly as to any new record.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tide import compile_project
from tide.data import InMemoryRepository
from tide.runtime import Channel, Principal, RequestContext
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def _runtime() -> RecordsService:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    return records


def _context(*roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal("user:copy", roles=frozenset(roles)),
        channel=Channel.TUI,
        correlation_id="duplicate",
    )


def test_a_duplicate_draft_is_what_a_person_could_have_typed() -> None:
    records = _runtime()
    context = _context("sales_clerk")
    source = records.get("sales.Invoice", 1, context)  # posted, one line

    draft = records.duplicate_draft("sales.Invoice", 1, context)

    assert draft["invoice_date"] == source["invoice_date"]
    assert draft["currency"] == "EUR"
    assert draft["customer"] == source["customer"]
    # The owned rows travel without their identities or stored results.
    assert [set(row) for row in draft["lines"]] == [
        {"line_number", "description", "quantity", "unit_price", "product"}
    ]
    assert draft["lines"][0]["quantity"] == Decimal("10")
    # Nothing the system or the workflow owns is in the draft: the new
    # record allocates its own number, starts in the default state, and
    # holds no stamps, no version, no file.
    assert set(draft) == {"invoice_date", "currency", "customer", "lines"}


def test_the_draft_saves_as_a_genuinely_new_record() -> None:
    records = _runtime()
    context = _context("sales_clerk")
    source = records.get("sales.Invoice", 1, context)
    draft = records.duplicate_draft("sales.Invoice", 1, context)

    session = records.create("sales.Invoice", context, draft)
    stored = records.commit(session, context)

    assert stored["id"] != 1
    assert stored["number"] != source["number"]
    assert stored["status"] == "draft"
    assert stored["posted_at"] is None
    assert stored["total"] == source["total"]  # recomputed from copied lines
    # The rows are the new record's own: editing the duplicate must never
    # reach through to the original.
    assert len(stored["lines"]) == 1
    assert stored["lines"][0]["quantity"] == source["lines"][0]["quantity"]
    session = records.begin_edit("sales.Invoice", stored["id"], context)
    session.set(
        "lines",
        [{**stored["lines"][0], "quantity": Decimal("99")}],
    )
    records.commit(session, context)
    assert records.get("sales.Invoice", 1, context)["lines"][0][
        "quantity"
    ] == Decimal("10")


def test_a_protected_collection_never_travels() -> None:
    records = _runtime()
    reader = _context("summary_viewer")  # sales.invoice.read only

    draft = records.duplicate_draft("sales.Invoice", 1, reader)

    assert "lines" not in draft
    assert set(draft) == {"invoice_date", "currency", "customer"}


def test_the_rest_door_hands_the_draft_and_the_round_trip_creates() -> None:
    import asyncio

    import httpx

    from tide.api.server import (
        DevelopmentTokenAuthenticator,
        build_fastapi_app,
    )

    token = "tide-development-token-that-is-long-enough"
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    app = build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            token, Principal("api:test", roles=frozenset({"sales_clerk"}))
        ),
        actions=actions,
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://testserver",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            answered = await client.get("/api/v1/invoices/1/duplicate-draft")
            assert answered.status_code == 200, answered.text
            values = answered.json()["values"]
            assert values["currency"] == "EUR"
            assert values["customer"] == 1
            assert values["lines"][0]["quantity"] == "10"
            assert "number" not in values
            assert "status" not in values

            created = await client.post("/api/v1/invoices", json=values)
            assert created.status_code == 201, created.text
            record = created.json()
            assert record["status"] == "draft"
            assert record["number"] != "INV-2026-0001"
            assert record["total"] == "850.00"

            missing = await client.get(
                "/api/v1/invoices/999/duplicate-draft"
            )
            assert missing.status_code == 404

    asyncio.run(exercise())
