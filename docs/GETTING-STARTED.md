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

- Python 3.11 or newer;
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
Model is valid: TIDE Invoicing 0.1.0 (4 entities, 9 views, 1 reports, 0 warning(s)).
```

On Windows, launch the isolated demo with:

```powershell
.\start.bat demo
```

The equivalent cross-platform command is:

```bash
uv run --extra tui tide run applications/invoicing --demo --page-size 5
```

The `--demo` switch loads application-owned sample records into memory. Closing
the process discards every change.

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

In a second terminal, verify the server or open the TUI as a remote API client:

```powershell
.\start.bat api-check
.\start.bat remote
```

Both commands securely prompt for the printed token. The remote TUI receives
no database URL: browse, lookup, mutation, report, concurrency, and action calls
all pass through FastAPI and the server-side services.

To inspect the generated OpenAPI document without starting a server:

```powershell
uv run tide api export-openapi applications/invoicing
```

See [REST API and MCP](API-AND-MCP.md) for filtering, ETags, idempotency,
production identity, and deployment requirements.

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

ChatGPT itself requires a supported remote connector or Secure MCP Tunnel;
`localhost` cannot be registered directly. Keep that deployment step separate
from this local functional test.

Developer MCP is a different, local stdio surface for inspecting and proposing
TIDE application definitions:

```powershell
uv run --extra mcp tide mcp dev applications/invoicing
```

It can produce deterministic proposals and validated candidate artifacts, but
cannot apply them or write arbitrary workspace files. See
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
.\start.bat
```

Use `start.bat auditor` for the persisted read-only audit/report workspace.
For an externally owned schema that TIDE must not change, follow the separate
[legacy database no-DDL contract](LEGACY-DATABASES.md). Complete driver,
connection, and troubleshooting guidance is in
[Microsoft SQL Server](SQL-SERVER.md).

## Create another application

`applications/invoicing` is a reference application, not a hard-coded part of
the runtime. Additional applications belong in independent
`applications/<name>/` directories and may define different models, views,
reports, security, mappings, and optional handlers.

There is not yet a general `tide new` wizard. Today, developers can either:

- create the manifest and YAML files directly using the metadata references;
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
- [Architecture](ARCHITECTURE.md) — service, model, repository, and adapter
  boundaries.
- [Security](SECURITY.md) — permissions, protected values, authentication, and
  fail-closed behavior.
- [Compilation and application layout](COMPILATION-AND-LAYOUT.md) — metadata
  compilation, packaging, and future bytecode/native deployment options.
- [Roadmap](ROADMAP.md) — implemented milestones and remaining work.

Run `start.bat help` on Windows or `uv run tide --help` on any platform for the
current command summary.
