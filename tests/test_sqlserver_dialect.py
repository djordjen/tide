from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import MetaData, Numeric
from sqlalchemy.dialects import mssql
from sqlalchemy.dialects.mssql import base as mssql_base
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


def test_distinct_enumeration_orders_legally_under_sql_server(
    repository: SQLAlchemyRepository,
) -> None:
    """SELECT DISTINCT may only order by items in the select list.

    The null-rank CASE that puts blanks last is not in the select list, and
    SQL Server refuses the combination outright (error 145, probed against a
    real server) -- so the whole column-value-filter capability was dead on
    one of the two supported dialects. Grouping by the enumerated column
    yields the identical value set and makes the rank a legal ORDER BY.
    """

    statement = repository._distinct_statement(
        "crm.Customer",
        "email",
        filters=(FilterCondition("active", "eq", True),),
        row_criteria=("active == true",),
        limit=200,
    )
    sql = str(statement.compile(dialect=mssql.dialect())).upper()

    assert "SELECT DISTINCT" not in sql
    assert "GROUP BY" in sql
    assert "ORDER BY CASE WHEN" in sql


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


def _reflected_money_types() -> dict[str, type]:
    """The types SQL Server reflection actually produces for money columns.

    Read from the dialect's own registry rather than named here, so a rename
    inside SQLAlchemy fails the guard below instead of quietly testing nothing.
    """

    found = {
        name: mssql_base.ischema_names[name]
        for name in ("money", "smallmoney")
        if name in mssql_base.ischema_names
    }
    assert set(found) == {"money", "smallmoney"}, (
        "SQL Server reflection no longer maps both money spellings; "
        f"found {sorted(found)}"
    )
    return found


def test_a_decimal_field_accepts_a_sql_server_money_column() -> None:
    """money is how SQL Server spells a fixed-scale decimal, not a foreign type.

    `MONEY` does not subclass `Numeric` -- it descends straight from
    `TypeEngine` with its own affinity -- so nothing about the ordinary numeric
    comparison recognises it without being told.
    """

    for name, money_type in _reflected_money_types().items():
        assert sqlalchemy_adapter._types_compatible(
            Numeric(precision=12, scale=2), money_type(), "mssql"
        ), f"a decimal field should map onto a {name} column"


@pytest.mark.parametrize(
    ("column", "precision", "scale", "expected"),
    [
        ("money", 19, 4, None),
        ("money", 12, 2, None),
        ("money", 19, 6, "scale 4 is smaller than required scale 6"),
        ("money", 24, 4, "precision 19 is smaller than required precision 24"),
        ("smallmoney", 10, 4, None),
        ("smallmoney", 12, 2, "precision 10 is smaller than required precision 12"),
    ],
)
def test_a_decimal_wider_than_its_money_column_is_still_rejected(
    column: str, precision: int, scale: int, expected: str | None
) -> None:
    """Accepting the type must not mean accepting any capacity.

    A money column is fixed at 19,4 (10,4 for smallmoney) and the reflected
    type carries neither number, so the capacity check is blind to it until the
    capacities are supplied.
    """

    issue = sqlalchemy_adapter._type_capacity_issue(
        Numeric(precision=precision, scale=scale),
        _reflected_money_types()[column](),
    )

    if expected is None:
        assert issue is None, f"decimal({precision},{scale}) fits {column}: {issue}"
    else:
        assert issue is not None, f"decimal({precision},{scale}) does not fit {column}"
        assert expected in issue


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
