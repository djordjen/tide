from __future__ import annotations

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.pool import StaticPool

from tide import compile_project
from tide.data import (
    InMemoryRepository,
    Repository,
    SQLAlchemyRepository,
    SchemaCompatibilityError,
    SchemaManagementError,
)
from tide.runtime import Channel, ConcurrencyError, Principal, RequestContext
from tide.services import ActionService, RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"
LEGACY = ROOT / "tests" / "fixtures" / "valid" / "legacy-database"
SPEC = importlib.util.spec_from_file_location(
    "sqlalchemy_invoicing_actions", INVOICING / "actions.py"
)
assert SPEC and SPEC.loader
invoicing_actions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(invoicing_actions)


def context() -> RequestContext:
    return RequestContext(
        principal=Principal("user:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )


def invoice_values() -> dict[str, Any]:
    return {
        "invoice_date": date(2026, 7, 15),
        "customer": 1,
        "lines": [
            {
                "line_number": 1,
                "description": "Consulting",
                "quantity": Decimal("2.5"),
                "unit_price": Decimal("4.20"),
                "product": 1,
            }
        ],
    }


@pytest.fixture
def sql_runtime():
    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")

    assert inspect(repository.engine).get_table_names() == []
    repository.create_schema()
    repository.validate_schema()
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
    records = RecordsService(model, repository)
    records.register_generator(
        "actions.allocate_invoice_number",
        lambda values, request_context, repo: invoicing_actions.allocate_invoice_number(
            repo.peek_next_identity("sales.Invoice"), values["invoice_date"]
        ),
    )
    yield model, repository, records
    repository.dispose()


def test_repository_protocol_accepts_memory_and_sqlalchemy(sql_runtime) -> None:
    _, repository, _ = sql_runtime

    assert isinstance(InMemoryRepository(), Repository)
    assert isinstance(repository, Repository)


def test_managed_schema_and_master_detail_round_trip(sql_runtime) -> None:
    model, repository, records = sql_runtime

    assert inspect(repository.engine).get_table_names() == [
        "catalog_product",
        "crm_customer",
        "sales_invoice",
        "sales_invoice_line",
    ]

    created = records.commit(
        records.create("sales.Invoice", context(), invoice_values()), context()
    )

    assert created["number"] == "INV-2026-000001"
    assert created["total"] == Decimal("10.50")
    assert created["version"] == 1
    assert created["lines"][0]["total"] == Decimal("10.50")

    fresh_service = RecordsService(model, repository)
    persisted = fresh_service.get("sales.Invoice", created["id"], context())
    assert persisted["customer"] == 1
    assert persisted["lines"][0]["product"] == 1
    assert persisted["lines"][0]["quantity"] == Decimal("2.500")
    assert repository.all("sales.InvoiceLine")[0]["invoice"] == created["id"]


def test_managed_collection_updates_and_orphan_deletes(sql_runtime) -> None:
    _, repository, records = sql_runtime
    created = records.commit(
        records.create("sales.Invoice", context(), invoice_values()), context()
    )

    edit = records.begin_edit("sales.Invoice", created["id"], context())
    edit.values["lines"][0]["quantity"] = Decimal("3")
    updated = records.commit(edit, context())

    assert updated["total"] == Decimal("12.60")
    assert repository.all("sales.InvoiceLine")[0]["quantity"] == Decimal("3.000")

    remove = records.begin_edit("sales.Invoice", created["id"], context())
    remove.set("lines", [])
    emptied = records.commit(remove, context())

    assert emptied["lines"] == []
    assert emptied["total"] == Decimal("0.00")
    assert repository.all("sales.InvoiceLine") == []


def test_managed_optimistic_concurrency_is_atomic(sql_runtime) -> None:
    _, _, records = sql_runtime
    created = records.commit(
        records.create("sales.Invoice", context(), invoice_values()), context()
    )
    first = records.begin_edit("sales.Invoice", created["id"], context())
    stale = records.begin_edit("sales.Invoice", created["id"], context())

    first.set("currency", "USD")
    assert records.commit(first, context())["version"] == 2

    stale.set("currency", "GBP")
    with pytest.raises(ConcurrencyError) as caught:
        records.commit(stale, context())

    assert caught.value.expected == 1
    assert caught.value.actual == 2


def test_managed_repository_runs_the_posting_action(sql_runtime) -> None:
    model, repository, records = sql_runtime
    actions = ActionService(model, records)
    actions.register(
        "actions.post_invoice",
        lambda record, request_context, payload: invoicing_actions.post_invoice(
            record,
            principal=request_context.principal.identifier,
            occurred_at=payload.get("occurred_at"),
        ),
    )
    created = records.commit(
        records.create("sales.Invoice", context(), invoice_values()), context()
    )

    posted = actions.execute(
        "sales.Invoice",
        "post",
        created["id"],
        {},
        context(),
        idempotency_key="sql-post-1",
    )
    retried = actions.execute(
        "sales.Invoice",
        "post",
        created["id"],
        {},
        context(),
        idempotency_key="sql-post-1",
    )

    assert posted["status"] == "posted"
    assert posted["version"] == 2
    assert retried["version"] == 2
    assert repository.get("sales.Invoice", created["id"])["posted_by"] == "user:clerk"


def test_legacy_mapping_reads_and_writes_without_ddl() -> None:
    model = compile_project(LEGACY)
    engine = _legacy_engine()
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append(statement.strip().upper())

    repository = SQLAlchemyRepository(model, engine)
    repository.validate_schema()

    with pytest.raises(SchemaManagementError):
        repository.create_schema()

    customer = repository.get("legacy.Customer", 1)
    assert customer == {"id": 1, "name": "Original", "account_manager": 10}

    updated = repository.write(
        "legacy.Customer",
        {"id": 1, "name": "Updated", "account_manager": 10},
        primary_key="id",
        version_field=None,
        expected_version=None,
        is_new=False,
    )
    assert updated["name"] == "Updated"
    assert not any(
        statement.startswith(("CREATE ", "ALTER ", "DROP "))
        for statement in statements
    )
    repository.dispose()


def test_legacy_schema_validation_reports_missing_mappings() -> None:
    model = compile_project(LEGACY)
    engine = _legacy_engine(missing_customer_name=True)
    repository = SQLAlchemyRepository(model, engine)

    with pytest.raises(SchemaCompatibilityError) as caught:
        repository.validate_schema()

    assert any(
        issue.object_name == "erp.CUSTOMER_MASTER.DISPLAY_NAME"
        and issue.message == "mapped column does not exist"
        for issue in caught.value.issues
    )
    repository.dispose()


def test_legacy_schema_validation_rejects_narrow_columns() -> None:
    model = compile_project(LEGACY)
    engine = _legacy_engine(customer_name_length=20)
    repository = SQLAlchemyRepository(model, engine)

    with pytest.raises(SchemaCompatibilityError) as caught:
        repository.validate_schema()

    assert any(
        issue.object_name == "erp.CUSTOMER_MASTER.DISPLAY_NAME"
        and "length 20 is smaller than required length 120" in issue.message
        for issue in caught.value.issues
    )
    repository.dispose()


def _legacy_engine(
    *,
    missing_customer_name: bool = False,
    customer_name_length: int = 120,
):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    customer_name = (
        ""
        if missing_customer_name
        else f", DISPLAY_NAME VARCHAR({customer_name_length}) NOT NULL"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS erp")
        connection.exec_driver_sql(
            "CREATE TABLE erp.EMPLOYEE_MASTER ("
            "EMPLOYEE_NO INTEGER PRIMARY KEY, DISPLAY_NAME VARCHAR(120) NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE erp.CUSTOMER_MASTER ("
            f"CUSTOMER_NO INTEGER PRIMARY KEY{customer_name}, "
            "OWNER_EMPLOYEE_NO INTEGER, "
            "FOREIGN KEY (OWNER_EMPLOYEE_NO) REFERENCES EMPLOYEE_MASTER(EMPLOYEE_NO))"
        )
        connection.exec_driver_sql(
            "INSERT INTO erp.EMPLOYEE_MASTER (EMPLOYEE_NO, DISPLAY_NAME) "
            "VALUES (10, 'Owner')"
        )
        if not missing_customer_name:
            connection.exec_driver_sql(
                "INSERT INTO erp.CUSTOMER_MASTER "
                "(CUSTOMER_NO, DISPLAY_NAME, OWNER_EMPLOYEE_NO) "
                "VALUES (1, 'Original', 10)"
            )
    return engine
