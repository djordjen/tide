from __future__ import annotations

import importlib.util
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.pool import StaticPool

from tide import compile_project
from tide.compiler.normalized import deep_thaw, immutable_mapping
from tide.data import (
    DeleteReference,
    FilterCondition,
    InMemoryRepository,
    QuerySpec,
    Repository,
    SQLAlchemyRepository,
    SchemaCompatibilityError,
    SchemaManagementError,
    SortField,
)
from tide.runtime import (
    AuthorizationError,
    Channel,
    ConcurrencyError,
    DeleteRestricted,
    Principal,
    RequestContext,
)
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
CUSTOMERS = [
    {
        "id": 1,
        "code": "ACME",
        "name": "ACME Ltd",
        "email": None,
        "active": True,
    },
    {
        "id": 2,
        "code": "OLD",
        "name": "Old Ltd",
        "email": None,
        "active": False,
    },
    {
        "id": 3,
        "code": "ZEN",
        "name": "Zen Ltd",
        "email": None,
        "active": True,
    },
]


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


def _seed_invoice(repository: SQLAlchemyRepository) -> None:
    repository.seed(
        "sales.Invoice",
        [
            {
                "id": 100,
                "number": "INV-TEST",
                "invoice_date": date(2026, 7, 15),
                "currency": "EUR",
                "status": "draft",
                "customer": 1,
                "total": Decimal("10.50"),
                "lines": [
                    {
                        "id": 1000,
                        "line_number": 1,
                        "description": "Consulting",
                        "quantity": Decimal("2.5"),
                        "unit_price": Decimal("4.20"),
                        "product": 1,
                        "total": Decimal("10.50"),
                    }
                ],
            }
        ],
    )


def test_sqlalchemy_readiness_requires_a_compatible_schema() -> None:
    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
    try:
        with pytest.raises(SchemaCompatibilityError):
            repository.check_readiness()

        repository.create_schema()
        repository.check_readiness()
    finally:
        repository.dispose()


