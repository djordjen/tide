"""Action parameters over REST: the body is the parameters object.

The transport carries and never judges -- exactly like the report routes.
What these tests pin is the doorway: a declared parameter travels, a
refusal arrives as the house 422 with `action_parameter` issues, and `{}`
keeps meaning what it always meant for an action whose parameters are all
optional.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

import httpx

from tide import compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.runtime import Principal
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

TOKEN = "tide-development-token-that-is-long-enough"
ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"

DRAFT_INVOICE = 2  # seeded as draft with one line


def _app() -> Any:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 15
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    principal = Principal(
        "api:test", roles=frozenset({"sales_clerk", "sales_manager"})
    )
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(TOKEN, principal),
        actions=actions,
    )


@asynccontextmanager
async def _client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://testserver",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        yield client


async def _etag(client: httpx.AsyncClient, identity: int) -> str:
    record = await client.get(f"/api/v1/invoices/{identity}")
    assert record.status_code == 200
    return record.headers["ETag"]


def test_a_declared_parameter_travels_and_lands_on_the_record() -> None:
    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            etag = await _etag(client, DRAFT_INVOICE)
            voided = await client.post(
                f"/api/v1/invoices/{DRAFT_INVOICE}/actions/void",
                json={"reason": "Ordered twice by mistake"},
                headers={
                    "If-Match": etag,
                    "Idempotency-Key": f"test:{uuid4()}",
                },
            )
            assert voided.status_code == 200, voided.text
            record = voided.json()
            assert record["status"] == "cancelled"
            assert record["cancelled_reason"] == "Ordered twice by mistake"
            assert record["cancelled_by"] == "api:test"

    asyncio.run(exercise())


def test_a_refused_payload_is_the_house_422_naming_the_parameter() -> None:
    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            etag = await _etag(client, DRAFT_INVOICE)
            refused = await client.post(
                f"/api/v1/invoices/{DRAFT_INVOICE}/actions/void",
                json={"cause": "typo"},
                headers={
                    "If-Match": etag,
                    "Idempotency-Key": f"test:{uuid4()}",
                },
            )
            assert refused.status_code == 422, refused.text
            body = refused.json()
            assert body["code"] == "validation_failed"
            issues = body["issues"]
            assert {issue["rule"] for issue in issues} == {"action_parameter"}
            messages = "\n".join(issue["message"] for issue in issues)
            assert "unknown action parameter 'cause'" in messages
            assert "action parameter 'reason' is required" in messages

            unchanged = await client.get(f"/api/v1/invoices/{DRAFT_INVOICE}")
            assert unchanged.json()["status"] == "draft"

    asyncio.run(exercise())


def test_an_empty_body_still_serves_an_optional_only_action() -> None:
    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            etag = await _etag(client, DRAFT_INVOICE)
            posted = await client.post(
                f"/api/v1/invoices/{DRAFT_INVOICE}/actions/post",
                json={},
                headers={
                    "If-Match": etag,
                    "Idempotency-Key": f"test:{uuid4()}",
                },
            )
            assert posted.status_code == 200, posted.text
            assert posted.json()["status"] == "posted"

    asyncio.run(exercise())


def test_an_optional_datetime_parameter_backdates_a_post() -> None:
    app = _app()

    async def exercise() -> None:
        async with _client(app) as client:
            etag = await _etag(client, DRAFT_INVOICE)
            posted = await client.post(
                f"/api/v1/invoices/{DRAFT_INVOICE}/actions/post",
                json={"occurred_at": "2026-07-01T09:30:00+00:00"},
                headers={
                    "If-Match": etag,
                    "Idempotency-Key": f"test:{uuid4()}",
                },
            )
            assert posted.status_code == 200, posted.text
            assert posted.json()["posted_at"] == "2026-07-01T09:30:00Z"

    asyncio.run(exercise())
