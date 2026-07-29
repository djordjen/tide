# Getting Started with TIDE Framework

This guide takes a new contributor from a clean checkout to the runnable
Invoicing application, TIDE Studio, and the generated REST and MCP interfaces.
The first run uses isolated in-memory demo data, so it does not require or
change a database.

![TIDE invoice browser](images/tide-invoice-browser.svg)

## What you will run

TIDE Framework compiles application YAML into one validated application model.
The Textual TUI, Studio, REST/OpenAPI, runtime MCP, reports, and future renderers
all consume that model and the same application services. The maintained
[Invoicing application](../applications/invoicing/README.md) is the golden
reference and demonstrates Customers, Products, Invoices, line items,
permissions, posting, reports, auditing, and optimistic concurrency.

## Prerequisites

- Python 3.11, the current development and CI-certified baseline; project
  metadata permits newer interpreters on a best-effort basis;
- [uv](https://docs.astral.sh/uv/) for dependency and environment management;
- Git;
- a terminal with at least 80 columns for the TUI;
- optional: Microsoft SQL Server and ODBC Driver 17 or newer for persistent
  Windows testing;
- optional: Node.js 22.7.5 or newer for the browser-based MCP Inspector.

## Five-minute demo

Clone the repository and install the complete development environment:

```powershell
git clone https://github.com/djordjen/tide.git
cd tide
uv sync --extra dev
```

Validate the reference application:

```powershell
uv run tide model validate applications/invoicing
```

Expected output:

```text
Model is valid: TIDE Invoicing 0.1.0 (4 entities, 9 views, 2 reports, 0 warning(s)).
```

On Windows, launch the isolated demo with:

```powershell
.\start.bat demo
```

The equivalent cross-platform command is:

```bash
uv run --extra tui tide run applications/invoicing --demo
```

The `--demo` switch loads application-owned sample records into memory. Closing
the process discards every change. The browse initially fills the terminal
viewport and automatically appends another secured cursor batch when scrolling
near the end; **Previous** and **Next** are not required.

For a screenshot-led tour that connects each screen to its application-owned
metadata, follow the [Invoicing Application Walkthrough](INVOICING-WALKTHROUGH.md).

## Tour the Invoicing application

Use the workspace selector to move between **Invoices**, **Customers**, and
**Products**. A useful first workflow is:

1. Create a Customer or Product and save it.
2. Open Invoices and choose **New**.
3. Pick a customer, add a line, and open the searchable Product lookup with
   `F4`, Space, or Down.
4. Save the invoice, preview its report, and post it.
5. Start the auditor demo and inspect its action and CRUD history.

Important shortcuts include:

| Shortcut | Action |
| --- | --- |
| `Tab` or `Enter` | Move through editable fields |
| `Ctrl+S` | Save the current record |
| `Ctrl+N` | Add a line or create a lookup record |
| `Ctrl+P` | Post an eligible invoice |
| `V` | Preview the selected invoice report |
| `S` | Preview the posted-sales summary |
| `H` | Show authorized audit history |
| `Esc` | Cancel or close the current screen |

For read-only audit/report behavior, use `start.bat auditor-demo` on Windows or:

```bash
uv run --extra tui tide run applications/invoicing --demo --role auditor
```

## Inspect the application model

The compiler can explain the resolved origin and metadata of an application
member:

```powershell
uv run tide model explain sales.Invoice.total --project applications/invoicing
uv run tide view explain sales.Invoice.edit --project applications/invoicing
```

Applications live below `applications/<name>/`, separate from the framework
runtime. An application normally owns:

```text
applications/<name>/
  tide.yaml
  models/
  views/
  presentation/
  reports/
  security/
  runtime.py       # optional application behavior registration
```

YAML remains the authoring format. It is compiled into an immutable normalized
model before a renderer or data adapter can use it. See the
[application model](APPLICATION-MODEL.md) and
[metadata v0.1 reference](METADATA-V0.md) for the accepted contract.

## Open TIDE Studio

Studio provides a structured application tree, schema-aware property editors,
view-layout tools, searchable syntax-colored YAML, validation, exact diffs,
undo/redo, and an approval-bound save workflow.

```powershell
.\start.bat studio
```

Or, cross-platform:

```bash
uv run --extra studio tide studio applications/invoicing
```

Studio first changes an in-memory candidate. **Save candidate** shows the exact
files and diff, then requires the displayed approval phrase before using the
transactional YAML save service. Closing an unsaved session changes no source
files. See [Designers and reporting](DESIGNERS-AND-REPORTING.md) for the safety
and recovery contracts.

## Run REST and OpenAPI locally

On Windows:

```powershell
.\start.bat api-demo
```

The shortcut prints a fresh development bearer token and starts a loopback-only
FastAPI server. Open <http://127.0.0.1:8000/docs>, choose **Authorize**, and
paste that token to exercise the generated contract.

After authorization, try `GET /api/v1/_tide/presentation`. It returns the safe,
principal-specific application navigation and browse contract intended for
remote renderers: columns, search, named filters, sorting, server fetch size,
and REST paths. It deliberately excludes raw YAML, permission rules, Python
handlers, and the database connection.

To see that contract rendered as the first responsive Web application slice:

```powershell
.\start.bat web-demo
```

Paste the printed development token into the connection screen opened by the
browser. Use `start.bat web` for the configured local SQL Server instead. The
generic Web shell provides capability-filtered navigation, server-mode browse,
search, filters, sorting, seamless cursor loading, and personal column
layouts. Select a row and choose **Open**, press Enter, or double-click it to
load the shared-YAML detail layout and inline collections. Previous/Next and
Page Up/Page Down move through the current secured list without replacing the
form.

Open **Products** or **Customers** and choose **New** to exercise the first Web
editing slice. Defaults, required fields, choices, masks, and numeric
constraints come from the compiled application model. Save a record, reopen
it, change one field, and save again; Web sends the create/update through
FastAPI and places authoritative validation messages beside their fields. If
the entity defines a concurrency token, the update also returns the record's
ETag.

Open a Draft **Invoice** and choose **Select…** beside Customer. The
multi-column window searches the YAML-declared Code, Name, and Email fields.
Select an existing Customer, or choose **New Customer**, complete the nested
form, and use **Save & Select**. The Invoice draft and its visible line table
remain open; Customer selection/defaults pass through FastAPI rather than
being interpreted in the browser. Line editing and Product lookup remain the
next Web master-detail slice. See [Web UI](WEB-UI.md) for its architecture,
security boundary, production build, tests, and current limitations.

The unauthenticated `GET /health/live` endpoint is process-only. The
`GET /health/ready` endpoint checks runtime persistence dependencies and returns
HTTP 503 with a safe `not_ready` body when the server should receive no traffic.
The server prints secret-safe JSON request events. Send `X-Correlation-ID` when
you want to trace one REST or hosted MCP request into its service-layer audit;
TIDE returns the accepted identifier, or a generated UUID when it was omitted
or malformed. Use `tide serve --log-level warning ...` to reduce routine output.
REST and hosted MCP requests also share a 1 MiB body limit, 30-second body
receive deadline, 100-request concurrency cap, five-second idle keep-alive, and
30-second graceful-shutdown window. For an explicitly reviewed deployment,
override them with, for example:

```powershell
uv run tide serve applications/invoicing --database-env `
  --max-request-body-bytes 2097152 --request-body-timeout 30 `
  --max-concurrent-requests 50 `
  --keep-alive-timeout 5 --graceful-shutdown-timeout 30
```

These are HTTP-host controls; they do not replace field/page limits or impose a
hard database-operation timeout.

In a second terminal, verify the server or open the TUI as a remote API client:

```powershell
.\start.bat api-check
.\start.bat remote
```

Both commands securely prompt for the printed token. The remote TUI receives
no database URL: browse, lookup, mutation, report, concurrency, and action calls
all pass through FastAPI and the server-side services.

The first native Qt proof uses that same server. With `api-demo` still running,
open another terminal and run:

```powershell
.\start.bat gui
```

Paste the same token to open the metadata-driven application shell. Its
**Sales** and **Master Data** groups expose the capability-allowed Invoice,
Customer, and Product workspaces declared in `presentation/defaults.yaml`.
Switching workspaces keeps each visited list's query, loaded records, selection,
and personal columns intact. Invoice is selected initially. Select a row
and use **Open**, double-click, or press **Enter**. The same YAML-defined
two-column form opens for editable and read-only records; field, Save, and Post
states follow permissions and Invoice status. Reaching the bottom automatically
appends the next secured server batch; there are no page-navigation buttons.
Inside an existing-record form, **Previous** and **Next** at bottom left move
through that current searched/filtered/sorted list; **Page Up** and **Page
Down** invoke the same actions. Next also crosses a cursor batch boundary
automatically. The dialog remains in place while its data and workflow state
update; save or cancel a changed draft before navigating.

For an editable draft Invoice, the Customer editor opens the compiled
`crm.Customer.lookup` as a searchable
multi-column dialog. Search matches code, name, or email through FastAPI; select
a result, or use **New** / Ctrl+N when authorized. The nested Customer form ends
with **Save & Select**, returning the new identity to the preserved Invoice
draft.

The Lines section follows `sales.InvoiceLine.inline_edit`: its table fills the
available height, the metadata-ordered Line Details fields sit below it, and
**Add line**, **Apply line**, and **Remove line** stay at bottom left. Product
uses its own multi-column lookup and may create a missing Product through
**Save & Select**. Selection asks FastAPI to copy Description and Unit Price;
enter Quantity and use **Apply line** to preview line Total and Invoice Total.
The final **Save** sends one sanitized nested Invoice payload with the observed
ETag.

To see the concurrency safeguard, open the same Product or draft Invoice in two
GUI instances, edit it in both, and save the first. Saving the second opens a
three-way **Original / Current / Your draft** review. Choose Current or Mine for
each genuine overlap, or reload the server version. **Apply Resolution** opens
a fresh form on the latest ETag so you can inspect the result before pressing
Save; the review dialog never writes or silently overwrites the other edit.

For the action path, edit a draft Invoice that has at least one valid line.
**Post invoice** is disabled while the line collection is empty and after the
Invoice is posted. With a valid draft, click it instead of Save. Qt first saves
any changed header/lines, then calls the secured Post action with the returned
ETag and an idempotency key. The dialog closes and the refreshed browse row
shows Posted. If posting fails after the draft save, the saved form reopens
with the server message so it can be corrected safely.

Inside an opened Invoice, choose **Preview PDF** to build its authorized report
through FastAPI. Qt writes the returned renderer-neutral document to a unique
file in the operating system's temporary directory and opens the system PDF
viewer. The temporary session directory is cleaned up later instead of filling
`output\reports`. Roles without the report capability do not see the button.

From the Invoice list, choose **Posted Sales Summary**. Qt asks FastAPI to build
the existing parameterless `sales.summary` report over authorized posted
invoices, then opens the returned renderer-neutral document in a native table.
Use **Export CSV**, **Export HTML**, or **Export PDF** as needed. This report
uses its reviewed metadata query and is independent of the current list search,
filter, and loaded cursor batches. Roles without `sales.summary` capability do
not see the action.

The Product and Customer forms are available from **Master Data** in the same
window. These shortcuts remain convenient direct startup links:

```powershell
.\start.bat gui-products
.\start.bat gui-customers
```

Use **New** or select a row and use **Open**. These dialogs follow compiled form
rows, writable-field capabilities, required fields, Boolean controls, regex
masks, and exact Decimal input. Save calls FastAPI in the background and
refreshes the secured list; the GUI never receives a database URL. See the
[Qt GUI prototype](QT-GUI.md) for the exact current scope.

To inspect the generated OpenAPI document without starting a server:

```powershell
uv run tide api export-openapi applications/invoicing
```

See [REST API and MCP](API-AND-MCP.md) for filtering, ETags, idempotency,
production identity, and deployment requirements.

For a complete executable client example, follow
[Call a TIDE Application Through REST](API-CLIENT-TUTORIAL.md). With
`api-demo` still running, the second terminal command is:

```powershell
uv run --extra client python examples/invoicing_api_client.py
```

The client securely prompts for the printed token, then demonstrates Product
defaults, Invoice create/update, validation and stale-ETag failures, idempotent
Post, correlated audit history, and the secured report contract. It has no
database connection string.

## Test runtime MCP locally

Runtime MCP gives an authenticated AI client explicitly exposed application
resources and tools. It never receives a repository, arbitrary SQL capability,
database credentials, or project source-write authority.

```powershell
.\start.bat mcp-demo
```

The shortcut prints a bearer token and hosts Streamable HTTP at
`http://127.0.0.1:8000/mcp`. For a local browser-based inspection UI, run:

```powershell
npx -y @modelcontextprotocol/inspector@latest
```

In MCP Inspector select **Streamable HTTP**, enter the URL above, and paste the
printed token into its bearer-token setting. Start with a read operation such
as `search_catalog_product`, then exercise the generated create/update tools or
the idempotent `post_sales_invoice` action. The demo process discards all
changes when it stops.

ChatGPT web requires remotely supplied MCP tools and cannot launch this local
HTTP process directly. Local ChatGPT desktop/Codex clients can configure MCP on
their Codex host; keep that developer workflow separate from exposing a runtime
data server over the internet.

Developer MCP is a different, local stdio surface for inspecting and proposing
TIDE application definitions:

```powershell
uv run --extra mcp tide mcp dev applications/invoicing
```

It can produce deterministic proposals and validated candidate artifacts, but
cannot apply them or write arbitrary workspace files. See
[Generate a TIDE Application with AI and Developer MCP](AI-GENERATION-TUTORIAL.md)
for the complete ChatGPT desktop/Codex setup, example prompt, checked-in plan,
expected output, and explicit local approval walkthrough. The architectural and
security contract remains in
[AI-assisted application generation](AI-APPLICATION-GENERATION.md).

## Use the local SQL Server database on Windows

This step is optional. The repository shortcut targets the local `TIDE`
database on port `1433` with Windows integrated security. Review `start.bat`
before changing that development connection.

Initialize TIDE-owned managed tables once:

```powershell
.\start.bat init
```

Seed an empty initialized database, perform a read-only operational check, and
then run normally:

```powershell
.\start.bat seed
.\start.bat check
.\start.bat diff
.\start.bat
```

`diff` is inspection-only: it prints the deterministic managed migration
proposal and never applies DDL. A clean initialized database reports no
differences. A changed database/model can be passed to the separate
fingerprint- and approval-bound `tide db revision` command to create review
files inside the application. `tide db render-sql` with `--extra migration`
then verifies those files and creates dialect-specific SQL without a database
connection; TIDE still cannot apply it. See [Schema migrations](MIGRATIONS.md).

Use `start.bat auditor` for the persisted read-only audit/report workspace.
For an externally owned schema that TIDE must not change, follow the separate
[legacy database no-DDL contract](LEGACY-DATABASES.md). Complete driver,
connection, and troubleshooting guidance is in
[Microsoft SQL Server](SQL-SERVER.md).

For a path-based SQLite deployment, create a non-overwriting online backup and
verify its adjacent SHA-256 manifest with:

```powershell
uv run tide db backup applications/invoicing --database-env `
  --output backups/invoicing.db
uv run tide db verify-backup applications/invoicing backups/invoicing.db
```

SQL Server continues to use native DBA-managed backup and a real isolated
restore drill; TIDE validates the restored application and framework schema
with `tide db check`. Follow the full
[backup and recovery runbook](OPERATIONS.md#database-changes-and-recovery)
before a production release or migration.

## Create another application

`applications/invoicing` is a reference application, not a hard-coded part of
the runtime. Additional applications belong in independent
`applications/<name>/` directories and may define different models, views,
reports, security, mappings, and optional handlers.

There is not yet a general `tide new` wizard. Today, developers can either:

- create the manifest and YAML files directly using the metadata references;
- follow [Build Your First TIDE Application](FIRST-APPLICATION.md), whose small
  Contacts example is compiler-validated in CI;
- use the Invoicing structure as a reviewed example; or
- use developer MCP to prepare a structured proposal, then review and apply it
  through the separate approval-required local command.

Always validate a new application before running it:

```powershell
uv run tide model validate applications/<name>
```

## Run the project checks

```powershell
uv run ruff check .
uv run pytest
```

The complete suite includes compiler, security, services, repositories,
SQL-policy compilation, TUI, Studio, REST/OpenAPI, MCP, report, generation, and
local documentation-link contract tests. Live SQL Server tests remain
explicitly opt-in.

## Where to go next

- [Windows quick start](WINDOWS-QUICKSTART.md) — every `start.bat` mode and
  Windows troubleshooting.
- [Web UI](WEB-UI.md) — run and build the generic React renderer.
- [Architecture](ARCHITECTURE.md) — service, model, repository, and adapter
  boundaries.
- [Security](SECURITY.md) — permissions, protected values, authentication, and
  fail-closed behavior.
- [Compilation and application layout](COMPILATION-AND-LAYOUT.md) — metadata
  compilation, packaging, and future bytecode/native deployment options.
- [Roadmap](ROADMAP.md) — implemented milestones and remaining work.

Run `start.bat help` on Windows or `uv run tide --help` on any platform for the
current command summary.
