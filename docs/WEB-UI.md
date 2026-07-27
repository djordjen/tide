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
  token and its record response supplies an ETag.

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
Record responses may include server-evaluated `writable_fields` hints so the
browser can distinguish locked fields. These hints and the browser's early
validation are advisory: every mutation is authorized, normalized, and
validated again by FastAPI and the shared services.

Create sends only currently writable scalar fields. Update sends only changed
fields and returns refreshed per-record field state. If a record `GET` supplies
an ETag, Web returns it through `If-Match`; a stale response is shown as an
explicit concurrency message instead of silently overwriting the record.
Validation responses may carry safe field-addressable issues so the browser can
place the authoritative message beside the corresponding editor.

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

## Next slice

The next Web milestone is renderer acceptance coverage for the semantic
behaviors now shared by Textual, Qt, and Web. After that, lookup selection and
**Save & Select** are the next editing vertical slice. Invoice master-detail
drafts, conflict review, domain actions, and report preview remain separate
reviewed milestones.
