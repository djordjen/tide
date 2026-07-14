# TIDE Invoicing Application

This is the golden vertical-slice application proposed in the TIDE roadmap. It
is now an executable compiler and secured headless-runtime fixture, not yet a
complete database or Textual application.

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
- immutable-when-posted metadata for invoice and line edits.
- a headless create/edit/post/retry workflow with row/field security and stale
  update rejection.

Validate it from the repository root:

```bash
uv run tide model validate applications/invoicing
uv run tide model explain sales.Invoice.status --project applications/invoicing
```

Deployment-specific database URLs and credentials are intentionally absent.
SQLite and PostgreSQL configuration will be supplied through environment or
deployment configuration once the runtime exists.
