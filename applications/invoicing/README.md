# TIDE Invoicing Application

This is the golden vertical-slice application proposed in the TIDE roadmap. It
is an executable compiler and secured headless-runtime fixture with a
metadata-driven Textual invoice workflow.

For a task-oriented tour with current screenshots and direct links from each
behavior to its metadata, see the
[Invoicing Application Walkthrough](../../docs/INVOICING-WALKTHROUGH.md).

It demonstrates:

- models split by module and entity;
- cross-module references and master-detail collections;
- generated TUI, REST, and MCP exposure;
- computed line and invoice totals;
- shared presentation defaults and formats;
- named filters and an invoice browse overlay;
- declarative secured invoice and posted-sales summary reports;
- strict metadata v0.1 validation and source-located diagnostics;
- action-owned posting state and an optimistic concurrency token;
- idempotent posting behavior with audit stamps and decimal rounding;
- immutable-when-posted metadata for invoice and line edits;
- a headless create/edit/post/retry workflow with row/field security and stale
  update rejection;
- application-owned typed demo records for the runnable TUI;
- application-owned `runtime.py` registration for number allocation and
  posting behavior;
- metadata-driven create/edit forms and inline InvoiceLine editing;
- secured Save/Cancel/Post behavior with validation, action audit, immutable
  posted records, and stale-version feedback.

Validate it from the repository root:

```bash
uv run tide model validate applications/invoicing
uv run tide model explain sales.Invoice.status --project applications/invoicing
uv run tide run applications/invoicing --demo --page-size 3
uv run tide serve applications/invoicing --demo
```

