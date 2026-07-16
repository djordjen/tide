from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tide import compile_project
from tide.api.client import (
    TideApiClient,
    TideApiClientError,
    TideApiContractError,
    TideApiTransportError,
)
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.data import FilterCondition, QuerySpec, SortField
from tide.runtime import Principal
from tide.runtime.application import configure_application_runtime
from tide.reporting import render_html, render_pdf
from tide.security import PROTECTED
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-client-test-token-that-is-long-enough"
BASE_URL = "http://127.0.0.1"


def test_client_connects_and_reports_server_authorized_capabilities() -> None:
    model, app = _app("sales_clerk")

    with _http_client(app) as transport:
        client = TideApiClient(model, BASE_URL, TOKEN, http_client=transport)
        session = client.connect()

    assert session.principal == "api:client-test"
    assert session.roles == ("sales_clerk",)
    assert session.application == "TIDE Invoicing"
    assert session.application_version == "0.1.0"
    assert session.schema_version == "0.1"
    assert session.entities["sales.Invoice"].operations == (
        "list",
        "get",
        "create",
        "update",
    )
    assert "status" in session.entities["sales.Invoice"].readable_fields
    assert "status" not in session.entities["sales.Invoice"].writable_fields
    assert session.entities["sales.Invoice"].actions == ("post",)
    assert session.entities["sales.InvoiceLine"].operations == ()
    assert session.entities["sales.InvoiceLine"].draft_operations == (
        "create",
        "update",
    )


def test_client_round_trips_types_mutations_versions_and_actions() -> None:
    model, app = _app("sales_clerk")

    with _http_client(app) as transport:
        client = TideApiClient(model, BASE_URL, TOKEN, http_client=transport)
        client.connect()

        page = client.list_records("sales.Invoice", limit=2)
        filtered = client.query_records(
            "sales.Invoice",
            QuerySpec(
                filters=(FilterCondition("status", "eq", "draft"),),
                sort=(SortField("invoice_date", descending=True),),
                limit=3,
            ),
        )
        selected_line = client.apply_reference_selection(
            "sales.InvoiceLine",
            "product",
            {
                "line_number": 1,
                "description": "",
                "quantity": Decimal("1.000"),
                "unit_price": Decimal("0.00"),
                "product": None,
            },
            1,
        )
        report = client.build_report_for_record("sales.invoice", 1)
        product = client.create_record(
            "catalog.Product",
            {
                "code": "REMOTE",
                "name": "Remote client product",
                "unit_price": Decimal("29.95"),
                "active": True,
            },
        )
        created = client.create_record(
            "sales.Invoice",
            {
                "invoice_date": date(2026, 7, 16),
                "currency": "EUR",
                "customer": 1,
                "lines": [
                    {
                        "line_number": 1,
                        "product": 1,
                        "description": "Remote line",
                        "unit_price": Decimal("25.00"),
                        "quantity": Decimal("2.000"),
                    }
                ],
            },
        )
        updated = client.update_record(
            "sales.Invoice",
            created.values["id"],
            {"currency": "USD"},
            if_match=created.etag,
        )
        posted = client.execute_action(
            "sales.Invoice",
            "post",
            created.values["id"],
            if_match=updated.etag,
            idempotency_key="remote-client-post",
        )

        with pytest.raises(TideApiClientError) as stale:
            client.update_record(
                "sales.Invoice",
                created.values["id"],
                {"currency": "GBP"},
                if_match=created.etag,
            )

    assert len(page.records) == 2
    assert page.next_cursor
    assert page.records[0]["invoice_date"] == date(2026, 7, 1)
    assert page.records[0]["total"] == Decimal("850.00")
    assert page.records[0]["lines"][0]["quantity"] == Decimal("10")
    assert len(filtered.records) == 3
    assert all(record["status"] == "draft" for record in filtered.records)
    assert filtered.records[0]["invoice_date"] >= filtered.records[1]["invoice_date"]
    assert selected_line["product"] == 1
    assert selected_line["description"] == "Consulting hour"
    assert selected_line["unit_price"] == Decimal("85.00")
    assert report.report == "sales.invoice"
    assert report.suggested_filename == "invoice-INV-2026-0001"
    assert report.detail.rows[0][-1].text == "850.00"
    assert "INV-2026-0001" in render_html(report)
    assert render_pdf(report).startswith(b"%PDF-")
    assert product.values["unit_price"] == Decimal("29.95")
    assert product.etag is None
    assert created.values["total"] == Decimal("50.00")
    assert created.etag == '"1"'
    assert updated.values["currency"] == "USD"
    assert updated.etag == '"2"'
    assert posted.values["status"] == "posted"
    assert posted.etag == '"3"'
    assert stale.value.status_code == 412
    assert stale.value.code == "stale_version"


