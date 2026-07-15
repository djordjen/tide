from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import MetaData
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateIndex, CreateTable

from tide import compile_project
from tide.data import (
    DatabaseDriverError,
    FilterCondition,
    QuerySpec,
    SQLAlchemyRepository,
    SortField,
)
from tide.data import sqlalchemy as sqlalchemy_adapter

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


@pytest.fixture
def repository() -> SQLAlchemyRepository:
    result = SQLAlchemyRepository(
        compile_project(INVOICING),
        "sqlite+pysqlite:///:memory:",
    )
    yield result
    result.dispose()


def test_managed_schema_compiles_to_sql_server_native_types() -> None:
    tables = sqlalchemy_adapter._build_tables(
        compile_project(INVOICING),
        MetaData(),
        dialect_name="mssql",
    )
    dialect = mssql.dialect()
    customer_ddl = str(
        CreateTable(tables["crm.Customer"]).compile(dialect=dialect)
    ).upper()
    invoice_ddl = str(
        CreateTable(tables["sales.Invoice"]).compile(dialect=dialect)
    ).upper()

    assert "IDENTITY" in customer_ddl
    assert "NVARCHAR(120)" in customer_ddl
    assert "ACTIVE BIT" in customer_ddl
    assert "DATETIMEOFFSET" in invoice_ddl
    assert "FOREIGN KEY(CUSTOMER_ID)" in invoice_ddl
    assert "ON DELETE NO ACTION" in invoice_ddl
    assert "ON DELETE RESTRICT" not in invoice_ddl

    email_index = next(
        index
        for index in tables["crm.Customer"].indexes
        if tuple(column.key for column in index.columns) == ("email",)
    )
    email_index_ddl = str(CreateIndex(email_index).compile(dialect=dialect)).upper()
    assert "CREATE UNIQUE INDEX" in email_index_ddl
    assert "WHERE EMAIL IS NOT NULL" in email_index_ddl
    assert "UNIQUE (EMAIL)" not in customer_ddl


def test_secured_query_compiles_to_parameterized_sql_server_sql(
    repository: SQLAlchemyRepository,
) -> None:
    statement = repository._query_statement(
        "crm.Customer",
        QuerySpec(
            filters=(FilterCondition("name", "contains", "Ltd"),),
            sort=(SortField("name"), SortField("id")),
            limit=25,
        ),
        row_criteria=(
            "active == true and length(name) > 1 "
            "and today() == today() and count(invoices) > 0",
        ),
    )
    compiled = statement.compile(dialect=mssql.dialect())
    sql = str(compiled).upper()

    assert "SELECT TOP" in sql
    assert "LEN(" in sql
    assert "CAST(GETDATE() AS DATE)" in sql
    assert "ACTIVE = 1" in sql
    assert "IS 1" not in sql
    assert "COUNT(" in sql
    assert "ORDER BY" in sql
    assert "LTD" not in sql
    assert "Ltd" in compiled.params.values()


def test_boolean_relationship_aggregates_avoid_invalid_sql_server_is_boolean(
    repository: SQLAlchemyRepository,
) -> None:
    statement = repository._query_statement(
        "sales.Invoice",
        QuerySpec(sort=(SortField("id"),)),
        row_criteria=(
            "any(lines.product.active) and all(lines.product.active)",
        ),
    )
    sql = str(statement.compile(dialect=mssql.dialect())).upper()

    assert "EXISTS" in sql
    assert "COALESCE" in sql
    assert "= 1" in sql
    assert "!= 1" in sql
    assert "IS 1" not in sql


def test_keyset_boundary_compiles_to_sql_server_top_and_bound_predicates(
    repository: SQLAlchemyRepository,
) -> None:
    statement = repository._query_statement(
        "crm.Customer",
        QuerySpec(
            sort=(SortField("name"), SortField("id")),
            limit=26,
            after=("ACME Ltd", 1),
        ),
        row_criteria=("active == true",),
    )
    compiled = statement.compile(dialect=mssql.dialect())
    sql = str(compiled).upper()

    assert "SELECT TOP" in sql
    assert "CASE WHEN" in sql
    assert " OR " in sql
    assert "CRM_CUSTOMER.NAME >" in sql
    assert "CRM_CUSTOMER.ID >" in sql
    assert "ACME LTD" not in sql
    assert "ACME Ltd" in compiled.params.values()


def test_missing_pyodbc_reports_the_installable_sql_server_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_driver(*_args: object, **_kwargs: object) -> object:
        raise ModuleNotFoundError("No module named 'pyodbc'", name="pyodbc")

    monkeypatch.setattr(sqlalchemy_adapter, "create_engine", missing_driver)

    with pytest.raises(
        DatabaseDriverError,
        match=r"tide-framework\[sqlserver\]",
    ):
        sqlalchemy_adapter._create_engine("mssql+pyodbc://localhost/tide")
