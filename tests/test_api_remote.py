from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tide import compile_project
from tide.api.client import TideApiClient, TideApiClientError
from tide.api.remote import (
    RemoteActionService,
    RemoteAuditHistoryService,
    RemoteRecordsService,
    RemoteReportService,
)
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import FilterCondition, InMemoryRepository, QuerySpec, SortField
from tide.runtime import AuthorizationError, Channel, Principal, RequestContext
from tide.runtime.application import configure_application_runtime
from tide.services import (
    ActionAuditEvent,
    ActionService,
    AuditValueMode,
    RecordAuditEvent,
    RecordsService,
)
from tide.tui import seed_demo_data
from tide.tui import TideApp
from tide.tui.conflict import ConflictReviewScreen
from tide.tui.form import RecordEditScreen
from tide.tui.report import ReportPreviewScreen
from tide.tui.confirm import DeleteConfirmationScreen
from textual.widgets import Button, DataTable, Input, Select

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-remote-facade-test-token-long-enough"
BASE_URL = "http://127.0.0.1"


def test_remote_audit_history_uses_server_capabilities_and_safe_contract() -> None:
    model, app = _app("auditor")
    app.state.tide.actions.execute(
        "sales.Invoice",
        "post",
        2,
        {},
        RequestContext(
            Principal("remote:clerk", roles=frozenset({"sales_clerk"})),
            channel=Channel.REST,
            correlation_id="remote-audit-post",
        ),
        idempotency_key="remote-audit-post-2",
    )

    with _http_client(app) as transport:
        client = TideApiClient(model, BASE_URL, TOKEN, http_client=transport)
        session = client.connect()
        context = RequestContext(
            Principal(session.principal, roles=frozenset(session.roles)),
            channel=Channel.TUI,
        )
        audits = RemoteAuditHistoryService(client, session)

        assert audits.can_view("sales.Invoice", context)
        events = audits.for_record("sales.Invoice", 2, context)
        assert len(events) == 2
        action = next(event for event in events if isinstance(event, ActionAuditEvent))
        change = next(event for event in events if isinstance(event, RecordAuditEvent))
        assert action.action == "post"
        assert action.principal == "remote:clerk"
        assert action.correlation_id == "remote-audit-post"
        assert change.operation == "update"
        assert change.correlation_id == action.correlation_id
        assert next(item for item in change.changes if item.field == "posted_by").value_mode is (
            AuditValueMode.REDACTED
        )

    denied_model, denied_app = _app("sales_clerk")
    with _http_client(denied_app) as transport:
        client = TideApiClient(
            denied_model,
            BASE_URL,
            TOKEN,
            http_client=transport,
        )
        session = client.connect()
        context = RequestContext(
            Principal(session.principal, roles=frozenset(session.roles)),
            channel=Channel.TUI,
        )
        audits = RemoteAuditHistoryService(client, session)
        assert not audits.can_view("sales.Invoice", context)
        with pytest.raises(AuthorizationError):
            audits.for_record("sales.Invoice", 2, context)


def test_remote_facades_browse_create_edit_lookup_and_post_via_http() -> None:
    model, app = _app("sales_clerk")

    with _http_client(app) as transport:
        client = TideApiClient(model, BASE_URL, TOKEN, http_client=transport)
        session_info = client.connect()
        context = RequestContext(
            Principal(session_info.principal),
            channel=Channel.TUI,
        )
        records = RemoteRecordsService(model, client, session_info)
        actions = RemoteActionService(client)

        assert "create" in session_info.entities[
            "sales.InvoiceLine"
        ].draft_operations
        page = records.query_page(
            "sales.Invoice",
            QuerySpec(
                filters=(FilterCondition("status", "eq", "draft"),),
                sort=(SortField("invoice_date", descending=True),),
                limit=2,
            ),
            context,
        )
        assert len(page.records) == 2
        assert all(record["status"] == "draft" for record in page.records)

        invoice = records.create("sales.Invoice", context)
        line = records.create("sales.InvoiceLine", context).values
        line.update(
            line_number=1,
            product=None,
            description="",
            quantity=Decimal("2.000"),
            unit_price=Decimal("0.00"),
        )
        selected = records.apply_reference_selection(
            "sales.InvoiceLine",
            "product",
            line,
            1,
            context,
        )
        invoice.set("customer", 1)
        invoice.set("currency", "EUR")
        invoice.set("lines", [selected])

        created = records.commit(invoice, context)
        assert created["number"] == "INV-2026-000009"
        assert created["total"] == Decimal("170.00")
        assert invoice.expected_version == 1

        edit = records.begin_edit("sales.Invoice", created["id"], context)
        edit.set("currency", "USD")
        updated = records.commit(edit, context)
        assert updated["currency"] == "USD"
        assert edit.expected_version == 2

        posted = actions.execute(
            "sales.Invoice",
            "post",
            created["id"],
            {},
            context,
            idempotency_key="remote-facade-post",
            expected_version=edit.expected_version,
        )
        assert posted["status"] == "posted"
        assert posted["version"] == 3


