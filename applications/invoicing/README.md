# TIDE Invoicing Application

This is the golden vertical-slice application proposed in the TIDE roadmap. It
is an executable compiler and secured headless-runtime fixture with an initial
metadata-driven Textual invoice browse.

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
- application-owned typed demo records for the runnable TUI.

Validate it from the repository root:

```bash
uv run tide model validate applications/invoicing
uv run tide model explain sales.Invoice.status --project applications/invoicing
uv run tide run applications/invoicing --demo --page-size 3
```

The `--demo` flag explicitly executes this application's `demo_data.py` and
loads an in-memory repository; it never changes a database. Omit `--page-size`
to use the browse metadata default of 25. The initial visible slice supports
secured browsing, next/previous paging, refresh, row selection, keyboard, and
mouse controls. Editing and deployment database selection are the next slices.

Deployment-specific database URLs and credentials remain intentionally absent.
Microsoft SQL Server is the first multi-user deployment target.
