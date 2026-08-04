from __future__ import annotations

import asyncio
from datetime import date
import importlib.util
from pathlib import Path
import sys

import httpx

from tide import compile_project
from tide.api.server import DevelopmentTokenAuthenticator, build_fastapi_app
from tide.data import InMemoryRepository
from tide.runtime import Principal
from tide.runtime.application import configure_application_runtime
from tide.services import ActionService, RecordsService
from tide.tui import seed_demo_data


ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
TOKEN = "tide-api-tutorial-token-that-is-long-enough"
BASE_URL = "http://127.0.0.1"

_EXAMPLE_SPEC = importlib.util.spec_from_file_location(
    "tide_invoicing_api_client_example",
    ROOT / "examples" / "invoicing_api_client.py",
)
assert _EXAMPLE_SPEC is not None and _EXAMPLE_SPEC.loader is not None
_EXAMPLE = importlib.util.module_from_spec(_EXAMPLE_SPEC)
sys.modules[_EXAMPLE_SPEC.name] = _EXAMPLE
_EXAMPLE_SPEC.loader.exec_module(_EXAMPLE)
run_tutorial = _EXAMPLE.run_tutorial


def test_api_tutorial_runs_against_the_in_process_generated_server() -> None:
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
            Principal(
                "api:tutorial-test",
                roles=frozenset({"sales_clerk", "auditor"}),
            ),
        ),
        actions=actions,
    )
    output: list[str] = []

    with _http_client(app) as transport:
        result = run_tutorial(
            project=INVOICING,
            base_url=BASE_URL,
            token=TOKEN,
            invoice_date=date(2026, 7, 20),
            idempotency_key="api-tutorial-test-post",
            http_client=transport,
            write=output.append,
        )

    assert result.invoice_id == 9
    assert result.invoice_number == "INV-2026-000009"
    assert result.created_etag == '"1"'
    assert result.updated_etag == '"2"'
    assert result.posted_etag == '"3"'
    assert result.validation_error_code == "invalid_request"
    assert result.concurrency_error_code == "stale_version"
    assert result.audit_correlation_id
    assert result.report_filename == "invoice-INV-2026-000009"
    assert any("rejected an invalid date" in line for line in output)
    assert any("rejected the stale ETag" in line for line in output)
    assert any("replayed safely" in line for line in output)
    assert any("Audit:" in line and "correlation" in line for line in output)

    paths = app.openapi()["paths"]
    assert {
        "/api/v1/_tide/session",
        "/api/v1/customers",
        "/api/v1/products",
        "/api/v1/invoices",
        "/api/v1/invoices/{id}",
        "/api/v1/invoices/{id}/actions/post",
        "/api/v1/invoices/{id}/_audit",
        "/api/v1/_tide/reports/{report_name}",
        "/api/v1/_tide/reports/{report_name}/records/{identity}",
    } <= set(paths)


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