def test_remote_capabilities_hide_mutations_and_reports_fail_closed() -> None:
    model, app = _app("summary_viewer")

    with _http_client(app) as transport:
        client = TideApiClient(model, BASE_URL, TOKEN, http_client=transport)
        session_info = client.connect()
        context = RequestContext(
            Principal(session_info.principal),
            channel=Channel.TUI,
        )
        records = RemoteRecordsService(model, client, session_info)
        reports = RemoteReportService(client, session_info)

        assert records.security.can_access_entity(
            model.entity("sales.Invoice"),
            "list",
            context,
        )
        assert not records.security.can_access_entity(
            model.entity("sales.Invoice"),
            "update",
            context,
        )
        assert not reports.can_generate("sales.invoice", context)
        with pytest.raises(AuthorizationError):
            records.begin_edit("sales.Invoice", 1, context)
        with pytest.raises(TideApiClientError) as denied:
            reports.build_for_record("sales.invoice", 1, context)
        assert denied.value.status_code == 403
    assert denied.value.code == "forbidden"


def test_remote_textual_delete_uses_the_secured_http_facade() -> None:
    model, app = _app("sales_clerk")

    async def exercise() -> None:
        with _http_client(app) as transport:
            client = TideApiClient(model, BASE_URL, TOKEN, http_client=transport)
            session_info = client.connect()
            created = client.create_record(
                "catalog.Product",
                {
                    "code": "REMOTE-DELETE",
                    "name": "Remote delete product",
                    "unit_price": Decimal("3.50"),
                    "active": True,
                },
            )
            context = RequestContext(
                Principal(session_info.principal),
                channel=Channel.TUI,
            )
            tide_app = TideApp(
                model,
                RemoteRecordsService(model, client, session_info),
                context,
                actions=RemoteActionService(client),
                page_size=10,
                source_label="remote delete test",
            )
            async with tide_app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                tide_app.query_one("#browse-view", Select).value = (
                    "catalog.Product.browse"
                )
                await pilot.pause()
                table = tide_app.query_one("#records", DataTable)
                table.move_cursor(row=3)
                await pilot.click("#delete-record")
                await pilot.pause()
                assert isinstance(tide_app.screen, DeleteConfirmationScreen)
                await pilot.click("#confirm-delete")
                await pilot.pause()
                assert table.row_count == 3

            with pytest.raises(TideApiClientError) as missing:
                client.get_record("catalog.Product", created.values["id"])
            assert missing.value.status_code == 404

    asyncio.run(exercise())


def test_remote_textual_stale_edit_reviews_server_values() -> None:
    model, app = _app("sales_clerk")

    async def exercise() -> None:
        with _http_client(app) as transport:
            client = TideApiClient(model, BASE_URL, TOKEN, http_client=transport)
            session_info = client.connect()
            context = RequestContext(
                Principal(session_info.principal),
                channel=Channel.TUI,
            )
            records = RemoteRecordsService(model, client, session_info)
            tide_app = TideApp(
                model,
                records,
                context,
                actions=RemoteActionService(client),
                page_size=3,
                source_label="remote conflict test",
            )
            async with tide_app.run_test(size=(120, 36)) as pilot:
                await pilot.pause()
                tide_app.open_record(2)
                await pilot.pause()
                screen = tide_app.screen
                assert isinstance(screen, RecordEditScreen)

                concurrent = records.begin_edit("sales.Invoice", 2, context)
                concurrent.set("currency", "USD")
                records.commit(concurrent, context)

                screen.query_one("#field-currency", Input).value = "GBP"
                screen.action_save()
                await pilot.pause()
                review = tide_app.screen
                assert isinstance(review, ConflictReviewScreen)
                assert review.conflict.conflicting_fields == ("currency",)

                await pilot.click("#use-draft-conflict")
                assert not review.query_one(
                    "#apply-conflict-resolution", Button
                ).disabled
                await pilot.click("#apply-conflict-resolution")
                await pilot.pause()
                reloaded = tide_app.screen
                assert isinstance(reloaded, RecordEditScreen)
                assert reloaded.session.expected_version == 2
                assert reloaded.query_one("#field-currency", Input).value == "GBP"

                reloaded.action_save()
                await pilot.pause()
                stored = client.get_record("sales.Invoice", 2)
                assert stored.values["currency"] == "GBP"
                assert stored.values["version"] == 3

    asyncio.run(exercise())


def test_remote_textual_preview_uses_the_server_report_document(
    tmp_path: Path,
) -> None:
    model, app = _app("sales_clerk")

    async def exercise() -> None:
        with _http_client(app) as transport:
            client = TideApiClient(model, BASE_URL, TOKEN, http_client=transport)
            session_info = client.connect()
            context = RequestContext(
                Principal(
                    session_info.principal,
                    roles=frozenset(session_info.roles),
                ),
                channel=Channel.TUI,
            )
            tide_app = TideApp(
                model,
                RemoteRecordsService(model, client, session_info),
                context,
                actions=RemoteActionService(client),
                report_service=RemoteReportService(client, session_info),
                report_output_directory=tmp_path,
                page_size=3,
                source_label="remote report test",
            )
            async with tide_app.run_test(size=(120, 36)) as pilot:
                await pilot.pause()
                preview = tide_app.query_one("#preview-report", Button)
                assert preview.display
                assert not preview.disabled

                await pilot.click("#preview-report")
                await pilot.pause()
                assert isinstance(tide_app.screen, ReportPreviewScreen)
                assert "INV-2026-0001" in tide_app.screen.document.plain_text()

                await pilot.click("#export-html")
                assert (tmp_path / "invoice-INV-2026-0001.html").is_file()

    asyncio.run(exercise())


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
            Principal("api:remote-test", roles=frozenset({role})),
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

        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, send()).result()

    return httpx.Client(
        base_url=BASE_URL,
        transport=httpx.MockTransport(dispatch),
    )
