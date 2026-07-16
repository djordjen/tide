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
