from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import uvicorn

from tide import compile_project
from tide.api.auth import OidcJwtAuthenticator
from tide.api.config import (
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
)
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.compiler.normalized import deep_thaw, immutable_mapping
from tide.cli import main
from tide.data import InMemoryRepository
from tide.runtime import Channel, Principal, RequestContext
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-development-token-that-is-long-enough"


def test_server_requires_bearer_auth_and_exposes_docs() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            live = await client.get("/health/live")
            ready = await client.get("/health/ready")
            docs = await client.get("/docs")
            missing = await client.get("/api/v1/invoices")
            session = await client.get(
                "/api/v1/_tide/session",
                headers=_authorization(),
            )
            incorrect = await client.get(
                "/api/v1/invoices",
                headers={"Authorization": "Bearer incorrect-token-value"},
            )

        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "application": "TIDE Invoicing",
            "version": "0.1.0",
        }
        assert docs.status_code == 200
        assert session.status_code == 200
        assert session.json()["authentication"] == "development-bearer"
        assert session.json()["reports"] == ["sales.invoice"]
        invoice_capabilities = session.json()["entities"]["sales.Invoice"]
        assert invoice_capabilities["operations"] == [
            "list",
            "get",
            "create",
            "update",
        ]
        assert set(invoice_capabilities["readable_fields"]) == {
            "id",
            "number",
            "invoice_date",
            "customer",
            "currency",
            "status",
            "lines",
            "posted_at",
            "version",
            "total",
        }
        assert set(invoice_capabilities["writable_fields"]) == {
            "invoice_date",
            "customer",
            "currency",
            "lines",
        }
        assert invoice_capabilities["actions"] == ["post"]
        assert invoice_capabilities["audit"] is False
        for response in (missing, incorrect):
            assert response.status_code == 401
            assert response.json() == {
                "code": "unauthorized",
                "message": "authentication required",
            }
            assert response.headers["www-authenticate"] == "Bearer"
        assert missing.headers["cache-control"] == "no-store"
        assert missing.headers["x-content-type-options"] == "nosniff"

    asyncio.run(exercise())

    schema = app.openapi()
    assert schema["x-tide"] == {
        "runtime": True,
        "read_only": False,
        "wire_version": "0.1",
        "schema_version": "0.1",
        "authentication": "development-bearer",
        "max_request_body_bytes": DEFAULT_MAX_REQUEST_BODY_BYTES,
        "request_body_timeout_seconds": DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
    }
    assert schema["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "description": (
            "Bearer credentials are mapped to a Principal by server configuration; "
            "clients cannot choose their roles or permissions."
        ),
        "scheme": "bearer",
        "bearerFormat": "opaque",
    }
    assert set(schema["paths"]["/health/ready"]["get"]["responses"]) == {
        "200",
        "503",
    }
    assert set(schema["paths"]["/api/v1/invoices"]) == {"get", "post"}
    assert "/api/v1/invoices/{id}" in schema["paths"]
    assert "/api/v1/invoices/{id}/_audit" in schema["paths"]
    assert "/api/v1/customers/{id}/_audit" in schema["paths"]
    assert "/api/v1/products/{id}/_audit" in schema["paths"]
    assert set(schema["paths"]["/api/v1/invoices/{id}"]) == {"get", "patch"}
    assert set(schema["paths"]["/api/v1/products/{id}"]) == {
        "get",
        "patch",
        "delete",
    }
    assert "delete" in schema["paths"]["/api/v1/customers/{id}"]
    assert "delete" in schema["paths"]["/api/v1/customers/{id}"]
    delete_operation = schema["paths"]["/api/v1/products/{id}"]["delete"]
    assert set(delete_operation["responses"]) == {
        "204",
        "400",
        "401",
        "403",
        "404",
        "409",
        "412",
        "422",
        "428",
    }
    assert set(schema["paths"]["/api/v1/invoices/_query"]) == {"post"}
    assert set(schema["paths"]["/api/v1/_tide/reference-selection"]) == {
        "post"
    }
    assert set(
        schema["paths"][
            "/api/v1/_tide/reports/{report_name}/records/{identity}"
        ]
    ) == {"get"}
    assert "/api/v1/invoices/{id}/actions/post" in schema["paths"]
    assert "413" in schema["paths"]["/api/v1/invoices"]["post"]["responses"]
    assert "408" in schema["paths"]["/api/v1/invoices"]["post"]["responses"]
    create_schema = schema["components"]["schemas"]["SalesInvoiceCreateInput"]
    update_schema = schema["components"]["schemas"]["SalesInvoiceUpdateInput"]
    nested_schema = schema["components"]["schemas"]["SalesInvoiceLineNestedInput"]
    assert set(create_schema["properties"]) == {
        "invoice_date",
        "currency",
        "customer",
        "lines",
    }
    assert create_schema["required"] == ["customer"]
    assert "required" not in update_schema
    assert set(nested_schema["required"]) == {
        "line_number",
        "description",
        "quantity",
        "unit_price",
        "product",
    }
    assert "invoice" not in nested_schema["properties"]
    action_parameters = schema["paths"][
        "/api/v1/invoices/{id}/actions/post"
    ]["post"]["parameters"]
    assert {parameter["name"] for parameter in action_parameters} == {
        "id",
        "If-Match",
        "Idempotency-Key",
    }


