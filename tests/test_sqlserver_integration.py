from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import event, inspect

from tide import compile_project
from tide.data import (
    QuerySpec,
    SQLAlchemyActionExecutionStore,
    SQLAlchemyRepository,
    SortField,
)
from tide.runtime import Channel, ConcurrencyError, Principal, RequestContext
from tide.services import (
    ActionAuditEvent,
    AuditOutcome,
    IdempotencyRecord,
    IdempotencyStatus,
    RecordsService,
)

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
SQLSERVER_URL = os.getenv("TIDE_TEST_SQLSERVER_URL")

pytestmark = [
    pytest.mark.sqlserver,
    pytest.mark.skipif(
        not SQLSERVER_URL,
        reason="TIDE_TEST_SQLSERVER_URL is not configured",
    ),
]


@pytest.fixture
def sqlserver_repository() -> Iterator[SQLAlchemyRepository]:
    assert SQLSERVER_URL is not None
    repository = SQLAlchemyRepository(compile_project(INVOICING), SQLSERVER_URL)
    mapped_tables = {table.name for table in repository.metadata.tables.values()}
    existing_tables = set(inspect(repository.engine).get_table_names())
    collisions = mapped_tables & existing_tables
    if collisions:
        repository.dispose()
        pytest.fail(
            "SQL Server integration tests require a dedicated empty database; "
            f"mapped tables already exist: {sorted(collisions)}"
        )

    schema_creation_started = False
    try:
        schema_creation_started = True
        repository.create_schema()
        repository.validate_schema()
        repository.validate_query_support()
        yield repository
    finally:
        if schema_creation_started:
            repository.metadata.drop_all(repository.engine)
        repository.dispose()


def test_sql_server_identity_unicode_decimal_policy_and_concurrency(
    sqlserver_repository: SQLAlchemyRepository,
) -> None:
    repository = sqlserver_repository
    repository.seed(
        "crm.Customer",
        [
            {
                "code": "MNE",
                "name": "Željko & Co",
                "email": None,
                "active": True,
            }
        ],
    )
    customer = repository.all("crm.Customer")[0]
    assert isinstance(customer["id"], int)
    assert customer["name"] == "Željko & Co"

    repository.seed(
        "sales.Invoice",
        [
            {
                "number": "SQLSERVER-1",
                "invoice_date": date(2026, 7, 15),
                "currency": "EUR",
                "status": "draft",
                "customer": customer["id"],
                "total": Decimal("1234.56"),
                "lines": [],
            }
        ],
    )
    invoice = repository.all("sales.Invoice")[0]
    assert invoice["total"] == Decimal("1234.56")
    assert invoice["version"] == 1

    visible = repository.query(
        "crm.Customer",
        QuerySpec(sort=(SortField("id"),)),
        row_criteria=("active == true and count(invoices) == 1",),
    )
    assert [record["id"] for record in visible] == [customer["id"]]

    invoice["status"] = "posted"
    updated = repository.write(
        "sales.Invoice",
        invoice,
        primary_key="id",
        version_field="version",
        expected_version=1,
        is_new=False,
        row_criteria=("status == 'draft'",),
    )
    assert updated["version"] == 2

    with pytest.raises(ConcurrencyError):
        repository.write(
            "sales.Invoice",
            invoice,
            primary_key="id",
            version_field="version",
            expected_version=1,
            is_new=False,
        )


def test_sql_server_keyset_boundary(sqlserver_repository: SQLAlchemyRepository) -> None:
    repository = sqlserver_repository
    repository.seed(
        "crm.Customer",
        [
            {"code": "D", "name": "Delta", "active": True},
            {"code": "A1", "name": "Alpha", "active": True},
            {"code": "A2", "name": "Alpha", "active": True},
            {"code": "G", "name": "Gamma", "active": True},
        ],
    )
    sort = (SortField("name"), SortField("id"))

    first = repository.query(
        "crm.Customer",
        QuerySpec(sort=sort, limit=2),
        row_criteria=("active == true",),
    )
    boundary = tuple(first[-1][field.field] for field in sort)
    second = repository.query(
        "crm.Customer",
        QuerySpec(sort=sort, limit=2, after=boundary),
        row_criteria=("active == true",),
    )

    assert [record["code"] for record in first] == ["A1", "A2"]
    assert [record["code"] for record in second] == ["D", "G"]


