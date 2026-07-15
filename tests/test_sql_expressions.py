from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tide import compile_project
from tide.data import (
    QuerySpec,
    QueryTranslationError,
    RelationshipLoadPlan,
    SQLAlchemyRepository,
)
from tide.data.sql_expressions import translate_expression

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def test_sql_expression_preserves_decimal_source_token_as_bound_value() -> None:
    model = compile_project(INVOICING)
    entity = model.entity("catalog.Product")
    repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")

    predicate = translate_expression(
        "unit_price >= 9999999999999999.99",
        model=model,
        entity=entity,
        columns=repository.table(entity.name).c,
    )
    compiled = predicate.compile()

    assert Decimal("9999999999999999.99") in compiled.params.values()
    repository.dispose()


def test_sql_repository_preflights_collection_row_policy_translation() -> None:
    model = compile_project(INVOICING)
    protected = replace(
        model,
        row_policies=(
            {
                "id": "customers_with_invoices",
                "entity": "crm.Customer",
                "operations": ("list",),
                "criteria": "count(invoices) > 0",
            },
        ),
    )
    repository = SQLAlchemyRepository(protected, "sqlite+pysqlite:///:memory:")

    repository.validate_query_support()

    repository.dispose()


def test_sql_relationship_paths_include_target_read_criteria() -> None:
    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
    statement = repository._query_statement(
        "sales.Invoice",
        QuerySpec(),
        row_criteria=("customer.name == 'ACME Ltd'",),
        relationships=RelationshipLoadPlan(
            entity_criteria=(("crm.Customer", ("active == true",)),),
        ),
    )
    sql = str(statement).upper()

    assert "TIDE_REL_1.ACTIVE = TRUE" in sql
    repository.dispose()


def test_nested_target_criteria_use_distinct_relationship_aliases() -> None:
    model = compile_project(INVOICING)
    repository = SQLAlchemyRepository(model, "sqlite+pysqlite:///:memory:")
    statement = repository._query_statement(
        "sales.Invoice",
        QuerySpec(),
        row_criteria=("count(lines) == 1",),
        relationships=RelationshipLoadPlan(
            entity_criteria=(
                ("sales.InvoiceLine", ("product.active == true",)),
            ),
        ),
    )
    sql = str(statement).upper()

    assert "SALES_INVOICE_LINE AS TIDE_REL_1" in sql
    assert "CATALOG_PRODUCT AS TIDE_REL_1_POLICY_1" in sql
    repository.dispose()


def test_sql_repository_preflight_fails_closed_for_multiple_collections() -> None:
    model = compile_project(INVOICING)
    unsupported = replace(
        model,
        row_policies=(
            {
                "id": "customers_with_line_items",
                "entity": "crm.Customer",
                "operations": ("list",),
                "criteria": "count(invoices.lines) > 0",
            },
        ),
    )
    repository = SQLAlchemyRepository(unsupported, "sqlite+pysqlite:///:memory:")

    with pytest.raises(
        QueryTranslationError,
        match="row policy 'customers_with_line_items' cannot run in SQL",
    ):
        repository.validate_query_support()

    repository.dispose()