def test_readiness_fails_closed_without_leaking_dependency_errors() -> None:
    class UnavailableRepository(InMemoryRepository):
        def check_readiness(self) -> None:
            raise RuntimeError("database password=must-not-leak")

    model = compile_project(INVOICING)
    repository = UnavailableRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    logger, log_handler = _recording_logger()
    app = build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"sales_clerk"})),
        ),
        logger=logger,
    )

    async def exercise() -> None:
        async with _client(app) as client:
            live = await client.get("/health/live")
            ready = await client.get("/health/ready")

        assert live.status_code == 200
        assert ready.status_code == 503
        assert ready.json() == {
            "status": "not_ready",
            "application": "TIDE Invoicing",
            "version": "0.1.0",
        }
        assert "password" not in ready.text
        assert ready.headers["cache-control"] == "no-store"

    asyncio.run(exercise())
    readiness_failure = next(
        record
        for record in log_handler.records
        if record.msg == "readiness.failed"
    )
    assert readiness_failure.tide_fields == {
        "channel": "system",
        "correlation_id": readiness_failure.tide_fields["correlation_id"],
        "operation": (
            "test_readiness_fails_closed_without_leaking_dependency_errors."
            "<locals>.UnavailableRepository.check_readiness"
        ),
        "error_type": "RuntimeError",
    }
    assert "password" not in repr(readiness_failure.__dict__)


def test_http_correlation_is_returned_logged_and_shared_with_crud_audit() -> None:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    logger, log_handler = _recording_logger()
    app = build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(
            TOKEN,
            Principal("api:test", roles=frozenset({"sales_clerk"})),
        ),
        actions=actions,
        logger=logger,
    )
    correlation_id = "client.create-product:123"

    async def exercise() -> None:
        async with _client(app) as client:
            created = await client.post(
                "/api/v1/products",
                headers={
                    **_authorization(),
                    "X-Correlation-ID": correlation_id,
                },
                json={
                    "code": "LOG-SECRET-CODE",
                    "name": "Correlation test",
                    "unit_price": "1.00",
                    "active": True,
                },
            )
            regenerated = await client.get(
                "/health/live",
                headers={"X-Correlation-ID": "invalid header value"},
            )

        assert created.status_code == 201
        assert created.headers["x-correlation-id"] == correlation_id
        assert regenerated.status_code == 200
        UUID(regenerated.headers["x-correlation-id"])

    asyncio.run(exercise())

    events = actions.execution_store.record_audit_events(
        correlation_id=correlation_id,
    )
    assert len(events) == 1
    assert events[0].entity == "catalog.Product"
    completed = next(
        record
        for record in log_handler.records
        if record.msg == "http.request.completed"
        and record.tide_fields.get("correlation_id") == correlation_id
    )
    assert completed.tide_fields == {
        "channel": "rest",
        "correlation_id": correlation_id,
        "operation": "createCatalogProduct",
        "method": "POST",
        "status_code": 201,
        "duration_ms": completed.tide_fields["duration_ms"],
    }
    assert TOKEN not in repr(completed.__dict__)
    assert "LOG-SECRET-CODE" not in repr(completed.__dict__)


