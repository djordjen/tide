# Windows Quick Start

The repository's `start.bat` launches the invoicing application from any
working directory. Its default mode connects to the local SQL Server database
named `TIDE` on `localhost:1433` using Windows integrated security.

## One-time prerequisites

1. Ensure SQL Server is listening on `localhost:1433` and the empty `TIDE`
   database exists.
2. Install Microsoft ODBC Driver 17 for SQL Server, or edit `start.bat` if this
   host uses another installed driver.
3. Install `uv`. The batch file requests the exact Python extras required by
   each mode, so the first double-click automatically prepares `textual` and
   `pyodbc`. To download all development and SQL Server dependencies in
   advance, run:

   ```powershell
   uv sync --extra dev --extra sqlserver
   ```

The batch file sets `TIDE_DATABASE_URL` only for itself and the TIDE process it
starts. It does not modify the Windows user or system environment, and the value
disappears when the shortcut exits. Integrated security means the included URL
does not contain a username or password.

The default and `init` modes use
`uv run --extra tui --extra sqlserver`; `seed` additionally requests the
`seed` extra. Therefore a previously created `.venv` that lacks `pyodbc` is
repaired on launch instead of failing with the optional-dependency message.
The Microsoft ODBC driver remains a system installation and cannot be supplied
by Python packaging.

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

## Local runtime MCP

Start the same demo server with its metadata-opted, read-only MCP surface:

```powershell
.\start.bat mcp-demo
```

The console prints an ephemeral bearer token. Configure an MCP Inspector or
other Streamable HTTP client with URL `http://127.0.0.1:8000/mcp` and paste the
token into its bearer-token setting. The client can discover schema resources,
record resource templates, and these structured query tools:

```text
search_catalog_product
search_crm_customer
search_sales_invoice
```

For example, call `search_catalog_product` with `limit: 2`, or filter unit
prices with `{"field":"unit_price","operator":"gte","value":"200.00"}`.
The MCP client receives no SQL Server URL. Every resource/tool call uses the
same server-side row, field, relationship, and query authorization as REST.
This milestone does not expose create, update, Post Invoice, reports, or
developer/project-editing tools through MCP.

## Local developer MCP

The developer MCP is a separate stdio process for an MCP-capable AI development
client. Configure that client to launch this command from the repository root:

```powershell
uv run --extra mcp tide mcp dev applications/invoicing
```

Unlike `mcp-demo`, this process does not expose or change business data. It can
inspect the compiled project, validate structured proposals for a new TIDE
application, and preview them in a deleted temporary candidate tree. For
example, an AI can propose Company, Product, Invoice and InvoiceLine entities,
two roles, a constrained Post state transition and an Invoice PDF report. The
preview returns compiler/static-check results, generated browse/form/lookup/
inline views, exact artifacts and a unified diff with fingerprints. It also
runs fixed TIDE-owned sequence/transition templates through bounded in-memory
CRUD, authorization, idempotency, report, HTML and optional PDF checks. It runs
no external command, uses no configured application database, and always
confirms that no workspace write or persistent candidate occurred.
Developer MCP still has no apply tool. Save the same structured plan as JSON to
use the separate local approval boundary:

```powershell
uv run tide app preview plan.json --workspace .
uv run tide app apply plan.json --workspace .
```

The second command prints the exact diff and requires the complete displayed
`APPLY tide-approval-...` challenge. It creates only a previously absent
`applications/<application-id>` tree and records `.tide-apply.json`; it never
edits or replaces an existing application. See
[AI-assisted application generation](AI-APPLICATION-GENERATION.md).

To preview a structured edit to an existing application, save a
`DesignerCommandBatch` as `changes.json` and run:

```powershell
uv run tide designer preview applications/invoicing changes.json
uv run tide designer preview applications/invoicing changes.json --json
```

Both commands are read-only. To persist that exact candidate locally, run:

```powershell
uv run tide designer save applications/invoicing changes.json
```

The save command prints the exact YAML diff and requires the complete displayed
`SAVE tide-designer-approval-...` challenge. It refuses a stale base, changed
candidate, invalid model, new/deleted source file, Python replacement, or
another active save. Approved YAML replacements are staged, recompiled,
rollback-protected, and recorded under `.tide/designer/`. Developer MCP itself
still cannot invoke this save boundary.

To verify the new reusable remote client, leave the API window running and open
a second terminal:

```powershell
.\start.bat api-check
```

Paste the token printed by the API window when prompted. Input is hidden. The
check authenticates, verifies that the local invoicing model exactly matches
the server application and wire versions, and reports the number of available
operations/actions. It never needs `TIDE_DATABASE_URL`.

To run the Textual application as a genuine API client, leave the API window
running and execute:

```powershell
.\start.bat remote
```

Paste the same token when prompted. The remote TUI receives no SQL Server URL
or database driver access: browse, search, sorting, paging, lookups,
create/update, nested invoice lines, and posting go through FastAPI and the
server-side services. Invoice report data is likewise authorized and formatted
on the server; Preview and local HTML/PDF export work through the transported
renderer-neutral document.

The `api` and `api-demo` shortcuts deliberately use development identity and
are restricted to the local computer. Do not change their binding to a network
address or expose them through a firewall.

For a reviewed network test, first install the production identity adapter:

```powershell
uv sync --extra api --extra auth --extra mcp --extra sqlserver
```

Then supply the issuer, API audience, provider role mapping, and real PEM
certificate/key explicitly (replace every example value):

```powershell
uv run tide serve applications/invoicing --database-env `
  --auth oidc `
  --oidc-issuer https://identity.example.com/tenant `
  --oidc-audience tide-api `
  --oidc-role-map external-sales=sales_clerk `
  --host 0.0.0.0 --port 8443 `
  --ssl-certfile C:\TIDE\tls\server-chain.pem `
  --ssl-keyfile C:\TIDE\tls\server-key.pem `
  --mcp `
  --mcp-resource-url https://tide.example.com:8443/mcp
```

TIDE validates bearer tokens but does not perform the provider's interactive
login or token refresh. Set `TIDE_API_TOKEN` to an access token obtained from
that provider before running `tide api check-server` or `tide run --api-url`
against the HTTPS origin. Do not put that token or private-key password in a
batch file. A password-protected key can use
`--ssl-keyfile-password-env NAME`. Reverse-proxy trust is not implemented yet;
this command's non-loopback mode therefore requires direct TLS.

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