The server command additionally requires a development token of at least 32
characters in `TIDE_API_TOKEN`. On Windows, `start.bat api-demo` generates and
prints an ephemeral token, starts the loopback-only server, and makes the
interactive contract available at `http://127.0.0.1:8000/docs`.
It grants the local demo token both sales-clerk and auditor capabilities so the
executable [REST API client tutorial](../../docs/API-CLIENT-TUTORIAL.md) can
also retrieve the safe audit event produced by its Invoice workflow.
The contract exposes secured list/get/create/update routes for the declared
entities and the Invoice Post action. Invoice mutations require the `ETag` from
a prior GET as `If-Match`; Post also requires a unique `Idempotency-Key`.
Provider-neutral OIDC/JWKS access-token validation and direct TLS are available
for reviewed network deployments; see
[REST API and MCP](../../docs/API-AND-MCP.md#current-application-server).

`start.bat mcp-demo` additionally mounts authenticated runtime MCP at
`http://127.0.0.1:8000/mcp`. It exposes principal-filtered entity schemas,
record and audit templates, structured search and explicitly opted CRUD tools
for Customers and Products, create/update tools for Invoices, and the
idempotent Post Invoice action. The isolated shortcut grants both sales-clerk
and auditor roles so the complete create/post/history proof of concept can be
tested. Every call reuses `RecordsService`, `ActionService`, and
`AuditHistoryService`; no database URL, repository, arbitrary SQL, or
project-editing capability is given to the MCP client.
Use `start.bat mcp` for the same full local role combination backed by the
configured SQL Server database; successful mutations then persist normally.

With that server still running, `start.bat remote` prompts for the printed token
and opens the same Textual workflow as an HTTP client. It supports browse,
search, filters, sorting, paging, lookups, nested create/edit, and posting while
receiving no database URL. Invoice report documents are built and authorized
on the server, then previewed and exported to CSV/HTML/PDF by the remote TUI.

The `--demo` flag explicitly executes this application's `demo_data.py` and
loads an in-memory repository; it never changes a database. Omit `--page-size`
to use the browse metadata default of 25. Select a row with Enter or the mouse,
or use **New**. Forms support invoice headers and line items; Ctrl+S saves,
Ctrl+P posts an eligible draft, Ctrl+N adds a line, and Escape cancels.
New invoices default to today's date. Date entry accepts `DD.MM.YYYY`,
`DD/MM/YYYY`, or ISO `YYYY-MM-DD`; with the date focused, `+` and `-` move it
forward or backward one day. Compact editable controls use a strong accent,
while read-only labels and values use muted italic styling. Tab traverses each
form section down the left column and then down the right column. Enter advances
from text, date, and collapsed selection fields; Space or an arrow opens a
selection list, where Enter confirms the highlighted value.
Product uses the alternative lookup editor: Space, Down, or F4 opens a secured
multi-column Product search. Selecting a Product copies its name and current
unit price into the editable line draft; the stored invoice price remains a
historical snapshot and may be edited before saving.
The line table keeps its reporting-oriented column order, while the explicit
line editor places Line Number, Product, and Description in the left column and
Unit Price and Quantity in the right. Its focus order follows that same
sequence: down the left column first and then down the right.
Integer and decimal values are right-aligned in browse, line-item, and lookup
tables so values of different widths share a common numeric edge.
Invoice quantities and prices use metadata numeric masks that limit fractional
entry to their declared scale and complete fixed decimal places on leaving the
field. Customer and Product codes demonstrate regular-expression masks; these
rules are enforced through the shared services as well as reflected in the TUI
and OpenAPI contract.
The workspace selector opens Invoices, Customers, or Products. Customer and
Product forms support secured create/edit operations. Customer and Product
lookups also expose **New** (Ctrl+N) when authorized; the nested form uses
**Save & Select**, preserving the unsaved invoice and applying Product defaults.
The browse search applies incrementally to invoice numbers. The filter selector
exposes **Draft invoices** and **High-value invoices** from view metadata, and
the sort selector or eligible column headers toggle secured ascending and
descending queries. **Clear** restores the default browse query.

The auditor role may select an invoice, customer, or product and choose
**History** or press `H` to open newest-first action and CRUD history. The
screen shows operations, configured safe field changes, outcomes, principals,
channels, timestamps, and correlation identifiers. Protected values are
redacted and payload/idempotency secrets are never included. The sales-clerk
role does not receive audit permissions, so the control remains hidden.

With the repository's Windows shortcut, run `start.bat auditor` to inspect
persisted SQL Server history, or `start.bat auditor-demo` for the isolated demo.
Seeded demo records have no prior events because seeding writes the repository
directly. Create/edit a record or post an invoice as the sales clerk first when
testing persistent history.

On the Invoice workspace, select a saved invoice and choose **Preview** or
press `V`. The preview is built from the secured `sales.invoice` report and can
export its detail table as CSV, standalone HTML, or A4 PDF. Files are written
to `output/reports/` below the directory from which TIDE was started. The sales
clerk and auditor roles have report access; a role without
`sales.invoice.report` does not see the
preview action.

**Export CSV** writes the visible detail table as an Excel-friendly UTF-8 file
while neutralizing formula-looking text values. Choose **Summary** or press `S`
from the Invoice workspace to build the secured `sales.summary` report. It
queries only posted invoices through `RecordsService`, groups them by Customer
and Currency, and shows invoice count and sales total. The report refuses to
return partial totals if its declared 500-row source limit is exceeded; narrow
criteria or revise the reviewed metadata before using a larger operational
dataset.

Running an application may also execute its fixed `runtime.py` file. That file
does not implement persistence or UI behavior; it explicitly registers the
application's ordinary Python generators and action handlers with the shared
TIDE services.

Deployment-specific database URLs and credentials remain intentionally absent.
Microsoft SQL Server is the first multi-user deployment target. Set
`TIDE_DATABASE_URL` outside the application and run with `--database-env` to
select it; add `--create-schema` only for the first explicitly managed setup.
See [the SQL Server runtime instructions](../../docs/SQL-SERVER.md#run-the-tui-against-sql-server).

After managed tables are initialized, an empty development database can be
filled through the real validation/action services:

```powershell
uv run --extra seed --extra sqlserver tide db seed applications/invoicing --database-env --customers 25 --products 20 --invoices 100 --random-seed 20260716
```

This requests both the `seed` and SQL Server extras and refuses a non-empty
database. The Windows `start.bat seed` shortcut uses the same dependencies.