def test_request_body_limit_rejects_declared_and_streamed_payloads_safely() -> None:
    logger, log_handler = _recording_logger()
    app = _app(
        "sales_clerk",
        logger=logger,
        max_request_body_bytes=96,
    )

    async def streamed_body() -> Any:
        yield b'{"code":"STREAMED",'
        yield b'"name":"' + (b"x" * 100) + b'"}'

    async def exercise() -> None:
        async with _client(app) as client:
            declared = await client.post(
                "/api/v1/products",
                headers={
                    **_authorization(),
                    "X-Correlation-ID": "declared-too-large",
                },
                json={"secret": "must-not-leak" * 20},
            )
            streamed = await client.post(
                "/api/v1/products",
                headers={
                    **_authorization(),
                    "Content-Type": "application/json",
                    "X-Correlation-ID": "streamed-too-large",
                },
                content=streamed_body(),
            )

        for response, correlation_id in (
            (declared, "declared-too-large"),
            (streamed, "streamed-too-large"),
        ):
            assert response.status_code == 413
            assert response.json() == {
                "code": "request_too_large",
                "message": "request body exceeds the configured limit",
            }
            assert response.headers["x-correlation-id"] == correlation_id
            assert response.headers["cache-control"] == "no-store"
            assert "must-not-leak" not in response.text

    asyncio.run(exercise())

    rejected = [
        record
        for record in log_handler.records
        if record.msg == "http.request.completed"
        and record.tide_fields.get("status_code") == 413
    ]
    assert len(rejected) == 2
    assert {record.tide_fields["correlation_id"] for record in rejected} == {
        "declared-too-large",
        "streamed-too-large",
    }
    assert "must-not-leak" not in repr([record.__dict__ for record in rejected])


def test_request_body_receive_timeout_is_safe_and_correlated() -> None:
    logger, log_handler = _recording_logger()
    app = _app(
        "sales_clerk",
        logger=logger,
        request_body_timeout_seconds=1,
    )

    async def slow_body() -> Any:
        await asyncio.sleep(1.1)
        yield b'{"code":"TOO-LATE"}'

    async def exercise() -> None:
        async with _client(app) as client:
            response = await client.post(
                "/api/v1/products",
                headers={
                    **_authorization(),
                    "Content-Type": "application/json",
                    "X-Correlation-ID": "slow-request-body",
                },
                content=slow_body(),
            )

        assert response.status_code == 408
        assert response.json() == {
            "code": "request_timeout",
            "message": (
                "request body was not received within the configured timeout"
            ),
        }
        assert response.headers["x-correlation-id"] == "slow-request-body"

    asyncio.run(exercise())

    completed = log_handler.records[-1]
    assert completed.msg == "http.request.completed"
    assert completed.tide_fields["status_code"] == 408
    assert completed.tide_fields["operation"] == "requestBodyTimeout"


def test_server_lists_gets_and_pages_secured_records() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            first = await client.get(
                "/api/v1/invoices?limit=3",
                headers=_authorization(),
            )
            assert first.status_code == 200
            body = first.json()
            assert len(body["records"]) == 3
            assert body["records"][0]["number"] == "INV-2026-0001"
            assert body["records"][0]["invoice_date"] == "2026-07-01"
            assert body["records"][0]["total"] == "850.00"
            assert body["records"][0]["lines"][0]["quantity"] == "10"
            assert body["next_cursor"]

            second = await client.get(
                "/api/v1/invoices",
                params={"limit": 3, "cursor": body["next_cursor"]},
                headers=_authorization(),
            )
            record = await client.get(
                "/api/v1/invoices/1",
                headers=_authorization(),
            )
            missing = await client.get(
                "/api/v1/invoices/999",
                headers=_authorization(),
            )
            invalid = await client.get(
                "/api/v1/invoices/not-an-integer",
                headers=_authorization(),
            )
            invalid_limit = await client.get(
                "/api/v1/invoices?limit=0",
                headers=_authorization(),
            )

        assert second.status_code == 200
        assert second.json()["records"][0]["number"] == "INV-2026-0004"
        assert record.status_code == 200
        assert record.json()["customer"] == 1
        assert record.headers["etag"] == '"2"'
        assert missing.status_code == 404
        assert missing.json()["code"] == "not_found"
        assert invalid.status_code == 422
        assert invalid.json() == {
            "code": "invalid_request",
            "message": "request validation failed",
        }
        assert invalid_limit.status_code == 422
        assert invalid_limit.json() == {
            "code": "invalid_request",
            "message": "request validation failed",
        }

    asyncio.run(exercise())


