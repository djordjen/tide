"""A concurrency token TIDE did not write is a version, not a dead end.

Adopted tables hold rows whose token column is NULL -- the reference XAF
store leaves exactly that behind until a row's first save -- and the
service already compares an expected None as IS NULL. But the wire could
not say it: no If-Match value ever matched, so the row was permanently
un-editable over REST and MCP while the TUI edited it freely. And the
memory adapter could not even heal: it added one to a version it never
checked for NULL.

`NULL_VERSION` carries the assertion where `None` already means "nothing
was supplied" at every precondition boundary; the wire spells it
`"null"`, it compares equal to a loaded NULL, and the first successful
write heals the row to version 1 -- after which it is an ordinary row.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tide import compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.compiler.normalized import deep_thaw, immutable_mapping
from tide.data import InMemoryRepository
from tide.runtime import (
    Channel,
    ConcurrencyError,
    Principal,
    RequestContext,
)
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-development-token-that-is-long-enough"


def _legacy_invoice(identity: int) -> dict:
    return {
        "id": identity,
        "number": f"LEG-{identity}",
        "invoice_date": date(2026, 7, 20),
        "currency": "EUR",
        "status": "draft",
        "customer": 1,
        "total": Decimal("0.00"),
    }


def _runtime() -> tuple[RecordsService, ActionService, RequestContext]:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    repository.seed(
        "sales.Invoice", [_legacy_invoice(90), _legacy_invoice(91)]
    )
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    context = RequestContext(
        Principal("user:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )
    return records, actions, context


def test_an_edit_heals_a_null_token_to_version_one() -> None:
    """The session loaded None; the write assigns 1, on every adapter.

    The SQL adapter already healed (`int(expected or 0) + 1`); the memory
    adapter added one to the actual version without the NULL guard and
    crashed on the exact row this whole contract exists for.
    """

    records, _, context = _runtime()

    session = records.begin_edit("sales.Invoice", 90, context)
    assert session.expected_version is None
    session.set("currency", "USD")
    stored = records.commit(session, context)

    assert stored["version"] == 1
    assert stored["currency"] == "USD"


def test_delete_accepts_the_null_version_assertion() -> None:
    from tide.runtime import NULL_VERSION

    records, _, context = _runtime()
    invoice = records.model.entity("sales.Invoice")
    metadata = deep_thaw(invoice.metadata)
    metadata["permissions"]["delete"] = "sales.invoice.write"
    entities = dict(records.model.entities)
    entities[invoice.name] = replace(
        invoice, metadata=immutable_mapping(metadata)
    )
    deleting = RecordsService(
        replace(records.model, entities=immutable_mapping(entities)),
        records.repository,
    )

    deleting.delete(
        "sales.Invoice", 91, context, expected_version=NULL_VERSION
    )
    assert not records.repository.exists("sales.Invoice", 91)

    # A healed row no longer matches the null assertion.
    session = records.begin_edit("sales.Invoice", 90, context)
    session.set("currency", "USD")
    records.commit(session, context)
    with pytest.raises(ConcurrencyError):
        deleting.delete(
            "sales.Invoice", 90, context, expected_version=NULL_VERSION
        )


def test_an_action_matches_and_refuses_the_null_assertion() -> None:
    from tide.runtime import NULL_VERSION

    records, actions, context = _runtime()

    voided = actions.execute(
        "sales.Invoice",
        "void",
        90,
        {"reason": "null assertion probe"},
        context,
        expected_version=NULL_VERSION,
    ).record
    assert voided["status"] == "cancelled"
    assert voided["version"] == 1

    session = records.begin_edit("sales.Invoice", 91, context)
    session.set("currency", "USD")
    records.commit(session, context)
    with pytest.raises(ConcurrencyError):
        actions.execute(
            "sales.Invoice",
            "void",
            91,
            {"reason": "null assertion probe"},
            context,
            expected_version=NULL_VERSION,
        )


def test_the_wire_speaks_null_for_a_token_never_written() -> None:
    records, actions, _ = _runtime()
    app = build_fastapi_app(
        records.model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"sales_clerk"})),
        ),
        actions=actions,
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            read = await client.get("/api/v1/invoices/90", headers=headers)
            healed = await client.patch(
                "/api/v1/invoices/90",
                headers={**headers, "If-Match": '"null"'},
                json={"currency": "USD"},
            )
            reread = await client.get("/api/v1/invoices/90", headers=headers)
            stale = await client.patch(
                "/api/v1/invoices/90",
                headers={**headers, "If-Match": '"null"'},
                json={"currency": "GBP"},
            )
            missing = await client.patch(
                "/api/v1/invoices/90",
                headers=headers,
                json={"currency": "GBP"},
            )
            garbage = await client.patch(
                "/api/v1/invoices/90",
                headers={**headers, "If-Match": '"soon"'},
                json={"currency": "GBP"},
            )

        assert read.status_code == 200
        assert read.headers["etag"] == '"null"'
        assert healed.status_code == 200
        assert healed.headers["etag"] == '"1"'
        assert healed.json()["version"] == 1
        assert reread.headers["etag"] == '"1"'
        assert stale.status_code == 412
        assert stale.json()["code"] == "stale_version"
        assert missing.status_code == 428
        assert garbage.status_code == 400
        assert '"null"' in garbage.json()["message"]

    asyncio.run(exercise())
