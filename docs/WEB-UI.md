# Web UI

**Status: generic metadata-driven browse, secured detail, and flat-form
editing slices are implemented.**

TIDE Web is a reusable React renderer for compiled TIDE applications. The
checked-in Invoicing application is the golden example, but the browser code
contains no Invoicing-specific entities, fields, routes, or layouts.

## Run the isolated demo

Install Node.js 20 or later, then run from the repository root:

```powershell
.\start.bat web-demo
```

The shortcut:

1. creates and prints a loopback-only development bearer token;
2. installs the locked Web dependencies when needed;
3. starts the demo FastAPI application and Vite development server; and
4. opens the Web connection screen.

Paste the printed token and choose **Connect**. The token remains only in the
current JavaScript runtime; it is not written to browser storage. Stop both
processes with `Ctrl+C`.

To use the configured local SQL Server database instead:

```powershell
.\start.bat web
```

This uses the same `TIDE_DATABASE_URL` defined inside `start.bat` as the other
persistent modes. The browser never receives that URL or connects to the
database.

## What is implemented

- responsive application shell with grouped, capability-filtered navigation;
- light and dark themes;
- server-side search, named filters, sorting, and opaque-cursor loading;
- automatic incremental loading near the bottom of the visible grid;
- row virtualization, so loaded records do not all become DOM elements;
- exact decimal and metadata-defined date formatting;
- authorized reference display through generated REST resources;
- right-aligned numeric columns and protected-value presentation;
- drag reordering and manual column resizing;
- **Best Fit all**, per-column Best Fit, **Fill available**, and
  **Reset app layout**;
- personal column order and widths scoped by application, principal, and view.
- row selection plus one **Open** path through button, double-click, or Enter;
- stable in-place record detail without closing or repositioning the shell;
- renderer-neutral form rows, groups, tabs, and inline collections projected
  from the same compiled YAML layout used by Textual and Qt;
- clearly distinguished workflow-locked and writable fields, based on safe
  per-record state evaluated by the server;
- Previous/Next navigation in the current secured browse order, including
  cursor-boundary loading and Page Up/Page Down shortcuts;
- capability-gated **New** and **Save** workflows for flat scalar Customer and
  Product forms;
- typed Boolean, choice, text, email, date, datetime, integer, and exact
  decimal controls, including metadata defaults, required state, length,
  regular-expression, numeric-mask, precision/scale, range, and choice hints;
- Enter traversal, date `+`/`-` shortcuts, changed-fields-only updates, and
  field-addressable client and server validation feedback;
- optimistic update protection whenever the entity declares a concurrency
  token and its record response supplies an ETag;
- explicit Original/Current/Your draft review after a stale update, including
  Current/Mine overlap choices, safe draft-only rebase, current workflow-lock
  reevaluation, and a fresh ETag-backed form for review before saving again.

The YAML column order remains the shared application default. Ordinary browser
reordering and resizing are personal preferences and never edit application
metadata. New and removed fields reconcile by stable field name. Best Fit
measures only the loaded server window, runs once for a new personal layout,
and does not repeatedly resize after every refresh.

## Architecture and security

The renderer uses React and TypeScript with Vite, Tailwind CSS, shadcn/ui-style
code-owned components, TanStack Query, TanStack Table, and TanStack Virtual.
The browser receives the authenticated presentation manifest and calls only
generated `/api/...` resources. FastAPI and the shared application services
remain authoritative for authentication, permissions, row policies,
validation, concurrency, actions, and auditing.

Portable metadata contains semantic presentation intent, not Tailwind class
names or shadcn component names. A renderer maps shared field types, formats,
alignment, and future conditional-style tokens to its own controls. This keeps
the same YAML meaningful to Textual, Qt, Web, and later renderers.

