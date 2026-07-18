from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect

from tide import compile_project
from tide.compiler.normalized import immutable_mapping
from tide.data import (
    InMemoryRepository,
    SQLAlchemyActionExecutionStore,
    SQLAlchemyRepository,
    SchemaManagementError,
)
from tide.runtime import (
    ActionDisabled,
    AuthorizationError,
    Channel,
    Principal,
    RequestContext,
)
from tide.runtime.errors import ConcurrencyError, IdempotencyConflict
from tide.services import (
    ActionAuditEvent,
    ActionExecutionStore,
    ActionService,
    AuditHistoryService,
    AuditOutcome,
    AuditFieldChange,
    AuditValueMode,
    IdempotencyStatus,
    InMemoryActionExecutionStore,
    RecordAuditEvent,
    RecordAuditOperation,
    RecordsService,
)

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
SPEC = importlib.util.spec_from_file_location(
    "action_store_invoicing_actions",
    INVOICING / "actions.py",
)
assert SPEC and SPEC.loader
invoicing_actions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(invoicing_actions)


def test_in_memory_store_survives_action_service_recreation() -> None:
    store = InMemoryActionExecutionStore()
    model, repository, records = _memory_runtime()
    created = records.commit(
        records.create("sales.Invoice", _context("create"), _invoice_values()),
        _context("create"),
    )
    first = _actions(model, records, store)

    posted = first.execute(
        "sales.Invoice",
        "post",
        created["id"],
        {},
        _context("first"),
        idempotency_key="durable-post-1",
    )

    second = ActionService(model, records, execution_store=store)
    second.register(
        "actions.post_invoice",
        lambda _record, _context, _payload: pytest.fail(
            "a completed idempotent action must not execute again"
        ),
    )
    replayed = second.execute(
        "sales.Invoice",
        "post",
        created["id"],
        {},
        _context("replay"),
        idempotency_key="durable-post-1",
    )

    assert isinstance(store, ActionExecutionStore)
    assert posted["version"] == replayed["version"] == 2
    assert repository.get("sales.Invoice", created["id"])["status"] == "posted"
    idempotency = store.get_idempotency("durable-post-1")
    assert idempotency is not None
    assert idempotency.status is IdempotencyStatus.COMPLETED
    assert idempotency.finished_at is not None
    events = store.audit_events()
    assert [event.outcome for event in events] == [
        AuditOutcome.SUCCEEDED,
        AuditOutcome.REPLAYED,
    ]
    assert [event.correlation_id for event in events] == ["first", "replay"]
    assert all(event.idempotency_key_hash for event in events)
    assert all("durable-post-1" not in repr(event) for event in events)


def test_action_expected_version_is_checked_before_idempotency_claim() -> None:
    store = InMemoryActionExecutionStore()
    model, repository, records = _memory_runtime()
    created = records.commit(
        records.create("sales.Invoice", _context("create"), _invoice_values()),
        _context("create"),
    )
    actions = _actions(model, records, store)

    with pytest.raises(ConcurrencyError):
        actions.execute(
            "sales.Invoice",
            "post",
            created["id"],
            {},
            _context("stale"),
            idempotency_key="stale-post",
            expected_version=created["version"] - 1,
        )

    assert store.get_idempotency("stale-post") is None
    assert repository.get("sales.Invoice", created["id"])["status"] == "draft"

    posted = actions.execute(
        "sales.Invoice",
        "post",
        created["id"],
        {},
        _context("current"),
        idempotency_key="stale-post",
        expected_version=created["version"],
    )

    assert posted["status"] == "posted"
    assert posted["version"] == created["version"] + 1


