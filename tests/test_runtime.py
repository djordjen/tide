from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tide import compile_project
from tide.data import InMemoryRepository
from tide.runtime import (
    ActionDisabled,
    AuthorizationError,
    Channel,
    ConcurrencyError,
    ImmutableFieldError,
    Principal,
    RequestContext,
    ValidationFailed,
)
from tide.runtime.errors import IdempotencyConflict
from tide.security import PROTECTED, SecurityEngine
from tide.services import ActionService, FilterCondition, QuerySpec, RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
SPEC = importlib.util.spec_from_file_location("invoicing_actions_runtime", INVOICING / "actions.py")
assert SPEC and SPEC.loader
invoicing_actions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(invoicing_actions)


@pytest.fixture
def runtime():
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    repository.seed(
        "crm.Customer",
        [
            {"id": 1, "code": "ACME", "name": "ACME Ltd", "email": None, "active": True, "invoices": []},
            {"id": 2, "code": "OLD", "name": "Inactive Co", "email": None, "active": False, "invoices": []},
        ],
    )
    repository.seed(
        "catalog.Product",
        [{"id": 1, "code": "CONS", "name": "Consulting", "unit_price": Decimal("4.20"), "active": True}],
    )
    security = SecurityEngine(model)
    records = RecordsService(model, repository, security)
    records.register_generator(
        "actions.allocate_invoice_number",
        lambda values, context, repo: invoicing_actions.allocate_invoice_number(
            repo.peek_next_identity("sales.Invoice"), values["invoice_date"]
        ),
    )
    actions = ActionService(model, records, security)
    actions.register(
        "actions.post_invoice",
        lambda record, context, payload: invoicing_actions.post_invoice(
            record,
            principal=context.principal.identifier,
            occurred_at=payload.get("occurred_at"),
        ),
    )
    return model, repository, records, actions


def context(identifier: str, *roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal(identifier, roles=frozenset(roles)),
        channel=Channel.TUI,
    )


def invoice_values(*, lines: bool = True) -> dict:
    return {
        "invoice_date": date(2026, 7, 14),
        "customer": 1,
        "lines": (
            [
                {
                    "line_number": 1,
                    "description": "Consulting",
                    "quantity": Decimal("2.5"),
                    "unit_price": Decimal("4.20"),
                    "product": 1,
                }
            ]
            if lines
            else []
        ),
    }


def test_headless_invoice_create_post_retry_and_protection(runtime) -> None:
    _, _, records, actions = runtime
    clerk = context("user:clerk", "sales_clerk")
    auditor = context("user:auditor", "auditor")

    session = records.create("sales.Invoice", clerk, invoice_values())
    created = records.commit(session, clerk)

    assert created["id"] == 1
    assert created["number"] == "INV-2026-000001"
    assert created["version"] == 1
    assert created["lines"][0]["total"] == Decimal("10.50")
    assert created["total"] == Decimal("10.50")

    occurred_at = datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc)
    posted = actions.execute(
        "sales.Invoice",
        "post",
        created["id"],
        {"occurred_at": occurred_at},
        clerk,
        idempotency_key="post-invoice-1",
    )

    assert posted["status"] == "posted"
    assert posted["version"] == 2
    assert posted["posted_by"] == PROTECTED

    retried = actions.execute(
        "sales.Invoice",
        "post",
        created["id"],
        {"occurred_at": occurred_at},
        clerk,
        idempotency_key="post-invoice-1",
    )
    assert retried["version"] == 2

    audited = records.get("sales.Invoice", created["id"], auditor)
    assert audited["posted_by"] == "user:clerk"

    summary = records.get(
        "sales.Invoice", created["id"], context("user:summary", "summary_viewer")
    )
    assert summary["lines"] == PROTECTED
    assert summary["total"] == PROTECTED

    with pytest.raises(IdempotencyConflict):
        actions.execute(
            "sales.Invoice",
            "post",
            created["id"],
            {"occurred_at": datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)},
            clerk,
            idempotency_key="post-invoice-1",
        )


def test_posted_invoice_is_immutable_and_status_is_action_owned(runtime) -> None:
    _, _, records, actions = runtime
    clerk = context("user:clerk", "sales_clerk")
    created = records.commit(records.create("sales.Invoice", clerk, invoice_values()), clerk)
    actions.execute("sales.Invoice", "post", created["id"], {}, clerk, idempotency_key="post-1")

    edit = records.begin_edit("sales.Invoice", created["id"], clerk)
    edit.set("invoice_date", date(2026, 7, 15))
    with pytest.raises(ImmutableFieldError):
        records.commit(edit, clerk)

    other = records.create("sales.Invoice", clerk, invoice_values())
    other.set("status", "posted")
    with pytest.raises(ImmutableFieldError):
        records.commit(other, clerk)


def test_action_permission_does_not_require_general_update_permission(runtime) -> None:
    _, _, records, actions = runtime
    clerk = context("user:clerk", "sales_clerk")
    poster = context("user:poster", "invoice_poster")
    created = records.commit(records.create("sales.Invoice", clerk, invoice_values()), clerk)

    with pytest.raises(AuthorizationError):
        records.begin_edit("sales.Invoice", created["id"], poster)

    posted = actions.execute(
        "sales.Invoice",
        "post",
        created["id"],
        {},
        poster,
        idempotency_key="poster-post-1",
    )
    assert posted["status"] == "posted"
    assert posted["version"] == 2


def test_optimistic_concurrency_rejects_stale_session(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    created = records.commit(records.create("sales.Invoice", clerk, invoice_values()), clerk)
    first = records.begin_edit("sales.Invoice", created["id"], clerk)
    stale = records.begin_edit("sales.Invoice", created["id"], clerk)

    first.set("currency", "USD")
    updated = records.commit(first, clerk)
    assert updated["version"] == 2

    stale.set("currency", "GBP")
    with pytest.raises(ConcurrencyError) as caught:
        records.commit(stale, clerk)
    assert caught.value.expected == 1
    assert caught.value.actual == 2


def test_row_policy_query_and_authorization(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    outsider = context("user:outside")

    customers = records.query("crm.Customer", QuerySpec(), clerk)
    assert [customer["code"] for customer in customers] == ["ACME"]

    with pytest.raises(AuthorizationError):
        records.query("crm.Customer", QuerySpec(), outsider)


def test_post_requires_lines(runtime) -> None:
    _, _, records, actions = runtime
    clerk = context("user:clerk", "sales_clerk")
    created = records.commit(records.create("sales.Invoice", clerk, invoice_values(lines=False)), clerk)

    with pytest.raises(ActionDisabled):
        actions.execute("sales.Invoice", "post", created["id"], {}, clerk)


def test_required_values_and_protected_field_inference_are_enforced(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    invalid = records.create(
        "sales.Invoice",
        clerk,
        {"customer": 1, "lines": []},
    )
    with pytest.raises(ValidationFailed) as caught:
        records.commit(invalid, clerk)
    assert caught.value.issues[0].fields == ("invoice_date",)

    with pytest.raises(AuthorizationError):
        records.query(
            "sales.Invoice",
            QuerySpec(filters=(FilterCondition("posted_by", "eq", "user:clerk"),)),
            clerk,
        )
