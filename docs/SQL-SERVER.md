# Microsoft SQL Server

**Status: Accepted as TIDE's first multi-user database target. Dialect
compilation and a live local SQL Server integration pass through Microsoft ODBC
Driver 17 are covered; automated multi-version certification is in progress.**

TIDE uses synchronous SQLAlchemy Core rather than the ORM. The repository
constructs typed SQLAlchemy statements and the `mssql` dialect renders bound
SQL Server commands. Application code and metadata never concatenate SQL.

## Installation

Install the optional driver dependency and Microsoft ODBC Driver 17 or later
for SQL Server. Driver 18 is recommended for new deployments:

```bash
uv sync --extra dev --extra sqlserver
```

The Python extra installs the version-locked `pyodbc` package. The Microsoft
ODBC driver is an operating-system component and must be installed separately.
`LongAsMax=Yes` in the example below requires Driver 18; omit it when using
Driver 17.
If `pyodbc` is missing, repository construction raises a stable
`database_driver_unavailable` error with the required extra name.

## Connection

Prefer a SQLAlchemy `URL` object so passwords do not require manual URL
escaping:

```python
from sqlalchemy import URL

from tide.data import SQLAlchemyRepository

url = URL.create(
    "mssql+pyodbc",
    username="tide_app",
    password="secret-from-a-secret-store",
    host="sql.example.net",
    port=1433,
    database="tide_invoicing",
    query={
        "driver": "ODBC Driver 18 for SQL Server",
        "Encrypt": "yes",
        "TrustServerCertificate": "no",
        "LongAsMax": "Yes",
    },
)

repository = SQLAlchemyRepository(model, url)
```

Credentials and connection URLs are deployment configuration. They do not
belong in application metadata or source control. Passing a preconfigured
SQLAlchemy `Engine` remains supported when a deployment needs custom pooling,
authentication, or `fast_executemany` settings. String/URL construction enables
connection pre-ping for SQL Server.

## Run the TUI against SQL Server

`tide run` reads its deployment URL from an environment variable. For the local
`TIDE` database on port 1433 with Windows integrated security and ODBC Driver
17, a development session can use:

```powershell
$env:TIDE_DATABASE_URL = "mssql+pyodbc://@localhost:1433/TIDE?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes&Encrypt=no"
uv run tide run applications/invoicing --database-env --create-schema --role sales_clerk
```

`--database-env` without a name reads `TIDE_DATABASE_URL`. On this first
managed run, `--create-schema` explicitly creates missing application tables
and TIDE-owned cursor, idempotency, and audit tables. It does not seed demo
records. Later launches omit schema creation:

```powershell
uv run tide run applications/invoicing --database-env --role sales_clerk
```

For a read-only operational acceptance check against an already initialized
database, run:

```powershell
uv run --extra sqlserver tide db check applications/invoicing --database-env
```

The check compiles the application, opens the configured database, validates
the mapped application schema, verifies managed cursor/idempotency/audit state,
and preflights every SQL-translated row policy. It performs no DDL and no data
mutation, and reports the dialect/mode without printing the connection URL.
On the repository's Windows shortcut the equivalent command is
`start.bat check`.

The environment value is never printed by TIDE. Use encrypted connections and
normal certificate validation for networked deployments; `Encrypt=no` above is
only for the previously certified local development instance. Legacy-mode
applications reject `--create-schema`, validate their declared mappings, and
keep framework cursor/action state in-process so the external schema remains
unchanged.

## Current portability contract

The normal test suite compiles managed schema and secured queries with the
SQLAlchemy MSSQL dialect. It covers:

- integer identity primary keys;
- Unicode `NVARCHAR`, `BIT`, `DATE`, `DATETIMEOFFSET`, and exact `NUMERIC`
  mappings;
- filtered unique indexes for optional fields, preserving TIDE's
  multiple-null uniqueness contract;
- foreign keys and physical schema/table/column mappings;
- portable delete actions (`restrict` renders as SQL Server `NO ACTION`);
- parameterized filters, deterministic null ordering, SQL Server `TOP` limits,
  and bound lexicographic keyset boundaries;
- direct, reference-path, and single-collection aggregate row policies;
- bounded collection hydration with target-row predicates and no requirement
  for MARS;
- shared continuation state with hashed bearer tokens, exact decimal round
  trips, expiry, and bounded capacity;
- SQL Server-specific `LEN` and date-only `today()` rendering;
- optimistic update predicates without invalid `IS 1` boolean syntax.

Multiple collection traversals still fail query preflight closed. Composite
keys, trigger-refreshed values, unusual vendor types, writable views, stored
procedures, and temporal-table semantics need explicit contracts before they
can be claimed as supported.

## Live integration suite

Live tests are deliberately opt-in because they create and remove the mapped
tables. Use a dedicated empty scratch database whose credentials may create
and drop tables:

```powershell
$env:TIDE_TEST_SQLSERVER_URL = "mssql+pyodbc://..."
uv run pytest -m sqlserver tests/test_sqlserver_integration.py
```

For a Windows-integrated localhost scratch instance that does not support
encrypted connections, the test URL may include `trusted_connection=yes` and
`Encrypt=no`. Keep encryption enabled for networked and production database
connections.

The fixture refuses to run if any mapped TIDE table already exists. It tests
schema creation/reflection, identity retrieval, Unicode and decimal round
trips, relationship aggregate and hydration policy SQL, keyset page boundaries,
optimistic concurrency, durable action idempotency/audit, and shared cursor
round trips, then removes only the tables it created.

For legacy mode, `create_schema()` remains forbidden regardless of dialect.
Compatibility inspection and normal bound data operations may run against an
existing SQL Server schema without changing it.
