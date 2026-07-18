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
`seed` extra. Studio uses `uv run --extra studio`, which includes Textual's
syntax support and YAML parser. Therefore a previously created `.venv` that
lacks `pyodbc` or Studio syntax packages is repaired by its relevant launch
mode instead of failing with the optional-dependency message. The Microsoft
ODBC driver remains a system installation and cannot be supplied by Python
packaging.

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

To verify SQL Server without opening the application or changing the database:

```powershell
.\start.bat check
```

This checks connectivity, the managed application and TIDE-owned runtime
tables, and SQL-translatable policies. A successful result names only the
dialect, database mode, and framework-state mode; the configured URL remains
secret. Run this after driver/server changes or before troubleshooting the TUI.

To use the isolated in-memory sample instead of SQL Server:

```powershell
.\start.bat demo
```

To open the read-only auditor workspace and inspect permitted invoice action
history, use `auditor` for SQL Server or `auditor-demo` for isolated demo data:

```powershell
.\start.bat auditor
.\start.bat auditor-demo
```

Select an invoice and choose **History** or press `H`. Persistent history is
created when an audited action such as Post runs; a fresh demo process starts
with no prior events.

To browse the application definition instead of running the business
application:

```powershell
.\start.bat studio
```

This opens TIDE Studio. Its left tree contains the application manifest,
entities, views, report, and YAML source files; selecting one shows its nested
properties and complete YAML source. Select an **Editable** scalar row, enter a
value and press Enter or **Apply in memory**. The candidate is immediately
recompiled. Use **Changes** for the exact diff, **Diagnostics** for validation
messages, and Undo/Redo or `Ctrl+Z`/`Ctrl+Y` for history. Schema-defined choices
such as field type and Boolean values appear as selectors. YAML source uses
syntax colors. `Ctrl+F` searches the current YAML/diff/diagnostic view; Enter or
Next and Previous move through highlighted matches. `Ctrl+D` opens Changes and
`Q` exits. `R` reloads only when there are no pending edits.

Select a document under **Views** to open the first structural view designer.
Its table shows the compiler-resolved table columns or the form/inline left and
right field tracks, including each field's layered metadata origin. Select a
local field and use **Move up** or **Move down** to reorder it within that
track. The candidate recompiles immediately and the structural summary and
exact Changes diff refresh together. Undo, redo and **Save candidate** work the
same way as for property edits; inherited or generated placements are visible
but read-only.

When a same-group field exists at the corresponding position in the opposite
column, **← Swap** or **Swap →** exchanges the two placements. Select an unused
entity field from the chooser and choose **Add field** to add it to the local
view; the selected layout field determines the form/inline group. **Remove
field** removes only that view placement, not the entity field or SQL column.
For inline editors, add/remove updates the table column and editor layout as one
undoable operation. Unmatched cells and cross-group moves remain disabled.

For a form or inline view, select the destination group before choosing **Add
field**. Choose **Groups…** to create or rename a local group, move it past an
adjacent field group, or remove it once empty. A group cannot cross a collection
section, and a non-empty group cannot be removed. The dialog closes after each
operation so the resolved structure and exact diff refresh immediately.

For a form view, choose **Layout…** to edit the complete shared presentation
track. You can assign or clear tab labels, move whole group/collection sections,
add an unused collection with a compatible inline editor, remove only its view
placement, and order the record or collection action buttons. The dialog closes
after one operation so Studio can recompile and show the exact diff. Save the
candidate, close Studio, and run `start.bat demo` to test the resulting tabs and
button order against isolated demo data.

Choose **Preview…** on any selected view to inspect it without saving or
starting the application. Select an application role and compact 80×24,
standard 100×30, or wide 140×40 terminal size. The canvas marks fields as
editable, record-dependent, read-only, protected, or hidden; shows action and
entity access; and warns when declared/estimated layout constraints do not fit.
This preview uses only compiled metadata and the shared security engine: it
does not connect to SQL Server, load records, or execute application handlers.

For a source-level change, select a document and choose **Edit YAML**. The lower
panel becomes writable while the tree and property editor are locked to that
document. Choose **Apply YAML** or press `Ctrl+S` to parse and compile the whole
buffer into the in-memory candidate; the exact Changes view opens on success.
Press `Esc` or **Cancel edit** to discard the raw buffer. Malformed YAML remains
open for correction, while changing an entity/view/report identity is refused
because renames require an atomic cross-document command. `Ctrl+F` also works
inside the expert editor.

To persist a valid candidate, choose **Save candidate** or press `Ctrl+S` while
the expert editor is closed. Review the canonical project path, changed YAML
files, receipt path and exact diff. Type the complete displayed `SAVE
tide-designer-approval-...` phrase; the confirmation button remains disabled
for partial or altered text. The existing transactional save service then
rereads and locks the live application, stages and recompiles the candidate,
replaces only the approved YAML files, writes its receipt, and reloads Studio
at a clean baseline. Unsaved edits are still discarded when Studio closes.

If the live files changed after Studio opened, save is refused without
overwriting them. If an active or interrupted Designer lock exists, the review
shows read-only recovery status and the exact `uv run tide designer recover
"..." --preview` command to run after closing Studio. Recovery remains a
separate explicit approval operation documented below.

Studio does not use `TIDE_DATABASE_URL`, connect to SQL Server, or execute
application Python. **Apply YAML** means apply to the process-local candidate,
not save to disk; only the separately reviewed **Save candidate** action has
the narrow transactional YAML-write authority.

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

If Windows, the terminal, or TIDE closes during the small multi-file replacement
window, inspect the retained transaction without changing it:

```powershell
uv run tide designer recover applications/invoicing --preview
uv run tide designer recover applications/invoicing --preview --json
```

If preview reports a safe rollback or finalize action, run:

```powershell
uv run tide designer recover applications/invoicing
```

Type the complete `RECOVER tide-designer-recovery-...` challenge. Recovery will
not run while the original save still owns its OS lock. It restores only hash-
verified originals, or cleans up an already successful save only when its
receipt and candidate match. If it reports ambiguous or malformed evidence,
leave the lock/stage in place and inspect the files or source control rather
than deleting them manually.

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