def test_fingerprint_preserves_payload_value_types() -> None:
    store = InMemoryActionExecutionStore()
    model, _, records = _memory_runtime()
    created = records.commit(
        records.create("sales.Invoice", _context("create"), _invoice_values()),
        _context("create"),
    )
    actions = _actions(model, records, store)
    occurred_at = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)
    actions.execute(
        "sales.Invoice",
        "post",
        created["id"],
        {"occurred_at": occurred_at},
        _context("first"),
        idempotency_key="typed-payload",
    )

    with pytest.raises(IdempotencyConflict, match="different request"):
        actions.execute(
            "sales.Invoice",
            "post",
            created["id"],
            {"occurred_at": occurred_at.isoformat()},
            _context("conflict"),
            idempotency_key="typed-payload",
        )

    assert (
        store.audit_events(correlation_id="conflict")[0].outcome
        is AuditOutcome.CONFLICT
    )


def test_failed_handler_blocks_automatic_reexecution() -> None:
    store = InMemoryActionExecutionStore()
    model, _, records = _memory_runtime()
    created = records.commit(
        records.create("sales.Invoice", _context("create"), _invoice_values()),
        _context("create"),
    )
    attempts = 0

    def fail_handler(
        _record: dict[str, Any],
        _context: RequestContext,
        _payload: dict[str, Any],
    ) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("simulated handler interruption")

    actions = ActionService(model, records, execution_store=store)
    actions.register("actions.post_invoice", fail_handler)
    with pytest.raises(RuntimeError, match="simulated handler interruption"):
        actions.execute(
            "sales.Invoice",
            "post",
            created["id"],
            {},
            _context("failed"),
            idempotency_key="failed-post",
        )

    with pytest.raises(IdempotencyConflict, match="requires reconciliation"):
        actions.execute(
            "sales.Invoice",
            "post",
            created["id"],
            {},
            _context("retry"),
            idempotency_key="failed-post",
        )

    assert attempts == 1
    idempotency = store.get_idempotency("failed-post")
    assert idempotency is not None
    assert idempotency.status is IdempotencyStatus.FAILED
    assert idempotency.error_code == "internal_error"
    assert [event.outcome for event in store.audit_events()] == [
        AuditOutcome.FAILED,
        AuditOutcome.CONFLICT,
    ]


def test_disabled_action_does_not_reserve_idempotency_key() -> None:
    store = InMemoryActionExecutionStore()
    model, _, records = _memory_runtime()
    created = records.commit(
        records.create(
            "sales.Invoice",
            _context("create"),
            _invoice_values(lines=False),
        ),
        _context("create"),
    )
    actions = _actions(model, records, store)

    with pytest.raises(ActionDisabled):
        actions.execute(
            "sales.Invoice",
            "post",
            created["id"],
            {},
            _context("disabled"),
            idempotency_key="disabled-post",
        )

    assert store.get_idempotency("disabled-post") is None
    event = store.audit_events()[0]
    assert event.outcome is AuditOutcome.FAILED
    assert event.error_code == "action_disabled"


def test_action_audit_can_be_explicitly_disabled() -> None:
    model = compile_project(INVOICING)
    invoice = model.entity("sales.Invoice")
    actions = {name: dict(value) for name, value in invoice.actions.items()}
    actions["post"]["audit"] = False
    entities = dict(model.entities)
    entities[invoice.name] = replace(invoice, actions=immutable_mapping(actions))
    model = replace(model, entities=immutable_mapping(entities))
    repository = InMemoryRepository()
    _seed(repository)
    records = _records(model, repository)
    store = InMemoryActionExecutionStore()
    created = records.commit(
        records.create("sales.Invoice", _context("create"), _invoice_values()),
        _context("create"),
    )

    _actions(model, records, store).execute(
        "sales.Invoice",
        "post",
        created["id"],
        {},
        _context("not-audited"),
        idempotency_key="not-audited-post",
    )

    assert store.audit_events() == ()
    assert store.get_idempotency("not-audited-post") is not None