def test_sql_server_relationship_hydration_applies_target_policy(
    sqlserver_repository: SQLAlchemyRepository,
) -> None:
    repository = sqlserver_repository
    repository.seed(
        "crm.Customer",
        [{"code": "REL", "name": "Relationship test", "active": True}],
    )
    repository.seed(
        "catalog.Product",
        [
            {
                "code": "REL",
                "name": "Relationship product",
                "unit_price": Decimal("1.00"),
                "active": True,
            }
        ],
    )
    customer = repository.all("crm.Customer")[0]
    product = repository.all("catalog.Product")[0]
    repository.seed(
        "sales.Invoice",
        [
            {
                "number": "SQLSERVER-REL",
                "invoice_date": date(2026, 7, 15),
                "currency": "EUR",
                "status": "draft",
                "customer": customer["id"],
                "total": Decimal("3.00"),
                "lines": [
                    {
                        "line_number": 1,
                        "description": "Denied detail",
                        "quantity": Decimal("1"),
                        "unit_price": Decimal("1.00"),
                        "product": product["id"],
                        "total": Decimal("1.00"),
                    },
                    {
                        "line_number": 2,
                        "description": "Visible detail",
                        "quantity": Decimal("2"),
                        "unit_price": Decimal("1.00"),
                        "product": product["id"],
                        "total": Decimal("2.00"),
                    },
                ],
            }
        ],
    )
    invoice = repository.all("sales.Invoice")[0]
    protected_model = replace(
        repository.model,
        row_policies=(
            *repository.model.row_policies,
            {
                "id": "substantial_invoice_lines",
                "entity": "sales.InvoiceLine",
                "operations": ("read",),
                "criteria": "quantity >= 2",
            },
        ),
    )
    records = RecordsService(protected_model, repository)
    request_context = RequestContext(
        principal=Principal("user:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )
    statements: list[str] = []

    @event.listens_for(repository.engine, "before_cursor_execute")
    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.upper())

    result = records.get("sales.Invoice", invoice["id"], request_context)

    assert [line["description"] for line in result["lines"]] == ["Visible detail"]
    child_query = next(
        statement for statement in statements if "FROM SALES_INVOICE_LINE" in statement
    )
    assert "SALES_INVOICE_LINE.QUANTITY >=" in child_query


def test_sql_server_persists_action_idempotency_and_audit(
    sqlserver_repository: SQLAlchemyRepository,
) -> None:
    store = SQLAlchemyActionExecutionStore(
        sqlserver_repository.engine,
        mode="managed",
    )
    action_tables = {table.name for table in store.metadata.tables.values()}
    collisions = action_tables & set(inspect(store.engine).get_table_names())
    if collisions:
        pytest.fail(
            "SQL Server action-store tests require unused TIDE-owned tables; "
            f"collisions: {sorted(collisions)}"
        )
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    try:
        store.create_schema()
        store.validate_schema()
        claim = store.claim_idempotency(
            IdempotencyRecord(
                key="sqlserver-action-1",
                fingerprint="a" * 64,
                entity="sales.Invoice",
                identity=Decimal("123.45"),
                principal="user:sqlserver",
                correlation_id="sqlserver-correlation",
                status=IdempotencyStatus.IN_PROGRESS,
                started_at=now,
            )
        )
        store.complete_idempotency(
            claim.record.key,
            claim.record.fingerprint,
            finished_at=now,
        )
        store.begin_audit(
            ActionAuditEvent(
                event_id="sqlserver-event-1",
                entity="sales.Invoice",
                action="post",
                identity=Decimal("123.45"),
                principal="user:sqlserver",
                channel="rest",
                correlation_id="sqlserver-correlation",
                started_at=now,
            )
        )
        store.finish_audit(
            "sqlserver-event-1",
            outcome=AuditOutcome.SUCCEEDED,
            finished_at=now,
        )

        restarted = SQLAlchemyActionExecutionStore(
            sqlserver_repository.engine,
            mode="legacy",
        )
        restarted.validate_schema()
        persisted = restarted.get_idempotency("sqlserver-action-1")
        assert persisted is not None
        assert persisted.identity == Decimal("123.45")
        assert persisted.status is IdempotencyStatus.COMPLETED
        assert restarted.audit_events()[0].outcome is AuditOutcome.SUCCEEDED
    finally:
        store.metadata.drop_all(store.engine)