def test_server_creates_and_patches_through_records_service() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            created_product = await client.post(
                "/api/v1/products",
                headers=_authorization(),
                json={
                    "code": "API-PRODUCT",
                    "name": "API product",
                    "unit_price": "19.95",
                    "active": True,
                },
            )
            rejected_system_field = await client.post(
                "/api/v1/products",
                headers=_authorization(),
                json={
                    "id": 500,
                    "code": "INVALID",
                    "name": "Invalid",
                    "unit_price": "10.00",
                },
            )
            created_invoice = await client.post(
                "/api/v1/invoices",
                headers=_authorization(),
                json={
                    "invoice_date": "2026-07-16",
                    "currency": "EUR",
                    "customer": 1,
                    "lines": [
                        {
                            "line_number": 1,
                            "description": "Created through the API",
                            "quantity": "2.000",
                            "unit_price": "85.00",
                            "product": 1,
                        }
                    ],
                },
            )
            missing_precondition = await client.patch(
                "/api/v1/invoices/2",
                headers=_authorization(),
                json={"currency": "USD"},
            )
            updated = await client.patch(
                "/api/v1/invoices/2",
                headers={**_authorization(), "If-Match": '"1"'},
                json={"currency": "USD"},
            )
            stale = await client.patch(
                "/api/v1/invoices/2",
                headers={**_authorization(), "If-Match": '"1"'},
                json={"currency": "GBP"},
            )
            protected_input = await client.patch(
                "/api/v1/invoices/2",
                headers={**_authorization(), "If-Match": '"2"'},
                json={"status": "posted"},
            )

        assert created_product.status_code == 201
        assert created_product.json()["id"] == 4
        assert created_product.json()["unit_price"] == "19.95"
        assert created_product.headers["location"] == "/api/v1/products/4"
        assert rejected_system_field.status_code == 422
        assert created_invoice.status_code == 201
        assert created_invoice.json()["number"] == "INV-2026-000009"
        assert created_invoice.json()["total"] == "170.00"
        assert created_invoice.headers["etag"] == '"1"'
        assert created_invoice.headers["location"] == "/api/v1/invoices/9"
        assert missing_precondition.status_code == 428
        assert missing_precondition.json()["code"] == "precondition_required"
        assert updated.status_code == 200
        assert updated.json()["currency"] == "USD"
        assert updated.json()["version"] == 2
        assert updated.headers["etag"] == '"2"'
        assert stale.status_code == 412
        assert stale.json()["code"] == "stale_version"
        assert protected_input.status_code == 422

    asyncio.run(exercise())


def test_server_deletes_only_explicitly_exposed_authorized_records() -> None:
    allowed_app = _app("sales_clerk")
    denied_app = _app("auditor")

    async def exercise() -> None:
        async with _client(allowed_app) as client:
            created = await client.post(
                "/api/v1/products",
                headers=_authorization(),
                json={
                    "code": "DELETE-ME",
                    "name": "Unused product",
                    "unit_price": "1.00",
                    "active": True,
                },
            )
            deleted = await client.delete(
                f"/api/v1/products/{created.json()['id']}",
                headers=_authorization(),
            )
            missing = await client.get(
                f"/api/v1/products/{created.json()['id']}",
                headers=_authorization(),
            )
            restricted = await client.delete(
                "/api/v1/products/1",
                headers=_authorization(),
            )
            invoice_route = await client.delete(
                "/api/v1/invoices/1",
                headers=_authorization(),
            )
        async with _client(denied_app) as client:
            forbidden = await client.delete(
                "/api/v1/products/1",
                headers=_authorization(),
            )

        assert created.status_code == 201
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert missing.status_code == 404
        assert restricted.status_code == 409
        assert restricted.json()["code"] == "delete_restricted"
        assert invoice_route.status_code == 405
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "forbidden"

    asyncio.run(exercise())


def test_server_requires_if_match_for_versioned_delete() -> None:
    app = _app("sales_clerk", model=_invoice_delete_model())

    async def exercise() -> None:
        async with _client(app) as client:
            missing = await client.delete(
                "/api/v1/invoices/8",
                headers=_authorization(),
            )
            stale = await client.delete(
                "/api/v1/invoices/8",
                headers={**_authorization(), "If-Match": '"99"'},
            )
            deleted = await client.delete(
                "/api/v1/invoices/8",
                headers={**_authorization(), "If-Match": '"1"'},
            )
            gone = await client.get(
                "/api/v1/invoices/8",
                headers=_authorization(),
            )

        assert missing.status_code == 428
        assert missing.json()["code"] == "precondition_required"
        assert stale.status_code == 412
        assert stale.json()["code"] == "stale_version"
        assert deleted.status_code == 204
        assert gone.status_code == 404

    asyncio.run(exercise())