def test_sql_store_persists_replay_and_audit_across_restart(tmp_path: Path) -> None:
    database = tmp_path / "durable-actions.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(model, url)
    repository.create_schema()
    store = SQLAlchemyActionExecutionStore(repository.engine, mode="managed")
    assert inspect(store.engine).get_table_names() == [
        "catalog_product",
        "crm_customer",
        "sales_invoice",
        "sales_invoice_line",
    ]
    store.create_schema()
    store.validate_schema()
    _seed(repository)
    records = _records(model, repository)
    created = records.commit(
        records.create("sales.Invoice", _context("create"), _invoice_values()),
        _context("create"),
    )
    _actions(model, records, store).execute(
        "sales.Invoice",
        "post",
        created["id"],
        {},
        _context("sql-first"),
        idempotency_key="sql-durable-post",
    )
    repository.dispose()

    restarted_repository = SQLAlchemyRepository(model, url)
    restarted_store = SQLAlchemyActionExecutionStore(
        restarted_repository.engine,
        mode="legacy",
    )
    restarted_store.validate_schema()
    restarted_records = RecordsService(model, restarted_repository)
    restarted_actions = ActionService(
        model,
        restarted_records,
        execution_store=restarted_store,
    )
    restarted_actions.register(
        "actions.post_invoice",
        lambda _record, _context, _payload: pytest.fail(
            "a durable completed action must replay without its handler"
        ),
    )
    replayed = restarted_actions.execute(
        "sales.Invoice",
        "post",
        created["id"],
        {},
        _context("sql-replay"),
        idempotency_key="sql-durable-post",
    )

    assert replayed["version"] == 2
    assert (
        restarted_store.get_idempotency("sql-durable-post").status
        is IdempotencyStatus.COMPLETED
    )  # type: ignore[union-attr]
    assert [event.outcome for event in restarted_store.audit_events()] == [
        AuditOutcome.SUCCEEDED,
        AuditOutcome.REPLAYED,
    ]
    restarted_repository.dispose()


def test_sql_action_store_defaults_to_no_ddl() -> None:
    store = SQLAlchemyActionExecutionStore("sqlite+pysqlite:///:memory:")

    assert inspect(store.engine).get_table_names() == []
    with pytest.raises(SchemaManagementError):
        store.create_schema()
    assert len(store.schema_issues()) == 3
    assert inspect(store.engine).get_table_names() == []
    store.dispose()


def test_audit_events_preserve_begin_order_when_timestamps_match() -> None:
    sql_store = SQLAlchemyActionExecutionStore(
        "sqlite+pysqlite:///:memory:",
        mode="managed",
    )
    sql_store.create_schema()
    stores: tuple[ActionExecutionStore, ...] = (
        InMemoryActionExecutionStore(),
        sql_store,
    )
    started_at = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)

    for store in stores:
        for event_id in ("z-first", "a-second"):
            store.begin_audit(
                ActionAuditEvent(
                    event_id=event_id,
                    entity="sales.Invoice",
                    action="post",
                    identity=1,
                    principal="user:clerk",
                    channel="tui",
                    correlation_id=event_id,
                    started_at=started_at,
                )
            )
        assert [event.event_id for event in store.audit_events()] == [
            "z-first",
            "a-second",
        ]

    sql_store.dispose()