@pytest.fixture
def sql_runtime():
    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")

    assert inspect(repository.engine).get_table_names() == []
    repository.create_schema()
    repository.validate_schema()
    repository.validate_query_support()
    repository.seed(
        "crm.Customer",
        CUSTOMERS,
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


def test_managed_delete_honors_restrict_and_removes_unused_rows(sql_runtime) -> None:
    _, repository, records = sql_runtime
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
    _seed_invoice(repository)

    records.delete("catalog.Product", 2, context())
    assert not repository.exists("catalog.Product", 2)

    with pytest.raises(DeleteRestricted) as restricted:
        records.delete("catalog.Product", 1, context())
    assert restricted.value.relationship == "sales.InvoiceLine.product"
    assert repository.exists("catalog.Product", 1)


def test_managed_delete_cascades_in_one_service_transaction(sql_runtime) -> None:
    model, repository, _ = sql_runtime
    _seed_invoice(repository)
    invoice = model.entity("sales.Invoice")
    metadata = deep_thaw(invoice.metadata)
    metadata["permissions"]["delete"] = "sales.invoice.write"
    entities = dict(model.entities)
    entities[invoice.name] = replace(
        invoice,
        metadata=immutable_mapping(metadata),
    )
    secured = replace(model, entities=immutable_mapping(entities))

    RecordsService(secured, repository).delete(
        "sales.Invoice",
        100,
        context(),
        expected_version=1,
    )

    assert not repository.exists("sales.Invoice", 100)
    assert not repository.exists("sales.InvoiceLine", 1000)


def test_sql_query_pushes_policy_filter_sort_and_limit_to_database(sql_runtime) -> None:
    model, repository, records = sql_runtime
    statements: list[str] = []

    @event.listens_for(repository.engine, "before_cursor_execute")
    def capture_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append(statement.upper())

    query = QuerySpec(
        filters=(FilterCondition("name", "contains", "Ltd"),),
        sort=(SortField("name", descending=True),),
        limit=1,
    )
    customers = records.query("crm.Customer", query, context())

    memory = InMemoryRepository()
    memory.seed("crm.Customer", CUSTOMERS)
    memory_customers = RecordsService(model, memory).query(
        "crm.Customer", query, context()
    )

    assert [customer["code"] for customer in customers] == ["ZEN"]
    assert [customer["code"] for customer in memory_customers] == ["ZEN"]
    root_query = next(
        statement for statement in statements if "FROM CRM_CUSTOMER" in statement
    )
    assert "CRM_CUSTOMER.ACTIVE = 1" in root_query
    assert "CRM_CUSTOMER.NAME LIKE" in root_query
    assert "ORDER BY" in root_query
    assert "LIMIT" in root_query
    assert "LTD" not in root_query


def test_case_insensitive_lookup_filter_matches_memory_and_sql(sql_runtime) -> None:
    model, _repository, records = sql_runtime
    query = QuerySpec(
        filters=(FilterCondition("name", "icontains", "lTd"),),
        sort=(SortField("name"),),
        limit=10,
    )

    sql_customers = records.query("crm.Customer", query, context())
    memory = InMemoryRepository()
    memory.seed("crm.Customer", CUSTOMERS)
    memory_customers = RecordsService(model, memory).query(
        "crm.Customer",
        query,
        context(),
    )

    assert [customer["code"] for customer in sql_customers] == ["ACME", "ZEN"]
    assert [customer["code"] for customer in memory_customers] == ["ACME", "ZEN"]


def test_sql_query_translates_relationship_aggregates(sql_runtime) -> None:
    model, repository, _ = sql_runtime
    _seed_invoice(repository)

    customers = repository.query(
        "crm.Customer",
        QuerySpec(sort=(SortField("id"),)),
        row_criteria=("count(invoices) > 0",),
    )
    invoices = repository.query(
        "sales.Invoice",
        QuerySpec(sort=(SortField("id"),)),
        row_criteria=(
            "count(lines) == 1 "
            "and sum(lines.total) == 10.50 "
            "and average(lines.quantity) == 2.5 "
            "and min(lines.total) == 10.50 "
            "and max(lines.total) == 10.50 "
            "and any(lines.product.active) "
            "and all(lines.product.active)",
        ),
    )
    lines = repository.query(
        "sales.InvoiceLine",
        QuerySpec(sort=(SortField("id"),)),
        row_criteria=("invoice.status == 'draft'",),
    )

    assert [customer["code"] for customer in customers] == ["ACME"]
    assert [invoice["number"] for invoice in invoices] == ["INV-TEST"]
    assert [line["description"] for line in lines] == ["Consulting"]

    protected_model = replace(
        model,
        row_policies=(
            *model.row_policies,
            {
                "id": "customers_with_invoices",
                "entity": "crm.Customer",
                "operations": ("list",),
                "criteria": "count(invoices) > 0",
            },
            {
                "id": "invoices_with_active_products",
                "entity": "sales.Invoice",
                "operations": ("list",),
                "criteria": (
                    "sum(lines.total) == 10.50 "
                    "and any(lines.product.active) "
                    "and all(lines.product.active)"
                ),
            },
            {
                "id": "draft_invoice_lines",
                "entity": "sales.InvoiceLine",
                "operations": ("list",),
                "criteria": "invoice.status == 'draft'",
            },
        ),
    )
    secured = RecordsService(protected_model, repository)

    secured_customers = secured.query(
        "crm.Customer", QuerySpec(sort=(SortField("id"),)), context()
    )
    secured_invoices = secured.query(
        "sales.Invoice", QuerySpec(sort=(SortField("id"),)), context()
    )
    secured_lines = secured.query(
        "sales.InvoiceLine", QuerySpec(sort=(SortField("id"),)), context()
    )

    assert [customer["code"] for customer in secured_customers] == ["ACME"]
    assert [invoice["number"] for invoice in secured_invoices] == ["INV-TEST"]
    assert [line["description"] for line in secured_lines] == ["Consulting"]


def test_sql_single_record_load_pushes_row_policy_to_database(sql_runtime) -> None:
    _, repository, records = sql_runtime
    statements: list[str] = []

    @event.listens_for(repository.engine, "before_cursor_execute")
    def capture_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append(statement.upper())

    with pytest.raises(AuthorizationError):
        records.get("crm.Customer", 2, context())

    policy_query = next(
        statement
        for statement in statements
        if "FROM CRM_CUSTOMER" in statement and "CRM_CUSTOMER.ACTIVE" in statement
    )
    assert "CRM_CUSTOMER.ID =" in policy_query
    assert "CRM_CUSTOMER.ACTIVE = 1" in policy_query


def test_sql_update_rechecks_row_policy_in_atomic_update(sql_runtime) -> None:
    model, repository, _ = sql_runtime
    protected_model = replace(
        model,
        row_policies=(
            *model.row_policies,
            {
                "id": "active_customer_updates",
                "entity": "crm.Customer",
                "operations": ("update",),
                "criteria": "active == true",
            },
        ),
    )
    records = RecordsService(protected_model, repository)
    edit = records.begin_edit("crm.Customer", 1, context())

    current = repository.get("crm.Customer", 1)
    current["active"] = False
    repository.write(
        "crm.Customer",
        current,
        primary_key="id",
        version_field=None,
        expected_version=None,
        is_new=False,
    )
    statements: list[str] = []

    @event.listens_for(repository.engine, "before_cursor_execute")
    def capture_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append(statement.upper())

    edit.set("name", "Raced update")
    with pytest.raises(AuthorizationError):
        records.commit(edit, context())

    update_statement = next(
        statement for statement in statements if statement.startswith("UPDATE CRM_CUSTOMER")
    )
    assert "CRM_CUSTOMER.ACTIVE = 1" in update_statement
    persisted = repository.get("crm.Customer", 1)
    assert persisted["name"] == "ACME Ltd"
    assert persisted["active"] is False


def test_create_row_policy_checks_final_values_before_insert(sql_runtime) -> None:
    model, repository, _ = sql_runtime
    protected_model = replace(
        model,
        row_policies=(
            *model.row_policies,
            {
                "id": "active_customer_creation",
                "entity": "crm.Customer",
                "operations": ("create",),
                "criteria": "active == true",
            },
        ),
    )
    records = RecordsService(protected_model, repository)
    session = records.create(
        "crm.Customer",
        context(),
        {"code": "BLOCKED", "name": "Blocked", "active": False},
    )

    with pytest.raises(AuthorizationError):
        records.commit(session, context())

    assert all(
        customer["code"] != "BLOCKED" for customer in repository.all("crm.Customer")
    )


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

    reference = DeleteReference(
        source_entity="legacy.Customer",
        source_field="account_manager",
        source_primary_key="id",
        target_entity="legacy.Employee",
        on_delete="restrict",
    )
    with pytest.raises(DeleteRestricted):
        repository.delete(
            "legacy.Employee",
            10,
            primary_key="id",
            version_field=None,
            expected_version=None,
            references=(reference,),
        )
    repository.delete(
        "legacy.Customer",
        1,
        primary_key="id",
        version_field=None,
        expected_version=None,
        references=(reference,),
    )
    repository.delete(
        "legacy.Employee",
        10,
        primary_key="id",
        version_field=None,
        expected_version=None,
        references=(reference,),
    )
    assert not repository.exists("legacy.Customer", 1)
    assert not repository.exists("legacy.Employee", 10)
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
