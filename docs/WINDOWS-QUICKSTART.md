# Windows Quick Start

The repository's `start.bat` launches the invoicing application from any
working directory. Its default mode connects to the local SQL Server database
named `TIDE` on `localhost:1433` using Windows integrated security.

## One-time prerequisites

1. Ensure SQL Server is listening on `localhost:1433` and the empty `TIDE`
   database exists.
2. Install Microsoft ODBC Driver 17 for SQL Server, or edit `start.bat` if this
   host uses another installed driver.
3. Install the project and SQL Server Python dependencies from a terminal in
   the repository:

   ```powershell
   uv sync --extra dev --extra sqlserver
   ```

The batch file sets `TIDE_DATABASE_URL` only for itself and the TIDE process it
starts. It does not modify the Windows user or system environment, and the value
disappears when the shortcut exits. Integrated security means the included URL
does not contain a username or password.

## First managed start

Open PowerShell or Command Prompt in the repository and run:

```powershell
.\start.bat init
```

This explicitly creates any missing invoicing and TIDE-owned runtime tables,
then opens the application. Use `init` only for a managed TIDE database where
the current Windows account is allowed to create tables. It does not add demo
records.

## Normal starts

After initialization, double-click `start.bat` or run:

```powershell
.\start.bat
```

This validates the existing schema and opens the application without requesting
schema creation.

To use the isolated in-memory sample instead of SQL Server:

```powershell
.\start.bat demo
```

To fill an empty initialized SQL Server database with deterministic development
data, run this once after `start.bat init` has created the tables:

```powershell
.\start.bat seed
```

The seeder creates 25 Customers, 20 Products, and 100 Invoices through the real
application services, and posts a deterministic subset through the normal Post
action. It refuses to run when any application entity already contains records.
Change the counts or `--random-seed` on the `:seed` command line in `start.bat`
when a different repeatable dataset is useful.

Available commands can be displayed with `start.bat help`.

## Local API

Start the API with isolated demo data:

```powershell
.\start.bat api-demo
```

Or use the configured SQL Server database:

```powershell
.\start.bat api
```

The shortcut creates an ephemeral development bearer token, prints it in the
console, and starts the server on `http://127.0.0.1:8000`. Open
`http://127.0.0.1:8000/docs`, choose **Authorize**, and paste the printed token.
The token is not a database credential: only the server process receives
`TIDE_DATABASE_URL`, while HTTP clients receive secured JSON projections.

The interactive contract includes create and partial-update operations for
Invoices, Customers, and Products plus the Invoice Post action. For versioned
Invoices, first execute `GET /api/v1/invoices/{id}` and copy its response
`ETag` into the mutation's `If-Match` header. Posting additionally requires a
new caller-generated `Idempotency-Key` such as a UUID. Demo-mode mutations are
discarded when the server stops; SQL Server-mode mutations are persistent and
pass through the same validation, authorization, concurrency, and action audit
services as the TUI.

This first identity adapter is deliberately restricted to the local computer.
Do not change the binding to a network address or expose it through a firewall;
production network access requires the planned OAuth/OIDC and HTTPS adapter.

## Previewing and exporting an invoice

In the Invoice workspace, highlight a saved invoice and click **Preview** or
press `V`. The report preview offers **Export HTML** and **Export PDF**. When
started through `start.bat`, exported files are placed in the repository's
`output\reports` directory. The documented `uv sync --extra dev --extra
sqlserver` installation includes ReportLab; a minimal production installation
can add `--extra report` when PDF output is required.

## Changing the connection

Edit this quoted line near the top of `start.bat`:

```bat
set "TIDE_DATABASE_URL=mssql+pyodbc://@localhost:1433/TIDE?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes&Encrypt=no"
```

Keep the entire `set "NAME=value"` assignment quoted. The URL contains `&`,
which Command Prompt would otherwise interpret as a command separator.
`Encrypt=no` is suitable only for the current local development instance; use
encryption and normal certificate validation for networked deployments.

If startup fails, the batch window remains open so the error can be read. Common
causes are a stopped SQL Server service, TCP port 1433 being disabled, a missing
ODBC driver or `pyodbc`, insufficient database permissions, or running normal
mode before the initial `start.bat init`.
