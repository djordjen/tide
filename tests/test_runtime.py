from __future__ import annotations

import importlib.util
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tide import compile_project
from tide.compiler.normalized import deep_thaw, immutable_mapping
from tide.data import InMemoryRepository
from tide.data.sqlalchemy import SQLAlchemyRepository
from tide.runtime import (
    ActionDisabled,
    AuthorizationError,
    Channel,
    ConcurrencyError,
    DeleteRestricted,
    ImmutableFieldError,
    Principal,
    RequestContext,
    ValidationFailed,
    VersionPreconditionRequired,
)
from tide.runtime.errors import IdempotencyConflict
from tide.security import PROTECTED, SecurityEngine
from tide.sessions import (
    ConflictDisposition,
    ConflictValueChoice,
    compare_record_conflict,
    resolve_record_conflict,
)
from tide.services import (
    ActionService,
    FilterCondition,
    QuerySpec,
    RecordAuditOperation,
    RecordsService,
    SortField,
)

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
SPEC = importlib.util.spec_from_file_location("invoicing_actions_runtime", INVOICING / "actions.py")
assert SPEC and SPEC.loader
invoicing_actions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(invoicing_actions)


@pytest.fixture(params=("memory", "sql"))
def runtime(request: pytest.FixtureRequest):
    model = compile_project(INVOICING)
    repository: InMemoryRepository | SQLAlchemyRepository
    if request.param == "memory":
        repository = InMemoryRepository()
    else:
        repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
        repository.create_schema()
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
    yield model, repository, records, actions
    if isinstance(repository, SQLAlchemyRepository):
        repository.dispose()


def context(identifier: str, *roles: str) -> RequestContext:
    return RequestContext(
        principal=Principal(identifier, roles=frozenset(roles)),
        channel=Channel.TUI,
    )