def test_server_posts_with_version_and_idempotency_preconditions() -> None:
    app = _app("sales_clerk")

    async def exercise() -> None:
        async with _client(app) as client:
            missing = await client.post(
                "/api/v1/invoices/2/actions/post",
                headers={**_authorization(), "If-Match": '"1"'},
                json={},
            )
            posted = await client.post(
                "/api/v1/invoices/2/actions/post",
                headers={
                    **_authorization(),
                    "If-Match": '"1"',
                    "Idempotency-Key": "test-post-invoice-2",
                },
                json={},
            )
            replay = await client.post(
                "/api/v1/invoices/2/actions/post",
                headers={
                    **_authorization(),
                    "If-Match": '"1"',
                    "Idempotency-Key": "test-post-invoice-2",
                },
                json={},
            )
            stale = await client.post(
                "/api/v1/invoices/8/actions/post",
                headers={
                    **_authorization(),
                    "If-Match": '"99"',
                    "Idempotency-Key": "test-stale-post",
                },
                json={},
            )

        assert missing.status_code == 428
        assert missing.json()["message"] == "Idempotency-Key header is required"
        assert posted.status_code == 200
        assert posted.json()["status"] == "posted"
        assert posted.json()["version"] == 2
        assert posted.headers["etag"] == '"2"'
        assert replay.status_code == 200
        assert replay.json()["version"] == 2
        assert stale.status_code == 412
        assert stale.json()["code"] == "stale_version"

    asyncio.run(exercise())


