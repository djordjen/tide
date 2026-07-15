from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tide import compile_project
from tide.data import QueryTranslationError, SQLAlchemyRepository
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
