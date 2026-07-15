from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterator

import pytest
from sqlalchemy import event

from tide import compile_project
from tide.compiler.normalized import ApplicationModel, deep_thaw, immutable_mapping
from tide.data import InMemoryRepository, QuerySpec, SQLAlchemyRepository, SortField
from tide.runtime import (
    Channel,
    Principal,
    RelationshipExpansionLimit,
    RequestContext,
)
from tide.security import PROTECTED
from tide.services import RecordsService

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def context() -> RequestContext:
    return RequestContext(
        principal=Principal("user:clerk", roles=frozenset({"sales_clerk"})),
        channel=Channel.TUI,
    )


def summary_context() -> RequestContext:
    return RequestContext(
        principal=Principal("user:summary", roles=frozenset({"summary_viewer"})),
        channel=Channel.TUI,
    )


def model_with_line_read_policy() -> ApplicationModel:
    model = compile_project(INVOICING)
    return replace(
        model,
        row_policies=(
            *model.row_policies,
            {
                "id": "substantial_invoice_lines",
                "entity": "sales.InvoiceLine",
                "operations": ("read",),
                "criteria": "quantity >= 2",
            },
        ),
    )


def model_with_separate_line_permission() -> ApplicationModel:
    model = compile_project(INVOICING)
    line = model.entity("sales.InvoiceLine")
    metadata = deep_thaw(line.metadata)
    metadata["permissions"]["read"] = "sales.invoice.line.read"
    entities = dict(model.entities)
    entities[line.name] = replace(line, metadata=immutable_mapping(metadata))
    return replace(model, entities=immutable_mapping(entities))


def invoice() -> dict[str, object]:
    return {
        "id": 10,
        "number": "INV-RELATIONSHIPS",
        "invoice_date": date(2026, 7, 15),
        "currency": "EUR",
        "status": "draft",
        "version": 1,
        "customer": 1,
        "total": Decimal("3.00"),
        "lines": [
            {
                "id": 101,
                "line_number": 1,
                "description": "Denied detail",
                "quantity": Decimal("1"),
                "unit_price": Decimal("1.00"),
                "product": 1,
                "total": Decimal("1.00"),
            },
            {
                "id": 102,
                "line_number": 2,
                "description": "Visible detail",
                "quantity": Decimal("2"),
                "unit_price": Decimal("1.00"),
                "product": 1,
                "total": Decimal("2.00"),
            },
        ],
    }


RuntimeFactory = Callable[..., tuple[RecordsService, object]]


@pytest.fixture(params=("memory", "sql"))
def runtime_factory(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[str, RuntimeFactory]]:
    repositories: list[SQLAlchemyRepository] = []

    def create(
        model: ApplicationModel,
        **service_options: int,
    ) -> tuple[RecordsService, object]:
        if request.param == "memory":
            repository: InMemoryRepository | SQLAlchemyRepository = (
                InMemoryRepository()
            )
            customer = {
                "id": 1,
                "code": "ACME",
                "name": "ACME Ltd",
                "email": None,
                "active": True,
                "invoices": [invoice()],
            }
        else:
            repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
            repository.create_schema()
            repositories.append(repository)
            customer = {
                "id": 1,
                "code": "ACME",
                "name": "ACME Ltd",
                "email": None,
                "active": True,
            }
        repository.seed("crm.Customer", [customer])
        repository.seed(
            "catalog.Product",
            [
                {
                    "id": 1,
                    "code": "TEST",
                    "name": "Test product",
                    "unit_price": Decimal("1.00"),
                    "active": True,
                }
            ],
        )
        repository.seed("sales.Invoice", [invoice()])
        return RecordsService(model, repository, **service_options), repository

    yield str(request.param), create
    for repository in repositories:
        repository.dispose()


def test_child_read_policy_filters_relationship_before_projection(
    runtime_factory: tuple[str, RuntimeFactory],
) -> None:
    adapter, create = runtime_factory
    records, repository = create(model_with_line_read_policy())
    statements: list[str] = []
    if isinstance(repository, SQLAlchemyRepository):

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

    result = records.get("sales.Invoice", 10, context())
    listed = records.query(
        "sales.Invoice",
        QuerySpec(sort=(SortField("id"),)),
        context(),
    )

    assert [line["description"] for line in result["lines"]] == ["Visible detail"]
    assert [line["description"] for line in listed[0]["lines"]] == [
        "Visible detail"
    ]
    if adapter == "sql":
        child_query = next(
            statement
            for statement in statements
            if "FROM SALES_INVOICE_LINE" in statement
        )
        assert "SALES_INVOICE_LINE.QUANTITY >=" in child_query


def test_root_aggregate_uses_the_child_read_policy(
    runtime_factory: tuple[str, RuntimeFactory],
) -> None:
    adapter, create = runtime_factory
    model = model_with_line_read_policy()
    model = replace(
        model,
        row_policies=(
            *model.row_policies,
            {
                "id": "one_visible_invoice_line",
                "entity": "sales.Invoice",
                "operations": ("list",),
                "criteria": "count(lines) == 1",
            },
        ),
    )
    records, repository = create(model)
    statements: list[str] = []
    if isinstance(repository, SQLAlchemyRepository):

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

    result = records.query(
        "sales.Invoice",
        QuerySpec(sort=(SortField("id"),)),
        context(),
    )

    assert [record["id"] for record in result] == [10]
    if adapter == "sql":
        root_query = next(
            statement
            for statement in statements
            if "FROM SALES_INVOICE" in statement and "ORDER BY" in statement
        )
        assert "TIDE_REL_1.QUANTITY >=" in root_query


def test_target_entity_permission_prevents_relationship_load(
    runtime_factory: tuple[str, RuntimeFactory],
) -> None:
    adapter, create = runtime_factory
    records, repository = create(model_with_separate_line_permission())
    statements: list[str] = []
    if isinstance(repository, SQLAlchemyRepository):

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

    result = records.get("sales.Invoice", 10, context())

    assert result["lines"] == PROTECTED
    if adapter == "sql":
        assert not any("FROM SALES_INVOICE_LINE" in sql for sql in statements)


def test_source_field_permission_prevents_relationship_load(
    runtime_factory: tuple[str, RuntimeFactory],
) -> None:
    adapter, create = runtime_factory
    records, repository = create(compile_project(INVOICING))
    statements: list[str] = []
    if isinstance(repository, SQLAlchemyRepository):

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

    result = records.get("sales.Invoice", 10, summary_context())

    assert result["lines"] == PROTECTED
    if adapter == "sql":
        assert not any("FROM SALES_INVOICE_LINE" in sql for sql in statements)


def test_relationship_item_limit_fails_instead_of_truncating(
    runtime_factory: tuple[str, RuntimeFactory],
) -> None:
    _adapter, create = runtime_factory
    records, _repository = create(
        compile_project(INVOICING),
        relationship_max_items=1,
    )

    with pytest.raises(RelationshipExpansionLimit, match="item"):
        records.get("sales.Invoice", 10, context())


def test_relationship_depth_limit_fails_instead_of_returning_partial_data(
    runtime_factory: tuple[str, RuntimeFactory],
) -> None:
    _adapter, create = runtime_factory
    records, _repository = create(
        compile_project(INVOICING),
        relationship_max_depth=1,
    )

    with pytest.raises(RelationshipExpansionLimit, match="depth"):
        records.get("crm.Customer", 1, context())