def test_server_returns_only_authorized_safe_record_audit_history() -> None:
    allowed_app = _app("auditor")
    denied_app = _app("sales_clerk")
    actor = RequestContext(
        Principal("api:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.REST,
        correlation_id="audit-post-first",
    )
    allowed_app.state.tide.actions.execute(
        "sales.Invoice",
        "post",
        2,
        {},
        actor,
        idempotency_key="audit-history-post-2",
    )
    allowed_app.state.tide.actions.execute(
        "sales.Invoice",
        "post",
        2,
        {},
        RequestContext(
            actor.principal,
            channel=Channel.REST,
            correlation_id="audit-post-replay",
        ),
        idempotency_key="audit-history-post-2",
    )

    async def exercise() -> None:
        async with _client(allowed_app) as client:
            session = await client.get(
                "/api/v1/_tide/session",
                headers=_authorization(),
            )
            history = await client.get(
                "/api/v1/invoices/2/_audit?limit=1",
                headers=_authorization(),
            )
            full_history = await client.get(
                "/api/v1/invoices/2/_audit?limit=10",
                headers=_authorization(),
            )
        async with _client(denied_app) as client:
            denied = await client.get(
                "/api/v1/invoices/2/_audit",
                headers=_authorization(),
            )

        assert session.json()["entities"]["sales.Invoice"]["audit"] is True
        assert history.status_code == 200
        body = history.json()
        assert body["entity"] == "sales.Invoice"
        assert body["identity"] == 2
        assert len(body["events"]) == 1
        event = body["events"][0]
        assert event["action"] == "post"
        assert event["outcome"] == "replayed"
        assert event["principal"] == "api:clerk"
        assert event["channel"] == "rest"
        assert event["correlation_id"] == "audit-post-replay"
        assert "idempotency_key_hash" not in event
        assert "audit-history-post-2" not in repr(body)
        complete = full_history.json()["events"]
        assert [item["kind"] for item in complete] == [
            "action",
            "action",
            "record",
        ]
        record_event = complete[-1]
        assert record_event["operation"] == "update"
        assert record_event["source"] == "action"
        assert record_event["outcome"] is None
        status = next(
            change for change in record_event["changes"] if change["field"] == "status"
        )
        assert status == {
            "field": "status",
            "before_present": True,
            "after_present": True,
            "value_mode": "recorded",
            "before": "draft",
            "after": "posted",
        }
        posted_by = next(
            change
            for change in record_event["changes"]
            if change["field"] == "posted_by"
        )
        assert posted_by["value_mode"] == "redacted"
        assert posted_by["before"] is posted_by["after"] is None
        assert denied.status_code == 403
        assert denied.json()["code"] == "forbidden"

    asyncio.run(exercise())


def test_server_builds_only_authorized_renderer_neutral_reports() -> None:
    allowed_app = _app("sales_clerk")
    denied_app = _app("summary_viewer")

    async def exercise() -> None:
        async with _client(allowed_app) as client:
            generated = await client.get(
                "/api/v1/_tide/reports/sales.invoice/records/1",
                headers=_authorization(),
            )
            unknown = await client.get(
                "/api/v1/_tide/reports/missing.report/records/1",
                headers=_authorization(),
            )
        async with _client(denied_app) as client:
            session = await client.get(
                "/api/v1/_tide/session",
                headers=_authorization(),
            )
            denied = await client.get(
                "/api/v1/_tide/reports/sales.invoice/records/1",
                headers=_authorization(),
            )

        assert generated.status_code == 200
        document = generated.json()
        assert document["wire_version"] == "0.1"
        assert document["report"] == "sales.invoice"
        assert document["application"] == "TIDE Invoicing"
        assert document["suggested_filename"] == "invoice-INV-2026-0001"
        assert document["detail"]["rows"][0][-1] == {
            "text": "850.00",
            "alignment": "right",
        }
        assert generated.headers["cache-control"] == "no-store"
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "not_found"
        assert session.json()["reports"] == []
        assert denied.status_code == 403
        assert denied.json()["code"] == "forbidden"

    asyncio.run(exercise())


def test_report_rest_delivery_is_independently_deny_by_default() -> None:
    model = compile_project(INVOICING)
    report = dict(model.reports["sales.invoice"])
    report["expose"] = {"rest": False, "mcp": False}
    model = replace(
        model,
        reports=immutable_mapping({"sales.invoice": report}),
    )
    app = _app("sales_clerk", model=model)

    async def exercise() -> None:
        async with _client(app) as client:
            session = await client.get(
                "/api/v1/_tide/session",
                headers=_authorization(),
            )
            hidden = await client.get(
                "/api/v1/_tide/reports/sales.invoice/records/1",
                headers=_authorization(),
            )

        assert session.json()["reports"] == []
        assert hidden.status_code == 404
        assert hidden.json()["code"] == "not_found"

    asyncio.run(exercise())


def test_server_preserves_protected_field_metadata_and_permissions() -> None:
    summary_app = _app("summary_viewer")
    denied_app = _app(None)

    async def exercise() -> None:
        async with _client(summary_app) as client:
            protected = await client.get(
                "/api/v1/invoices/1",
                headers=_authorization(),
            )
        async with _client(denied_app) as client:
            denied = await client.get(
                "/api/v1/invoices",
                headers={**_authorization(), "X-Tide-Role": "sales_clerk"},
            )

        assert protected.status_code == 200
        assert protected.json()["lines"] is None
        assert protected.json()["total"] is None
        assert protected.json()["_tide"] == {
            "protected_fields": ["lines", "posted_by", "total"]
        }
        assert denied.status_code == 403
        assert denied.json()["code"] == "forbidden"

    asyncio.run(exercise())


def test_development_authenticator_rejects_short_tokens() -> None:
    try:
        DevelopmentTokenAuthenticator("short", Principal("test"))
    except ValueError as error:
        assert "at least 32" in str(error)
    else:  # pragma: no cover - defensive assertion.
        raise AssertionError("short development token was accepted")


def test_tide_serve_requires_token_without_echoing_secret(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("MISSING_TIDE_API_TOKEN", raising=False)
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--dev-token-env",
            "MISSING_TIDE_API_TOKEN",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: development bearer-token environment variable "
        "'MISSING_TIDE_API_TOKEN' is not set\n"
    )


def test_tide_serve_builds_local_app_with_server_assigned_role(
    monkeypatch,
    capsys,
) -> None:
    launched: dict[str, Any] = {}
    monkeypatch.setenv("TEST_TIDE_API_TOKEN", TOKEN)

    def fake_run(app: Any, **configuration: Any) -> None:
        launched["app"] = app
        launched["configuration"] = configuration

    monkeypatch.setattr(uvicorn, "run", fake_run)

    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--dev-token-env",
            "TEST_TIDE_API_TOKEN",
            "--role",
            "auditor",
            "--port",
            "8123",
            "--log-level",
            "warning",
            "--max-request-body-bytes",
            "2048",
            "--max-concurrent-requests",
            "7",
            "--request-body-timeout",
            "13",
            "--keep-alive-timeout",
            "11",
            "--graceful-shutdown-timeout",
            "12",
        ]
    )

    assert result == 0
    assert launched["configuration"] == {
        "host": "127.0.0.1",
        "port": 8123,
        "log_level": "warning",
        "access_log": False,
        "proxy_headers": False,
        "server_header": False,
        "limit_concurrency": 7,
        "timeout_keep_alive": 11,
        "timeout_graceful_shutdown": 12,
    }
    runtime = launched["app"].state.tide
    assert runtime.authenticator.authenticate(TOKEN) == Principal(
        "development:api",
        roles=frozenset({"auditor"}),
    )
    assert runtime.max_request_body_bytes == 2048
    assert runtime.request_body_timeout_seconds == 13
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert "development auth only" in output