def test_client_restores_protected_values_without_confusing_them_with_null() -> None:
    model, app = _app("summary_viewer")

    with _http_client(app) as transport:
        client = TideApiClient(model, BASE_URL, TOKEN, http_client=transport)
        session = client.connect()
        invoice = client.get_record("sales.Invoice", 2)
        with pytest.raises(TideApiClientError) as protected_query:
            client.query_records(
                "sales.Invoice",
                QuerySpec(
                    filters=(
                        FilterCondition("total", "gt", Decimal("100.00")),
                    ),
                ),
            )
        with pytest.raises(TideApiClientError) as denied_report:
            client.build_report_for_record("sales.invoice", 1)

    assert session.entities["sales.Invoice"].operations == ("list", "get")
    assert session.entities["sales.Invoice"].writable_fields == ()
    assert session.entities["sales.Invoice"].actions == ()
    assert invoice.values["lines"] is PROTECTED
    assert invoice.values["posted_by"] is PROTECTED
    assert invoice.values["total"] is PROTECTED
    assert invoice.values["posted_at"] is None
    assert protected_query.value.status_code == 403
    assert protected_query.value.code == "forbidden"
    assert denied_report.value.status_code == 403
    assert denied_report.value.code == "forbidden"


def test_client_fails_closed_for_contract_and_transport_hazards() -> None:
    model, app = _app("sales_clerk")

    with pytest.raises(ValueError, match="only for loopback"):
        TideApiClient(model, "http://example.com", TOKEN)
    with pytest.raises(ValueError, match="must not contain credentials"):
        TideApiClient(model, "https://user:secret@example.com", TOKEN)

    mismatched = replace(model, version="99.0.0")
    with _http_client(app) as transport:
        client = TideApiClient(mismatched, BASE_URL, TOKEN, http_client=transport)
        with pytest.raises(TideApiContractError, match="does not match"):
            client.connect()

    def unavailable(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    with httpx.Client(
        base_url=BASE_URL,
        transport=httpx.MockTransport(unavailable),
    ) as transport:
        client = TideApiClient(model, BASE_URL, TOKEN, http_client=transport)
        with pytest.raises(TideApiTransportError, match="ConnectError") as failure:
            client.connect()
    assert TOKEN not in str(failure.value)


def _app(role: str) -> tuple[object, object]:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    app = build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:client-test", roles=frozenset({role})),
        ),
        actions=actions,
    )
    return model, app


def _http_client(app: object) -> httpx.Client:
    def dispatch(request: httpx.Request) -> httpx.Response:
        async def send() -> httpx.Response:
            async with httpx.AsyncClient(
                base_url=BASE_URL,
                transport=httpx.ASGITransport(app=app),
            ) as client:
                response = await client.request(
                    request.method,
                    str(request.url),
                    headers=request.headers,
                    content=request.content,
                )
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=await response.aread(),
                    request=request,
                )

        return asyncio.run(send())

    return httpx.Client(
        base_url=BASE_URL,
        transport=httpx.MockTransport(dispatch),
    )
