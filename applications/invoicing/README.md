# TIDE Invoicing Application

This is the golden vertical-slice application proposed in the TIDE roadmap. It
is an executable compiler and secured headless-runtime fixture with a
metadata-driven Textual invoice workflow.

It demonstrates:

- models split by module and entity;
- cross-module references and master-detail collections;
- generated TUI, REST, and MCP exposure;
- computed line and invoice totals;
- shared presentation defaults and formats;
- named filters and an invoice browse overlay;
- a declarative secured invoice report;
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
```

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
The browse search applies incrementally to invoice numbers. The filter selector
exposes **Draft invoices** and **High-value invoices** from view metadata, and
the sort selector or eligible column headers toggle secured ascending and
descending queries. **Clear** restores the default browse query.

Running an application may also execute its fixed `runtime.py` file. That file
does not implement persistence or UI behavior; it explicitly registers the
application's ordinary Python generators and action handlers with the shared
TIDE services.

Deployment-specific database URLs and credentials remain intentionally absent.
Microsoft SQL Server is the first multi-user deployment target; selecting a
deployment repository from `tide run` remains the next adapter slice.