The detail manifest is also a safe projection. It contains resolved semantic
layout plus the editor facts needed to render a useful form: type, writable
hint, required state, help, choices, bounded masks, numeric constraints,
validation names, and already-resolved defaults. It contains no YAML
expressions, permission/workflow rules, handlers, or database configuration.
For an authorized reference field configured with `editor: lookup`, the same
manifest adds only the compiler-approved lookup view, readable columns,
searchable fields, bounded fetch size, target REST paths, and allowed nested
create form. A missing list/read/write capability removes the lookup rather
than weakening it.
Record responses may include server-evaluated `writable_fields` hints so the
browser can distinguish locked fields. These hints and the browser's early
validation are advisory: every mutation is authorized, normalized, and
validated again by FastAPI and the shared services.

Create sends only currently writable scalar fields. Update sends only changed
fields and returns refreshed per-record field state. If a record `GET` supplies
an ETag, Web returns it through `If-Match`. A stale response loads the latest
secured record and opens an explicit three-way review. Draft-only changes can
be retained safely; overlapping changes require a Current or Mine choice.
Collections such as Invoice Lines are deliberately compared as one unit rather
than merged row by row. Applying the resolution reloads current server values,
drops fields newly locked by workflow rules, and reopens the resolved draft on
the latest ETag. It never writes automatically: the user reviews and saves
again, so FastAPI repeats authorization, normalization, validation, and
concurrency checks.
Validation responses may carry safe field-addressable issues so the browser can
place the authoritative message beside the corresponding editor.

Invoice headers now use that reference contract for Customer. **Select…** opens
a debounced multi-column table and searches every YAML-declared readable search
field through ordinary structured REST queries. Choosing a row calls
`/_tide/reference-selection`; React never implements `on_select` assignments.
When `allow_create: true`, target create permission, and a compiled target form
all agree, **New Customer** opens a nested metadata-driven form.
**Save & Select** creates that independent master record, applies the returned
identity through the same server operation, and returns to the unchanged
Invoice draft.

Invoice lines use the same pattern through the compiler-resolved
`sales.InvoiceLine.inline_edit` view. The safe manifest exposes the line
identity, readable table columns, authorized writable fields, semantic editor
rows, nested draft operations, and YAML Add/Apply/Remove order only when all
required capabilities agree. Selecting Product opens its multi-column lookup;
**New Product → Save & Select** preserves the Invoice and line draft while the
server returns Description and Unit Price assignments. Add, edit, and remove
remain local until Invoice **Save** sends one complete nested replacement with
the observed ETag. FastAPI then reauthorizes, normalizes, validates, handles
orphans, and recalculates stored line and Invoice totals transactionally.

The current development connection screen accepts a bearer token manually.
Provider-specific interactive browser sign-in, token refresh, and logout are a
later production identity slice.

## Build and host

Build the static renderer:

```powershell
cd web
npm ci
npm run build
cd ..
```

FastAPI can host that exact build at the same origin:

```powershell
$env:TIDE_API_TOKEN = "replace-with-a-development-token"
uv run --extra api tide serve applications/invoicing --demo `
  --role sales_clerk --role auditor --web-root web/dist
```

Open <http://127.0.0.1:8000>. API routes are registered before the static
renderer, so Web hosting cannot shadow `/api`, `/docs`, OpenAPI, health, or MCP
routes. Fingerprinted `/assets/` files receive immutable caching while the HTML
entry point remains uncached.

This command is a local development example. A production deployment still
requires reviewed OIDC, TLS, process supervision, resource limits, and the
operational controls in [Operational baseline](OPERATIONS.md).

## Validate the renderer

```powershell
cd web
npm ci
npm run check
npm run build
npx playwright install chromium
npm run test:e2e
```

`check` runs TypeScript plus Vitest unit/component tests. Playwright supplies a
small browser smoke test for the built connection surface. CI runs all of these
against one supported Node version alongside the Python 3.11 framework suite.

## Current boundary and next slice

The executable
[renderer acceptance matrix](RENDERER-ACCEPTANCE.md) now protects the shared
semantic baseline and records deliberate gaps. Multi-column lookup selection
and nested **Save & Select** are covered for scalar references such as
Invoice Customer and collection references such as InvoiceLine Product.
Transactional Invoice master-detail drafts are also covered. Three-way
conflict review now has the same loss-prevention semantics in Textual, Qt, and
Web. Domain actions and report preview remain separate reviewed milestones.