def test_tide_serve_rejects_development_authentication_off_loopback(
    capsys,
) -> None:
    result = main(
        ["serve", str(INVOICING), "--demo", "--host", "0.0.0.0"]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: development authentication may listen only on a "
        "loopback interface\n"
    )


def test_tide_serve_rejects_invalid_operational_limits(capsys) -> None:
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--max-request-body-bytes",
            "0",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: maximum request body size must be a positive integer\n"
    )


def test_tide_serve_requires_direct_tls_for_non_loopback_oidc(capsys) -> None:
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "oidc",
            "--host",
            "0.0.0.0",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: non-loopback serving requires --ssl-certfile and "
        "--ssl-keyfile\n"
    )


def test_tide_serve_rejects_unknown_oidc_role_mapping(capsys) -> None:
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "oidc",
            "--oidc-issuer",
            "https://identity.example.test/tenant",
            "--oidc-audience",
            "tide-api",
            "--oidc-role-map",
            "external-sales=not-an-application-role",
        ]
    )

    assert result == 1
    assert "unknown application role 'not-an-application-role'" in (
        capsys.readouterr().err
    )


def test_tide_serve_builds_non_loopback_oidc_app_with_direct_tls(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    launched: dict[str, Any] = {}
    discovery: dict[str, Any] = {}
    certfile = tmp_path / "server-cert.pem"
    keyfile = tmp_path / "server-key.pem"
    certfile.write_text("test certificate", encoding="utf-8")
    keyfile.write_text("test key", encoding="utf-8")

    class TestOidcAuthenticator:
        authentication_type = "oidc-jwt"
        production = True

        def authenticate(self, credential: str) -> Principal | None:
            if credential == "valid-token":
                return Principal(
                    "oidc:test-user",
                    roles=frozenset({"sales_clerk"}),
                )
            return None

    def fake_discovery(cls: Any, **configuration: Any) -> Any:
        discovery.update(configuration)
        return TestOidcAuthenticator()

    def fake_run(app: Any, **configuration: Any) -> None:
        launched["app"] = app
        launched["configuration"] = configuration

    monkeypatch.setattr(
        OidcJwtAuthenticator,
        "from_discovery",
        classmethod(fake_discovery),
    )
    monkeypatch.setattr(uvicorn, "run", fake_run)

    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "oidc",
            "--host",
            "0.0.0.0",
            "--port",
            "8443",
            "--ssl-certfile",
            str(certfile),
            "--ssl-keyfile",
            str(keyfile),
            "--oidc-issuer",
            "https://identity.example.test/tenant",
            "--oidc-audience",
            "tide-api",
            "--oidc-role-map",
            "external-sales=sales_clerk",
            "--mcp",
            "--mcp-resource-url",
            "https://tide.example.test:8443/mcp",
        ]
    )

    assert result == 0
    assert discovery["role_map"] == {"external-sales": "sales_clerk"}
    assert discovery["algorithms"] == ("RS256",)
    assert launched["configuration"] == {
        "host": "0.0.0.0",
        "port": 8443,
        "log_level": "info",
        "access_log": False,
        "proxy_headers": False,
        "server_header": False,
        "limit_concurrency": 100,
        "timeout_keep_alive": 5,
        "timeout_graceful_shutdown": 30,
        "ssl_certfile": str(certfile),
        "ssl_keyfile": str(keyfile),
    }
    schema = launched["app"].openapi()
    assert schema["x-tide"]["authentication"] == "oidc-jwt"
    assert schema["components"]["securitySchemes"]["bearerAuth"][
        "bearerFormat"
    ] == "JWT"
    hosted_mcp = launched["app"].state.tide_mcp
    assert hosted_mcp.resource_url == "https://tide.example.test:8443/mcp"
    assert hosted_mcp.issuer_url == "https://identity.example.test/tenant"
    output = capsys.readouterr().out
    assert "https://0.0.0.0:8443" in output
    assert "OIDC issuer https://identity.example.test/tenant" in output
    assert "MCP: https://tide.example.test:8443/mcp" in output


