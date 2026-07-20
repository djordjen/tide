"""Run an end-to-end invoicing workflow through the TIDE REST client."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import getpass
import os
from pathlib import Path
import sys
from uuid import uuid4

import httpx

from tide import compile_project
from tide.api.client import TideApiClient, TideApiClientError
from tide.api.contracts import TideSessionInfo
from tide.runtime import TideRuntimeError
from tide.services import ActionAuditEvent


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = ROOT / "applications" / "invoicing"
DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_TOKEN_ENV = "TIDE_API_TOKEN"


class TutorialConfigurationError(TideRuntimeError):
    """The connected principal cannot complete the tutorial workflow."""


@dataclass(frozen=True, slots=True)
class TutorialResult:
    """Stable summary returned by the executable tutorial and its CI test."""

    invoice_id: object
    invoice_number: str
    created_etag: str
    updated_etag: str
    posted_etag: str
    validation_error_code: str
    concurrency_error_code: str
    audit_correlation_id: str
    report_filename: str


def run_tutorial(
    *,
    project: Path = DEFAULT_PROJECT,
    base_url: str = DEFAULT_URL,
    token: str,
    invoice_date: date | None = None,
    idempotency_key: str | None = None,
    http_client: httpx.Client | None = None,
    write: Callable[[str], None] = print,
) -> TutorialResult:
    """Exercise validation, CRUD, concurrency, actions, audit, and reporting."""

    model = compile_project(project)
    chosen_date = invoice_date or date.today()
    action_key = idempotency_key or f"tide-api-tutorial-{uuid4().hex}"

    with TideApiClient(
        model,
        base_url,
        token,
        http_client=http_client,
    ) as client:
        session = client.connect()
        _require_capabilities(session)
        write(
            f"Connected to {session.application} {session.application_version} "
            f"as {session.principal} ({', '.join(session.roles)})."
        )

        customer = _first_record(client, "crm.Customer")
        product = _first_record(client, "catalog.Product")
        write(f"Customer: {customer['code']} - {customer['name']}")
        write(
            f"Product: {product['code']} - {product['name']} "
            f"({product['unit_price']})"
        )

        line = client.apply_reference_selection(
            "sales.InvoiceLine",
            "product",
            {
                "line_number": 1,
                "product": None,
                "description": "",
                "unit_price": Decimal("0.00"),
                "quantity": Decimal("1.000"),
            },
            product["id"],
        )
        invoice_input = {
            "invoice_date": chosen_date,
            "customer": customer["id"],
            "currency": "EUR",
            "lines": [line],
        }

        validation_error = _demonstrate_validation(client, invoice_input)
        write(
            "Validation example: rejected an invalid date with "
            f"HTTP {validation_error.status_code} ({validation_error.code})."
        )

        created = client.create_record("sales.Invoice", invoice_input)
        created_etag = _required_etag(created.etag, "created invoice")
        identity = created.values["id"]
        number = str(created.values["number"])
        write(f"Created {number} with ETag {created_etag}.")

        updated = client.update_record(
            "sales.Invoice",
            identity,
            {"currency": "USD"},
            if_match=created_etag,
        )
        updated_etag = _required_etag(updated.etag, "updated invoice")
        write(f"Updated currency to USD with ETag {updated_etag}.")

        concurrency_error = _demonstrate_stale_update(
            client,
            identity,
            stale_etag=created_etag,
        )
        write(
            "Concurrency example: rejected the stale ETag with "
            f"HTTP {concurrency_error.status_code} ({concurrency_error.code})."
        )

        posted = client.execute_action(
            "sales.Invoice",
            "post",
            identity,
            if_match=updated_etag,
            idempotency_key=action_key,
        )
        posted_etag = _required_etag(posted.etag, "posted invoice")
        replayed = client.execute_action(
            "sales.Invoice",
            "post",
            identity,
            if_match=updated_etag,
            idempotency_key=action_key,
        )
        if replayed.etag != posted_etag or replayed.values != posted.values:
            raise TideRuntimeError("idempotent action replay returned a new result")
        write(f"Posted once and replayed safely with ETag {posted_etag}.")

        events = client.audit_history("sales.Invoice", identity, limit=20)
        post_event = next(
            (
                event
                for event in events
                if isinstance(event, ActionAuditEvent) and event.action == "post"
            ),
            None,
        )
        if post_event is None or not post_event.correlation_id:
            raise TideRuntimeError("posted invoice has no correlated action audit event")
        write(
            "Audit: "
            f"{post_event.outcome.value} post event, correlation "
            f"{post_event.correlation_id}."
        )

        report = client.build_report_for_record("sales.invoice", identity)
        write(
            f"Report: {report.title} ({len(report.detail.rows)} line(s)), "
            f"suggested filename {report.suggested_filename}."
        )

    return TutorialResult(
        invoice_id=identity,
        invoice_number=number,
        created_etag=created_etag,
        updated_etag=updated_etag,
        posted_etag=posted_etag,
        validation_error_code=validation_error.code,
        concurrency_error_code=concurrency_error.code,
        audit_correlation_id=post_event.correlation_id,
        report_filename=report.suggested_filename,
    )


def _require_capabilities(session: TideSessionInfo) -> None:
    entities = session.entities
    invoice = entities["sales.Invoice"]
    required_operations = {"list", "get", "create", "update"}
    missing = required_operations - set(invoice.operations)
    if missing:
        raise TutorialConfigurationError(
            "the API principal is missing Invoice operations: "
            + ", ".join(sorted(missing))
        )
    if "post" not in invoice.actions:
        raise TutorialConfigurationError(
            "the API principal is not allowed to post invoices"
        )
    if not invoice.audit:
        raise TutorialConfigurationError(
            "the API principal cannot read invoice audit history; start the local "
            "tutorial server with both sales_clerk and auditor roles"
        )
    if "sales.invoice" not in session.reports:
        raise TutorialConfigurationError(
            "the API principal is not allowed to build the invoice report"
        )


def _first_record(client: TideApiClient, entity_name: str) -> dict[str, object]:
    page = client.list_records(entity_name, limit=1)
    if not page.records:
        raise TutorialConfigurationError(
            f"{entity_name} has no readable records; use the demo server or seed data"
        )
    return page.records[0]


def _demonstrate_validation(
    client: TideApiClient,
    valid_input: dict[str, object],
) -> TideApiClientError:
    invalid_input = dict(valid_input)
    invalid_input["invoice_date"] = "not-a-date"
    try:
        client.create_record("sales.Invoice", invalid_input)
    except TideApiClientError as error:
        if error.status_code == 422:
            return error
        raise
    raise TideRuntimeError("the API unexpectedly accepted an invalid invoice date")


def _demonstrate_stale_update(
    client: TideApiClient,
    identity: object,
    *,
    stale_etag: str,
) -> TideApiClientError:
    try:
        client.update_record(
            "sales.Invoice",
            identity,
            {"currency": "GBP"},
            if_match=stale_etag,
        )
    except TideApiClientError as error:
        if error.status_code == 412:
            return error
        raise
    raise TideRuntimeError("the API unexpectedly accepted a stale invoice ETag")


def _required_etag(value: str | None, label: str) -> str:
    if value is None:
        raise TideRuntimeError(f"{label} did not return an ETag")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the TIDE Invoicing REST client tutorial.",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=DEFAULT_PROJECT,
        help="TIDE application directory used to validate the remote contract",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"TIDE API base URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help=f"bearer-token environment variable (default: {DEFAULT_TOKEN_ENV})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    token = os.environ.get(arguments.token_env)
    if not token:
        try:
            token = getpass.getpass("Paste API token: ")
        except (EOFError, KeyboardInterrupt):
            token = ""
    if not token:
        print("API tutorial failed: no bearer token was entered.", file=sys.stderr)
        return 1

    try:
        run_tutorial(
            project=arguments.project,
            base_url=arguments.url,
            token=token,
        )
    except (TideRuntimeError, ValueError) as error:
        print(f"API tutorial failed: {error}", file=sys.stderr)
        return 1
    print("Tutorial completed; all writes went through the TIDE API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
