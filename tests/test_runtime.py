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
from tide.services import (
    ActionService,
    FilterCondition,
    QuerySpec,
    RecordsService,
    SortField,
)

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


def test_create_session_applies_today_default_factory(runtime) -> None:
    _, _, records, _ = runtime

    session = records.create(
        "sales.Invoice",
        context("user:clerk", "sales_clerk"),
    )

    assert session.values["invoice_date"] == date.today()
    assert session.original["invoice_date"] == date.today()


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


def test_action_authorization_fails_closed_without_explicit_access(runtime) -> None:
    model, _, records, _ = runtime
    entity = model.entity("sales.Invoice")
    clerk = context("user:clerk", "sales_clerk")

    with pytest.raises(AuthorizationError):
        records.security.authorize_action(entity, {}, clerk)

    records.security.authorize_action(entity, {"unrestricted": True}, clerk)


def test_commit_coerces_typed_inputs_to_declared_field_types(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    values = invoice_values()
    values["lines"][0]["quantity"] = 2.5
    values["lines"][0]["unit_price"] = "4.20"

    created = records.commit(records.create("sales.Invoice", clerk, values), clerk)

    line = created["lines"][0]
    assert isinstance(line["quantity"], Decimal)
    assert line["quantity"] == Decimal("2.5")
    assert isinstance(line["unit_price"], Decimal)
    assert line["unit_price"] == Decimal("4.20")
    assert isinstance(created["total"], Decimal)
    assert created["total"] == Decimal("10.50")


def test_commit_rejects_values_that_cannot_become_the_field_type(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    values = invoice_values()
    values["lines"][0]["quantity"] = "not-a-number"

    with pytest.raises(ValidationFailed) as caught:
        records.commit(records.create("sales.Invoice", clerk, values), clerk)

    assert any(
        issue.rule == "type" and issue.fields == ("quantity",)
        for issue in caught.value.issues
    )


def test_commit_rejects_wrong_scalar_types_instead_of_storing_them(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    values = invoice_values()
    values["invoice_date"] = "2026-07-14"
    values["lines"][0]["line_number"] = "1"

    with pytest.raises(ValidationFailed) as caught:
        records.commit(records.create("sales.Invoice", clerk, values), clerk)

    failed = {issue.fields for issue in caught.value.issues if issue.rule == "type"}
    assert ("invoice_date",) in failed
    assert ("line_number",) in failed


def test_commit_rejects_non_boolean_flags(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    session = records.create(
        "crm.Customer",
        clerk,
        {"code": "NEW", "name": "New Co", "active": "yes"},
    )

    with pytest.raises(ValidationFailed) as caught:
        records.commit(session, clerk)

    assert any(
        issue.rule == "type" and issue.fields == ("active",)
        for issue in caught.value.issues
    )


def test_commit_rejects_reference_with_wrong_identity_type(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    values = invoice_values()
    values["customer"] = "1"

    with pytest.raises(ValidationFailed) as caught:
        records.commit(records.create("sales.Invoice", clerk, values), clerk)

    assert any(
        issue.rule == "type" and issue.fields == ("customer",)
        for issue in caught.value.issues
    )


def test_commit_rejects_reference_to_missing_record(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    values = invoice_values()
    values["lines"][0]["product"] = 999

    with pytest.raises(ValidationFailed) as caught:
        records.commit(records.create("sales.Invoice", clerk, values), clerk)

    assert any(
        issue.rule == "reference" and issue.fields == ("product",)
        for issue in caught.value.issues
    )


def test_unique_fields_allow_multiple_null_values(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")

    first = records.commit(
        records.create("crm.Customer", clerk, {"code": "A1", "name": "First"}), clerk
    )
    second = records.commit(
        records.create("crm.Customer", clerk, {"code": "A2", "name": "Second"}), clerk
    )

    assert first["email"] is None
    assert second["email"] is None


def test_unique_fields_reject_duplicate_values(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    records.commit(
        records.create(
            "crm.Customer", clerk, {"code": "B1", "name": "First", "email": "x@example.com"}
        ),
        clerk,
    )

    with pytest.raises(ValidationFailed) as caught:
        records.commit(
            records.create(
                "crm.Customer", clerk, {"code": "B2", "name": "Second", "email": "x@example.com"}
            ),
            clerk,
        )

    assert any(
        issue.rule == "unique" and issue.fields == ("email",)
        for issue in caught.value.issues
    )


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


def test_query_rejects_unstored_fields_and_invalid_filter_types(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")

    with pytest.raises(ValueError, match="not stored"):
        records.query(
            "sales.Invoice",
            QuerySpec(sort=(SortField("lines"),)),
            clerk,
        )

    with pytest.raises(ValueError, match="string field and value"):
        records.query(
            "crm.Customer",
            QuerySpec(filters=(FilterCondition("active", "contains", "true"),)),
            clerk,
        )

    with pytest.raises(ValueError, match="must be a integer"):
        records.query(
            "crm.Customer",
            QuerySpec(filters=(FilterCondition("id", "eq", "1"),)),
            clerk,
        )


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
        {"lines": []},
    )
    with pytest.raises(ValidationFailed) as caught:
        records.commit(invalid, clerk)
    assert caught.value.issues[0].fields == ("customer",)

    with pytest.raises(AuthorizationError):
        records.query(
            "sales.Invoice",
            QuerySpec(filters=(FilterCondition("posted_by", "eq", "user:clerk"),)),
            clerk,
        )