def test_tide_serve_mounts_read_only_mcp_on_the_local_api(
    monkeypatch,
    capsys,
) -> None:
    launched: dict[str, Any] = {}
    monkeypatch.setenv("TEST_TIDE_API_TOKEN", TOKEN)

    def fake_run(app: Any, **configuration: Any) -> None:
        launched["app"] = app
        launched["configuration"] = configuration

    monkeypatch.setattr(uvicorn, "run", fake_run)

    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--dev-token-env",
            "TEST_TIDE_API_TOKEN",
            "--role",
            "sales_clerk",
            "--port",
            "8124",
            "--mcp",
        ]
    )

    assert result == 0
    hosted = launched["app"].state.tide_mcp
    assert hosted.path == "/mcp"
    assert hosted.resource_url == "http://127.0.0.1:8124/mcp"
    assert hosted.issuer_url == "http://127.0.0.1:8124"
    assert set(hosted.service.exposures) == {
        "catalog.Product",
        "crm.Customer",
        "sales.Invoice",
    }
    assert launched["configuration"] == {
        "host": "127.0.0.1",
        "port": 8124,
        "log_level": "info",
        "access_log": False,
        "proxy_headers": False,
        "server_header": False,
        "limit_concurrency": 100,
        "timeout_keep_alive": 5,
        "timeout_graceful_shutdown": 30,
    }
    assert "MCP: http://127.0.0.1:8124/mcp" in capsys.readouterr().out


def test_tide_serve_requires_canonical_resource_url_for_network_mcp(
    capsys,
    tmp_path: Path,
) -> None:
    certfile = tmp_path / "server-cert.pem"
    keyfile = tmp_path / "server-key.pem"
    certfile.write_text("test certificate", encoding="utf-8")
    keyfile.write_text("test key", encoding="utf-8")

    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--auth",
            "oidc",
            "--host",
            "0.0.0.0",
            "--ssl-certfile",
            str(certfile),
            "--ssl-keyfile",
            str(keyfile),
            "--mcp",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: non-loopback MCP serving requires "
        "--mcp-resource-url\n"
    )


def test_tide_serve_requires_mcp_resource_path_to_match_endpoint(capsys) -> None:
    result = main(
        [
            "serve",
            str(INVOICING),
            "--demo",
            "--mcp",
            "--mcp-resource-url",
            "http://127.0.0.1:8000/not-mcp",
        ]
    )

    assert result == 1
    assert capsys.readouterr().err == (
        "API startup failed: MCP resource URL must be an absolute HTTP or HTTPS "
        "URL whose path exactly matches --mcp-path\n"
    )


def _app(
    role: str | None,
    *,
    model: Any | None = None,
    logger: logging.Logger | None = None,
    max_request_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES,
    request_body_timeout_seconds: int = DEFAULT_REQUEST_BODY_TIMEOUT_SECONDS,
) -> Any:
    model = model or compile_project(INVOICING)
    repository = InMemoryRepository()
    assert seed_demo_data(model, repository) == 14
    records = RecordsService(model, repository)
    actions = ActionService(model, records)
    assert configure_application_runtime(model, records, actions)
    principal = Principal(
        "api:test",
        roles=frozenset({role}) if role else frozenset(),
    )
    return build_fastapi_app(
        model,
        records,
        DevelopmentTokenAuthenticator(TOKEN, principal),
        actions=actions,
        logger=logger,
        max_request_body_bytes=max_request_body_bytes,
        request_body_timeout_seconds=request_body_timeout_seconds,
    )


def _invoice_delete_model() -> Any:
    model = compile_project(INVOICING)
    invoice = model.entity("sales.Invoice")
    metadata = deep_thaw(invoice.metadata)
    metadata["permissions"]["delete"] = "sales.invoice.write"
    operations = metadata["expose"]["rest"]["operations"]
    metadata["expose"]["rest"]["operations"] = [*operations, "delete"]
    entities = dict(model.entities)
    entities[invoice.name] = replace(
        invoice,
        metadata=immutable_mapping(metadata),
    )
    return replace(model, entities=immutable_mapping(entities))


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _authorization() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _recording_logger() -> tuple[logging.Logger, _RecordingHandler]:
    logger = logging.Logger("tide.test.runtime", level=logging.DEBUG)
    handler = _RecordingHandler()
    logger.addHandler(handler)
    return logger, handler