def test_record_conflict_comparison_classifies_safe_and_overlapping_changes() -> None:
    original = {"name": "Original", "email": "old@example.test", "active": True}
    current = {"name": "Current", "email": "old@example.test", "active": False}
    draft = {"name": "Draft", "email": "new@example.test", "active": False}

    conflict = compare_record_conflict(original, current, draft)

    assert [field.name for field in conflict.fields] == ["name", "email", "active"]
    assert [field.disposition for field in conflict.fields] == [
        ConflictDisposition.CONFLICT,
        ConflictDisposition.YOUR_CHANGE,
        ConflictDisposition.SAME_CHANGE,
    ]
    assert conflict.conflicting_fields == ("name",)
    assert conflict.rebase_fields == ("email",)
    unresolved = resolve_record_conflict(conflict, {})
    assert not unresolved.complete
    assert unresolved.unresolved_fields == ("name",)

    resolution = resolve_record_conflict(
        conflict,
        {"name": ConflictValueChoice.DRAFT},
    )
    assert resolution.complete
    assert resolution.draft_fields == ("name", "email")
    assert resolution.current_fields == ("active",)

    with pytest.raises(ValueError, match="non-conflicting field"):
        resolve_record_conflict(
            conflict,
            {"email": ConflictValueChoice.CURRENT},
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


def test_delete_is_authorized_and_restricts_embedded_references(runtime) -> None:
    _, repository, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    repository.seed(
        "catalog.Product",
        [
            {
                "id": 2,
                "code": "FREE",
                "name": "Unused product",
                "unit_price": Decimal("1.00"),
                "active": True,
            }
        ],
    )

    records.delete("catalog.Product", 2, clerk)
    assert not repository.exists("catalog.Product", 2)

    invoice = records.create("sales.Invoice", clerk, invoice_values())
    records.commit(invoice, clerk)
    with pytest.raises(DeleteRestricted) as restricted:
        records.delete("catalog.Product", 1, clerk)
    assert restricted.value.relationship == "sales.InvoiceLine.product"
    assert repository.exists("catalog.Product", 1)


def test_delete_permission_fails_closed(runtime) -> None:
    _, repository, records, _ = runtime

    with pytest.raises(AuthorizationError):
        records.delete(
            "catalog.Product",
            1,
            context("user:auditor", "auditor"),
        )

    assert repository.exists("catalog.Product", 1)


def test_delete_row_policy_fails_closed(runtime) -> None:
    model, repository, _, _ = runtime
    policies = [deep_thaw(policy) for policy in model.row_policies]
    active_products = next(
        policy for policy in policies if policy["id"] == "active_products"
    )
    active_products["operations"].append("delete")
    secured_model = replace(
        model,
        row_policies=tuple(immutable_mapping(policy) for policy in policies),
    )
    records = RecordsService(secured_model, repository)
    repository.seed(
        "catalog.Product",
        [
            {
                "id": 2,
                "code": "INACTIVE",
                "name": "Inactive product",
                "unit_price": Decimal("1.00"),
                "active": False,
            }
        ],
    )

    with pytest.raises(AuthorizationError):
        records.delete(
            "catalog.Product",
            2,
            context("user:clerk", "sales_clerk"),
        )

    assert repository.exists("catalog.Product", 2)


def _model_granting_invoice_delete(model):
    """Return the compiled model with delete allowed on sales.Invoice.

    The shipped invoicing metadata never grants it, so any test that has to
    actually remove an invoice has to add the permission first.
    """

    invoice = model.entity("sales.Invoice")
    metadata = deep_thaw(invoice.metadata)
    metadata["permissions"]["delete"] = "sales.invoice.write"
    entities = dict(model.entities)
    entities[invoice.name] = replace(invoice, metadata=immutable_mapping(metadata))
    return replace(model, entities=immutable_mapping(entities))


def _model_with_owner_policy(model):
    """Return the compiled model with a customer policy bound to the principal."""

    policy = immutable_mapping(
        {
            "id": "own_customer",
            "entity": "crm.Customer",
            "operations": ("list", "read"),
            "criteria": "code == $principal",
        }
    )
    return replace(model, row_policies=tuple(model.row_policies) + (policy,))


def test_row_policies_can_scope_records_to_the_current_principal(runtime) -> None:
    """A policy has to be able to name who is asking.

    Ownership is the common case for row security, and it cannot be written at
    all while criteria see only the record.
    """

    model, repository, _, _ = runtime
    repository.seed(
        "crm.Customer",
        [
            {
                "id": 3,
                "code": "OTHER",
                "name": "Other active customer",
                "email": "other@example.test",
                "active": True,
                "invoices": [],
            }
        ],
    )
    records = RecordsService(_model_with_owner_policy(model), repository)

    visible = records.query("crm.Customer", QuerySpec(), context("ACME", "sales_clerk"))

    assert [customer["code"] for customer in visible] == ["ACME"]


def test_row_policies_scoped_to_the_principal_also_guard_a_single_record(
    runtime,
) -> None:
    """The same binding must apply when a record is fetched by identity."""

    model, repository, _, _ = runtime
    repository.seed(
        "crm.Customer",
        [
            {
                "id": 3,
                "code": "OTHER",
                "name": "Other active customer",
                "email": "other@example.test",
                "active": True,
                "invoices": [],
            }
        ],
    )
    records = RecordsService(_model_with_owner_policy(model), repository)
    acme = context("ACME", "sales_clerk")

    assert records.get("crm.Customer", 1, acme)["code"] == "ACME"
    with pytest.raises(AuthorizationError):
        records.get("crm.Customer", 3, acme)


def _model_without_orphan_delete(model):
    """Return the compiled model with sales.Invoice.lines keeping its orphans.

    The shipped collection declares `orphan_delete`, so removal semantics for a
    collection that does not can only be exercised by taking it away.
    """

    invoice = model.entity("sales.Invoice")
    lines = invoice.field("lines")
    metadata = deep_thaw(lines.metadata)
    metadata.pop("orphan_delete", None)
    fields = dict(invoice.fields)
    fields["lines"] = replace(lines, metadata=immutable_mapping(metadata))
    entities = dict(model.entities)
    entities[invoice.name] = replace(invoice, fields=immutable_mapping(fields))
    return replace(model, entities=immutable_mapping(entities))


def test_removing_an_item_needs_a_collection_that_deletes_orphans(runtime) -> None:
    """Dropping a child from a collection that keeps orphans must not look saved.

    Without `orphan_delete` the row keeps pointing at its parent, so the item
    the caller removed reappears on the next read. Refusing the commit turns a
    silent reversal into a visible authoring mistake.
    """

    model, repository, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    created = records.commit(
        records.create(
            "sales.Invoice",
            clerk,
            invoice_values(lines=True),
        ),
        clerk,
    )
    keeping = RecordsService(_model_without_orphan_delete(model), repository)

    edit = keeping.begin_edit("sales.Invoice", created["id"], clerk)
    edit.set("lines", [])

    with pytest.raises(ValidationFailed) as caught:
        keeping.commit(edit, clerk)

    assert any(issue.rule == "orphan" for issue in caught.value.issues)


def test_delete_audits_the_rows_a_cascade_removes(runtime) -> None:
    """A cascade deletes child rows, so the trail has to name them.

    Invoice lines are removed under the invoice's own authority, which is what
    `on_delete: cascade` asks for. Recording only the invoice leaves no evidence
    that the lines existed, let alone what they held.
    """

    model, repository, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    created = records.commit(
        records.create("sales.Invoice", clerk, invoice_values()), clerk
    )
    deleting = RecordsService(
        _model_granting_invoice_delete(model),
        repository,
        audit_store=records.audit_store,
    )

    deleting.delete(
        "sales.Invoice",
        created["id"],
        clerk,
        expected_version=created["version"],
    )

    line_events = records.audit_store.record_audit_events(
        entity="sales.InvoiceLine"
    )
    assert [event.operation for event in line_events] == [
        RecordAuditOperation.DELETE
    ]
    assert all(
        change.before_present and not change.after_present
        for change in line_events[0].changes
    )


def test_versioned_delete_requires_and_checks_observed_version(runtime) -> None:
    model, repository, _, _ = runtime
    invoice = model.entity("sales.Invoice")
    metadata = deep_thaw(invoice.metadata)
    metadata["permissions"]["delete"] = "sales.invoice.write"
    entities = dict(model.entities)
    entities[invoice.name] = replace(
        invoice,
        metadata=immutable_mapping(metadata),
    )
    secured_model = replace(model, entities=immutable_mapping(entities))
    records = RecordsService(secured_model, repository)
    repository.seed(
        "sales.Invoice",
        [
            {
                "id": 1,
                "version": 1,
                "customer": 1,
                # Every non-null column has to be supplied: a real schema
                # rejects the partial record the in-memory store would accept.
                "number": "INV-2026-000001",
                "invoice_date": date(2026, 7, 14),
                "currency": "EUR",
                "status": "draft",
                "lines": [],
            }
        ],
    )
    clerk = context("user:clerk", "sales_clerk")

    with pytest.raises(VersionPreconditionRequired):
        records.delete("sales.Invoice", 1, clerk)
    with pytest.raises(ConcurrencyError):
        records.delete("sales.Invoice", 1, clerk, expected_version=0)

    records.delete("sales.Invoice", 1, clerk, expected_version=1)
    assert not repository.exists("sales.Invoice", 1)


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


def test_commit_rejects_unknown_fields_inside_a_collection_item(runtime) -> None:
    """A child payload must be checked as strictly as the record that owns it.

    The unknown-field guard only inspected top-level values, so anything extra
    on a nested line travelled straight into storage on the document-shaped
    repository and was silently dropped by the relational one.
    """

    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    values = invoice_values()
    values["lines"][0]["bogus"] = "anything"

    with pytest.raises(ValidationFailed) as caught:
        records.commit(records.create("sales.Invoice", clerk, values), clerk)

    assert any(issue.rule == "unknown_field" for issue in caught.value.issues)


def test_commit_rejects_a_client_chosen_identity_for_a_new_collection_item(
    runtime,
) -> None:
    """Child identity is system-owned, exactly like the parent primary key.

    Accepting one lets a caller pick a new line's key, or claim a key that
    belongs to another invoice's line.
    """

    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    values = invoice_values()
    values["lines"][0]["id"] = 4242

    with pytest.raises((ValidationFailed, ImmutableFieldError)):
        records.commit(records.create("sales.Invoice", clerk, values), clerk)


def test_collection_items_receive_a_stable_identity(runtime) -> None:
    """A child row needs its own key in every repository.

    Without one a commit cannot tell an edited line from a replaced one, and
    per-child concurrency, audit and orphan handling have nothing to key on.
    """

    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    created = records.commit(
        records.create("sales.Invoice", clerk, invoice_values()), clerk
    )

    edit = records.begin_edit("sales.Invoice", created["id"], clerk)
    identities = [line.get("id") for line in edit.values["lines"]]
    assert identities and all(identity is not None for identity in identities)

    lines = deepcopy(list(edit.values["lines"]))
    lines[0]["description"] = "Revised consulting"
    edit.set("lines", lines)
    updated = records.commit(edit, clerk)

    assert [line["id"] for line in updated["lines"]] == identities


def test_commit_updates_a_collection_item_loaded_from_the_record(runtime) -> None:
    """The identity rule must still let an ordinary line edit through.

    Every renderer edits a collection by sending back what it loaded, so a rule
    about identity has to accept exactly that. The two repositories disagree on
    whether a child carries a key at all, which is why this asserts the visible
    outcome rather than the key itself.
    """

    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    created = records.commit(
        records.create("sales.Invoice", clerk, invoice_values()), clerk
    )

    edit = records.begin_edit("sales.Invoice", created["id"], clerk)
    lines = deepcopy(list(edit.values["lines"]))
    lines[0]["description"] = "Revised consulting"
    edit.set("lines", lines)
    updated = records.commit(edit, clerk)

    assert [line["description"] for line in updated["lines"]] == [
        "Revised consulting"
    ]
    assert updated["total"] == Decimal("10.50")


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


def test_commit_enforces_decimal_scale_and_precision(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    too_precise = invoice_values()
    too_precise["lines"][0]["unit_price"] = "4.201"

    with pytest.raises(ValidationFailed) as caught:
        records.commit(records.create("sales.Invoice", clerk, too_precise), clerk)

    assert any(
        issue.rule == "scale" and issue.fields == ("unit_price",)
        for issue in caught.value.issues
    )

    too_large = records.create(
        "catalog.Product",
        clerk,
        {
            "code": "HUGE",
            "name": "Too large",
            "unit_price": "12345678901.00",
            "active": True,
        },
    )
    with pytest.raises(ValidationFailed) as caught:
        records.commit(too_large, clerk)

    assert any(
        issue.rule == "precision" and issue.fields == ("unit_price",)
        for issue in caught.value.issues
    )


def test_commit_enforces_regular_expression_edit_masks(runtime) -> None:
    _, _, records, _ = runtime
    clerk = context("user:clerk", "sales_clerk")
    customer = records.create(
        "crm.Customer",
        clerk,
        {"code": "lowercase", "name": "Invalid code", "active": True},
    )

    with pytest.raises(ValidationFailed) as caught:
        records.commit(customer, clerk)

    assert any(
        issue.rule == "edit_mask" and issue.fields == ("code",)
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
    assert first.expected_version == 2

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


@pytest.mark.parametrize(
    "operator, value, expected",
    [
        ("gt", "a", ["KNOWN"]),
        ("gte", "a", ["KNOWN"]),
        ("lt", "a", []),
        ("lte", "a", []),
        ("ne", "a", ["KNOWN"]),
        ("contains", "known", ["KNOWN"]),
        ("icontains", "KNOWN", ["KNOWN"]),
    ],
)
def test_filters_treat_a_stored_null_as_no_match(
    runtime,
    operator: str,
    value: str,
    expected: list[str],
) -> None:
    """A null column must drop out of a comparison rather than crash or match.

    SQL evaluates any comparison with NULL as unknown and excludes the row, so
    the in-memory store has to reach the same answer; otherwise the identical
    query returns different records depending on the configured repository.
    """

    _, repository, records, _ = runtime
    repository.seed(
        "crm.Customer",
        [
            {
                "id": 3,
                "code": "KNOWN",
                "name": "Known contact",
                "email": "known@example.test",
                "active": True,
                "invoices": [],
            }
        ],
    )
    clerk = context("user:clerk", "sales_clerk")

    matched = records.query(
        "crm.Customer",
        QuerySpec(filters=(FilterCondition("email", operator, value),)),
        clerk,
    )

    assert [customer["code"] for customer in matched] == expected


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