def test_audit_history_is_bounded_filtered_newest_first_and_authorized() -> None:
    sql_store = SQLAlchemyActionExecutionStore(
        "sqlite+pysqlite:///:memory:",
        mode="managed",
    )
    sql_store.create_schema()
    stores: tuple[ActionExecutionStore, ...] = (
        InMemoryActionExecutionStore(),
        sql_store,
    )
    started_at = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    events = (
        ("invoice-1-first", "sales.Invoice", 1),
        ("invoice-2", "sales.Invoice", 2),
        ("invoice-1-latest", "sales.Invoice", 1),
        ("product-1", "catalog.Product", 1),
    )

    model = compile_project(INVOICING)
    auditor = RequestContext(
        Principal("user:auditor", roles=frozenset({"auditor"})),
        channel=Channel.TUI,
    )
    clerk = RequestContext(
        Principal("user:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )
    for store in stores:
        for event_id, entity, identity in events:
            store.begin_audit(
                ActionAuditEvent(
                    event_id=event_id,
                    entity=entity,
                    action="post",
                    identity=identity,
                    principal="user:clerk",
                    channel="tui",
                    correlation_id=event_id,
                    started_at=started_at,
                )
            )
        service = AuditHistoryService(model, store)
        assert service.can_view("sales.Invoice", auditor)
        assert not service.can_view("sales.Invoice", clerk)
        assert [
            event.event_id
            for event in service.for_record(
                "sales.Invoice",
                1,
                auditor,
                limit=1,
            )
        ] == ["invoice-1-latest"]
        with pytest.raises(AuthorizationError):
            service.for_record("sales.Invoice", 1, clerk)
        with pytest.raises(ValueError, match="between 1 and 500"):
            service.for_record("sales.Invoice", 1, auditor, limit=0)

    sql_store.dispose()


def test_record_audit_store_round_trips_typed_changes_and_filters() -> None:
    sql_store = SQLAlchemyActionExecutionStore(
        "sqlite+pysqlite:///:memory:",
        mode="managed",
    )
    sql_store.create_schema()
    stores: tuple[ActionExecutionStore, ...] = (
        InMemoryActionExecutionStore(),
        sql_store,
    )
    occurred_at = datetime(2026, 7, 18, 13, 0, tzinfo=timezone.utc)

    for store in stores:
        for event_id, identity, amount in (
            ("first", 1, Decimal("1000.25")),
            ("other", 2, Decimal("20.00")),
            ("latest", 1, Decimal("1001.25")),
        ):
            store.record_audit(
                RecordAuditEvent(
                    event_id=event_id,
                    entity="sales.Invoice",
                    operation=RecordAuditOperation.UPDATE,
                    identity=identity,
                    principal="user:clerk",
                    channel="tui",
                    correlation_id=event_id,
                    occurred_at=occurred_at,
                    source="user",
                    changes=(
                        AuditFieldChange(
                            field="total",
                            before_present=True,
                            after_present=True,
                            value_mode=AuditValueMode.RECORDED,
                            before=date(2026, 7, 17),
                            after=amount,
                        ),
                    ),
                )
            )
        events = store.record_audit_events(
            entity="sales.Invoice",
            identity=1,
            newest_first=True,
            limit=1,
        )
        assert [event.event_id for event in events] == ["latest"]
        assert events[0].changes[0].before == date(2026, 7, 17)
        assert events[0].changes[0].after == Decimal("1001.25")
        with pytest.raises(ValueError, match="between 1 and 500"):
            store.record_audit_events(limit=501)

    sql_store.dispose()


def test_records_service_audits_crud_and_redacts_protected_values() -> None:
    store = InMemoryActionExecutionStore()
    model, repository, records = _memory_runtime()
    records.audit_store = store
    created = records.commit(
        records.create("sales.Invoice", _context("invoice-create"), _invoice_values()),
        _context("invoice-create"),
    )
    edit = records.begin_edit("sales.Invoice", created["id"], _context("invoice-edit"))
    edit.set("invoice_date", date(2026, 7, 16))
    records.commit(edit, _context("invoice-edit"))
    _actions(model, records, store).execute(
        "sales.Invoice",
        "post",
        created["id"],
        {},
        _context("invoice-post"),
        idempotency_key="record-audit-post",
    )

    record_events = store.record_audit_events(
        entity="sales.Invoice",
        identity=created["id"],
    )
    assert [event.operation for event in record_events] == [
        RecordAuditOperation.CREATE,
        RecordAuditOperation.UPDATE,
        RecordAuditOperation.UPDATE,
    ]
    created_number = next(
        change for change in record_events[0].changes if change.field == "number"
    )
    assert not created_number.before_present
    assert created_number.after == created["number"]
    edited_date = next(
        change for change in record_events[1].changes if change.field == "invoice_date"
    )
    assert edited_date.before == date(2026, 7, 15)
    assert edited_date.after == date(2026, 7, 16)
    post_event = record_events[2]
    assert post_event.source == "action"
    assert post_event.correlation_id == "invoice-post"
    posted_by = next(
        change for change in post_event.changes if change.field == "posted_by"
    )
    assert posted_by.value_mode is AuditValueMode.REDACTED
    assert posted_by.before is posted_by.after is None

    auditor = RequestContext(
        Principal("user:auditor", roles=frozenset({"auditor"})),
        channel=Channel.TUI,
    )
    combined = AuditHistoryService(model, store).for_record(
        "sales.Invoice",
        created["id"],
        auditor,
    )
    assert len(combined) == 4
    assert {event.correlation_id for event in combined} == {
        "invoice-create",
        "invoice-edit",
        "invoice-post",
    }

    product = records.commit(
        records.create(
            "catalog.Product",
            _context("product-create"),
            {
                "code": "DELETE-ME",
                "name": "Temporary product",
                "unit_price": Decimal("1.25"),
            },
        ),
        _context("product-create"),
    )
    records.delete(
        "catalog.Product",
        product["id"],
        _context("product-delete"),
    )
    deleted = store.record_audit_events(
        entity="catalog.Product",
        identity=product["id"],
        newest_first=True,
        limit=1,
    )[0]
    assert deleted.operation is RecordAuditOperation.DELETE
    assert all(change.before_present and not change.after_present for change in deleted.changes)
    assert all(
        record["id"] != product["id"]
        for record in repository.all("catalog.Product")
    )


def test_audit_history_redacts_stored_values_from_field_policy() -> None:
    model = compile_project(INVOICING)
    store = InMemoryActionExecutionStore()
    store.record_audit(
        RecordAuditEvent(
            event_id="protected-change",
            entity="sales.Invoice",
            operation=RecordAuditOperation.UPDATE,
            identity=1,
            principal="user:clerk",
            channel="rest",
            correlation_id="protected-change",
            occurred_at=datetime(2026, 7, 18, 14, 0, tzinfo=timezone.utc),
            source="action",
            changes=(
                AuditFieldChange(
                    field="lines",
                    before_present=True,
                    after_present=True,
                    value_mode=AuditValueMode.RECORDED,
                    before=None,
                    after=[{"description": "secret detail"}],
                ),
            ),
        )
    )
    audit_only = RequestContext(
        Principal(
            "user:audit-only",
            permissions=frozenset({"sales.invoice.audit"}),
        ),
        channel=Channel.REST,
    )

    event = AuditHistoryService(model, store).for_record(
        "sales.Invoice",
        1,
        audit_only,
    )[0]

    assert isinstance(event, RecordAuditEvent)
    assert event.changes[0].value_mode is AuditValueMode.REDACTED
    assert event.changes[0].after is None
    assert "secret detail" not in repr(event)


def _memory_runtime() -> tuple[Any, InMemoryRepository, RecordsService]:
    model = compile_project(INVOICING)
    repository = InMemoryRepository()
    _seed(repository)
    return model, repository, _records(model, repository)


def _records(model: Any, repository: Any) -> RecordsService:
    records = RecordsService(model, repository)
    records.register_generator(
        "actions.allocate_invoice_number",
        lambda values, _context, repo: invoicing_actions.allocate_invoice_number(
            repo.peek_next_identity("sales.Invoice"),
            values["invoice_date"],
        ),
    )
    return records


def _actions(
    model: Any,
    records: RecordsService,
    store: ActionExecutionStore,
) -> ActionService:
    actions = ActionService(model, records, execution_store=store)
    actions.register(
        "actions.post_invoice",
        lambda record, context, payload: invoicing_actions.post_invoice(
            record,
            principal=context.principal.identifier,
            occurred_at=payload.get("occurred_at"),
        ),
    )
    return actions


def _seed(repository: Any) -> None:
    repository.seed(
        "crm.Customer",
        [
            {
                "id": 1,
                "code": "ACME",
                "name": "ACME Ltd",
                "email": None,
                "active": True,
            }
        ],
    )
    repository.seed(
        "catalog.Product",
        [
            {
                "id": 1,
                "code": "CONS",
                "name": "Consulting",
                "unit_price": Decimal("4.20"),
                "active": True,
            }
        ],
    )


def _context(correlation_id: str) -> RequestContext:
    return RequestContext(
        principal=Principal("user:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
        correlation_id=correlation_id,
    )


def _invoice_values(*, lines: bool = True) -> dict[str, Any]:
    return {
        "invoice_date": date(2026, 7, 15),
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
