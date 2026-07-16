# Designers and Reporting

## Principle

Designers edit metadata and overlays, not generated source code. A preview uses
the same compiler and renderer as the deployed application.

Generated browses, forms, lookups, and basic reports must work before visual
designers exist. Building designers after metadata contracts stabilize avoids
embedding early model mistakes in a large tooling codebase.

## Headless DesignerService

TIDE Studio is built around a UI-independent command service:

```text
Textual designer ----+
                     +--> DesignerService --> model compiler --> YAML overlays
Web designer --------+
                     |
Developer MCP -------+
```

Candidate commands are:

```text
add_node
move_node
set_property
remove_override
validate_view
preview_view
undo
redo
save
```

Commands operate on stable model paths and produce diagnostics and diffs. This
supports undo/redo, source control, AI assistance, and multiple designer
frontends without duplicating editing rules.

## TUI view designer

A Textual designer can dogfood TIDE and support:

- model and component trees;
- keyboard and mouse selection;
- move, nest, unnest, resize, and reorder operations;
- fields, groups, tabs, collections, and action bars;
- property inspector;
- undo, redo, clipboard, and source diff;
- preview at several terminal dimensions;
- preview as a selected role;
- resolved-model explanation.

The first version should favor structural tree editing over free-form dragging.
Move-up, move-down, nest, unnest, and property editing provide most practical
value with substantially less complexity.

## Web view designer

A local browser-based TIDE Studio may later add:

- responsive breakpoint previews;
- drag-and-drop structural layout;
- theme and design-token editing;
- accurate typography and page sizing;
- web-renderer-specific properties;
- side-by-side TUI and web previews.

Using a browser for development tooling does not make deployed TIDE
applications web applications.

## AI-assisted design

The developer MCP server can expose DesignerService safely. An agent can inspect
the entity model and presets, propose a structured layout patch, validate it,
render a preview, and present the YAML diff for approval.

Useful tools include:

```text
tide_get_resolved_view
tide_create_view
tide_move_view_node
tide_set_view_property
tide_validate_view
tide_preview_view
tide_preview_report
```

## Report model

Reports use a declarative banded model inspired by classic business report
formatters:

```text
Report Header
Page Header
Group Header
Detail
Group Footer
Page Footer
Report Footer
```

An initial invoice report may look like:

```yaml
report: sales.invoice
title: Invoice
entity: sales.Invoice
permission: sales.invoice.report
expose: {rest: true}

parameters:
  invoice_id: {type: integer, required: true}

query:
  criteria: "id == $invoice_id"

bands:
  report_header:
    - text: Invoice
      style: report_title

  record_header:
    - field: number
      label: Invoice number
    - field: customer
      label: Customer

  detail:
    source: lines
    columns: [product, quantity, unit_price, total]

  report_footer:
    - field: total
      label: Total
      format: money

  page_footer:
    - expression: "'Page ' + page_number"
```

The executable v0.1 subset is intentionally a secured `kind: record` report.
Its query must bind the entity primary key to one required parameter, so the
runtime can perform an indexed, row-policy-aware `RecordsService.get` instead
of loading and filtering a table in memory. The compiler validates report
access, parameter and expression types, root fields, the detail collection,
detail columns, and named formats. Reports fail closed if any requested field
is protected. REST delivery is independently opt-in through
`expose.rest: true`; declaring a report permission alone does not create an
HTTP route.

## Report capabilities

The intended progression includes:

- typed required parameters with validation; **implemented for record reports**
- sorting, filtering, grouping, and totals;
- shared formats and computed expressions; **implemented**
- page size, orientation, and margins;
- repeating table headers and numbered page footers; **implemented in PDF**
- page breaks and keep-together behavior;
- tables, text, images, and later barcodes;
- standalone HTML output, native A4 PDF output, and TUI preview; **implemented**
- controlled CSV and spreadsheet export;
- subreports after the core band model is stable.

A browser-based designer is the likely primary visual report designer because
it can show a realistic page canvas. A TUI editor can still provide band-tree
and property editing.

## Reporting security

Reports request data through secured record and report services. They may not
create unrestricted SQLAlchemy sessions. Entity, row, field, report, and export
permissions all apply. Protected fields and computed-field inference rules
remain active. Durable report/export audit is still a future operational
contract and is not implied by the initial renderer.

## Initial rendering strategy

`ReportService` first creates an immutable, renderer-neutral `ReportDocument`
containing only authorized, already formatted values. Textual renders that
document as a terminal preview. A standard-library renderer writes standalone
print CSS/HTML, while the optional `report` package extra uses ReportLab to
write A4 PDF directly with Unicode-capable system-font discovery, repeating
table headings, numeric alignment, and page numbering. Output defaults to
`output/reports/` below the process working directory.

For remote Textual mode, the server builds this same secured document and
transports its versioned structure. The client validates it and reuses the
ordinary preview/HTML/PDF renderers; raw records and database credentials never
move to the reporting client.

This establishes the adapter boundary without claiming a pixel-perfect report
engine. Grouping, images, arbitrary result-set reports, configurable page
geometry, keep-together controls, and durable report audit remain later work.
