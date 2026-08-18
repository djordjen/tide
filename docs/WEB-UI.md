# Web UI

**Status: generic metadata-driven browse, secured detail/editing, lookup,
master-detail, conflict-review, domain-action, report, and browser-identity
slices are implemented.**

TIDE Web is a reusable React renderer for compiled TIDE applications. The
checked-in Invoicing application is the golden example, but the browser code
contains no Invoicing-specific entities, fields, routes, or layouts.

## Run the isolated demo

Install Node.js 20 or later, then run from the repository root:

```powershell
.\start.bat web-demo
```

The shortcut:

1. securely prompts for the first local `admin` password when needed;
2. installs the locked Web dependencies when needed;
3. starts the demo FastAPI application and Vite development server; and
4. opens the username/password sign-in screen.

Sign in as `admin` with the password you chose. The password and opaque session
cookie are never written to browser storage. Stop both processes with `Ctrl+C`.

To use the configured local SQL Server database instead:

```powershell
.\start.bat web
```

This uses the same `TIDE_DATABASE_URL` defined inside `start.bat` as the other
persistent modes. The browser never receives that URL or connects to the
database.

## Open any application while developing

`--auth development` serves the built renderer and asks for no credential to
enter it. Build the renderer once, then point a server at any application:

```bash
npm --prefix web run build
```

```bash
export TIDE_API_TOKEN=$(uv run python -c "import secrets; print(secrets.token_urlsafe(32))")
```

```bash
uv run --extra api --extra client tide serve applications/invoicing --demo --role sales_clerk --port 8000 --web-root web/dist
```

Open <http://127.0.0.1:8000/> and choose **Open without signing in**. The token
is still needed to *start* the server, because the REST API still wants it --
`curl`, Swagger and the typed client are unchanged -- but nothing has to be
typed into the browser. The session carries whatever `--principal` and `--role`
named, so this is also how to see a screen the way one role meets it.

Three things keep that off a network, and the first two refuse at startup: the
bind must be loopback, and the identity adapter must not be a production one.
The third is per request -- a `Host` header naming anything but this machine is
answered `403 non_loopback_host`, which is what closes DNS rebinding. Details in
[Operations](OPERATIONS.md). `--auth local` remains the mode for a real
username and password, and the only one to put in front of anybody else.

Remember the renderer is served from `web/dist`, so an edit under `web/src` is
invisible until it is rebuilt.

## What is implemented

- responsive application shell with grouped, capability-filtered navigation,
  which below the sidebar's breakpoint collapses into one workspace select. It
  names the application and the version the manifest reports, so a support
  conversation can start with which build is on the screen; from ten entries
  up the navigation gains a filter box, and a group whose every entry is
  filtered out leaves with them rather than heading an empty space.
  **375px is the supported floor and is checked**: the Web UI is the only TIDE
  surface a phone can run, since the terminal client cannot, so a layout that
  holds only at desk widths is a defect rather than a trade-off;
- the open view and the open record in the address bar as `?view=` and
  `?record=`, so a screen can be linked to, survives a refresh, and answers the
  back button; a named view is checked against the manifest rather than
  trusted, and leaving a view closes the record that belonged to it;
- a split bundle: the sign-in page loads 331 kB of JavaScript rather than 563,
  because the application shell and the record screen are fetched separately.
  The shell is fetched while the sign-in form is being read, so the split costs
  no visible wait; a Playwright check holds the entry chunk under a ceiling,
  since one static import would put it all back;
- light and dark themes;
- a browser tab named after the screen it shows — `Invoices · TIDE Invoicing`,
  or `Invoice — INV-2026-0002 · TIDE Invoicing` — most specific first, because
  a tab truncates from the right. The record's half is the same string its
  heading shows rather than a second derivation of it, and the shell puts its
  own name back when a session ends. An SVG icon carries the same mark as the
  sidebar;
- server-side search, named filters, sorting, and opaque-cursor loading;
- automatic incremental loading near the bottom of the visible grid;
- row virtualization, so loaded records do not all become DOM elements;
- exact decimal and metadata-defined date formatting;
- authorized reference display through generated REST resources;
- right-aligned numeric columns and protected-value presentation;
- drag reordering and manual column resizing;
- **Best Fit all**, per-column Best Fit, **Fill available**, and
  **Reset app layout**;
- personal column order and widths scoped by application, principal, and view;
- row selection plus one **Open** path through button, double-click, or Enter;
- a browse grid that is one tab stop rather than one per row: the selected row
  owns it, Up/Down move it, Home and End reach the ends of what is loaded, and
  moving it selects, so **Open** and the record pane follow the keyboard the
  same way they follow a click;
- stable in-place record detail without closing or repositioning the shell;
- renderer-neutral form rows, groups, tabs, and inline collections projected
  from the same compiled YAML layout used by Textual;
- inline collection wording and row numbering taken from the manifest —
  `record_label` names the rows and `sequence_field` names the field a new row
  is numbered by, so the widget carries no application's field names or
  vocabulary. The row controls sit above the rows they act on: below the table
  they lived under an editor card whose height changes with every row
  selected, so the control being reached for was somewhere new each time;
- clearly distinguished workflow-locked and writable fields, based on safe
  per-record state evaluated by the server. A writable field is an input; a
  locked one is the value as text. Neither is wrapped in a card: a field spends
  at most 24px above its control on a label and gap, which a Playwright check
  measures in both renderers rather than trusting, because jsdom computes no
  layout;
- code-owned controls for every editor one exists for. A closed set — a
  `choice` field, or one captioned by `values:` — is a vendored `Select` rather
  than a native one, so its popup wears the application's own surface instead
  of the operating system's. Two exceptions are deliberate and marked where
  they are: the boolean tick box, which `Input` is the wrong shape for, and the
  phone navigation switcher, where a native select opens the platform's own
  picker;
- the field label beside its control rather than above it, from 768px up, and
  stacked below that because a phone has no second column to give it. A label
  above doubles the vertical space a record needs, which is the same budget the
  field cards used to waste and the reason collections fell below the fold; the
  terminal renderer and the desktop applications this replaces both read down a
  single value column. One rule in `index.css` covers both renderers, and a
  Playwright journey asserts the label ends before the control begins at
  1440px and sits above it at 375px;
- Previous/Next navigation in the current secured browse order, including
  cursor-boundary loading and Page Up/Page Down shortcuts;
- capability-gated **New** and **Save** workflows for flat scalar Customer and
  Product forms;
- **Save and New** on the create screen, because entry arrives in runs: the
  record is written and the form comes back empty with the model's defaults
  in place and the cursor in the first field, while the grid behind it
  refreshes for whenever the run ends. Plain **Save** still closes to the
  grid, and an existing record is offered neither — it is one record;
- typed Boolean, choice, text, email, date, datetime, integer, and exact
  decimal controls, including metadata defaults, required state, length,
  regular-expression, numeric-mask, precision/scale, range, and choice hints;
- Enter traversal, date `+`/`-` shortcuts, changed-fields-only updates, and
  field-addressable client and server validation feedback;
- modal dialogs — reference lookup, conflict review, and report preview — that
  take focus when they open, hold `Tab` and `Shift+Tab` within their own
  controls, and hand focus back to whatever opened them, so `aria-modal="true"`
  is enforced rather than only declared;
- optimistic update protection whenever the entity declares a concurrency
  token and its record response supplies an ETag;
- explicit Original/Current/Your draft review after a stale update, including
  Current/Mine overlap choices, safe draft-only rebase, current workflow-lock
  reevaluation, and a fresh ETag-backed form for review before saving again;
- metadata-ordered, capability-gated domain actions with server-evaluated
  per-record visibility and enabled state;
- save-then-action execution for changed drafts using the saved record's fresh
  ETag plus a unique idempotency key where the action requires one;
- capability-filtered record and summary report discovery, including each
  summary's declared parameters;
- responsive report preview from the server-built immutable document, with a
  parameter form when the summary declares one; and
- controlled CSV, standalone HTML, and PDF downloads generated by FastAPI.

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
the same YAML meaningful to Textual, Web, and later renderers.

The detail manifest is also a safe projection. It contains resolved semantic
layout plus the editor facts needed to render a useful form: type, writable
hint, required state, help, choices, bounded masks, numeric constraints,
and already-resolved defaults. It contains no YAML
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

Domain-action discovery follows the same fail-closed projection. The manifest
contains only the authorized REST action name, label, order, and idempotency
requirement; it never exposes permission, visibility, or workflow expressions.
Each record response carries server-evaluated `visible` and `enabled` hints,
while the action endpoint repeats authorization and business-rule checks.
When a form has unsaved changes, Web first sends the ordinary ETag-protected
update. An action disabled for the stored record may therefore become available
after draft changes such as adding the first line; Web rechecks the fresh
server-returned action state before invoking it with the returned ETag. The
**Post** result replaces the current form data in place, immediately
locking fields and disabling the action according to the returned record state.

Report discovery is equally narrow. The presentation manifest exposes only an
authorized report's safe name, title, record/summary kind, owning entity,
generated resource path, supported export formats, and -- for a summary --
each parameter's name, label, type, and required flag. It does not expose the
report query, criteria, bands, expressions, permission names, or database
configuration. **Posted Sales Summary** on the Invoice list and **Preview
Invoice** on a saved detail both ask FastAPI to build the same immutable
`ReportDocument` used by Textual. React renders its already-formatted
values and tables, including a grouped listing's bands -- each group's heading
and subtotal are full-width rows in the one table; CSV, HTML, and PDF buttons
call separately authorized export routes that rebuild and render the report
server-side. A summary that declares parameters renders a form from that
manifest metadata, with native date pickers for date values. The inputs
collect strings and nothing else -- typing and the required check stay with
the report service, so a wrong value reads the same in the browser as in the
terminal -- and a blank optional input is simply not sent, which drops its
criteria clause. An all-optional summary still builds immediately; the form
then narrows and rebuilds it. A summary with a required parameter waits for
the form instead of rendering the service's refusal, and the manifest counts
a server-side default as satisfying its parameter, so only a value the caller
must actually supply is flagged required. Exports always send the values the
visible preview was built with, never an edit that has not been built, so a
download cannot disagree with the screen. Record preview is
disabled while the form has unsaved changes so the displayed document cannot
silently disagree with the draft.

Invoice headers now use that reference contract for Customer. The reference is
one combobox-shaped well: the chosen value, then its controls on the trailing
edge. The ellipsis picker opens a debounced multi-column table and searches
every YAML-declared readable search field through ordinary structured REST
queries; choosing a row calls `/_tide/reference-selection`, and React never
implements `on_select` assignments. An open-record door beside it deep-links
the referenced record's own screen in a new tab — offered only when the
capability-filtered manifest carries a browse view with a detail form for the
target entity, so a person who may not see Customers simply gets no door. A
clear control empties the reference through the same draft path as selection,
and only where the field is not required, because emptying a required
reference could only manufacture the service's refusal.

Where a reference is read rather than edited — a locked field on a posted
record, a collection row, a browse grid cell — the resolved name itself is
the door, the way the reference application draws its grids. Following it
navigates in place with one history entry, marked so the opened record's
**Close** walks back to exactly where the person was; a ctrl, shift or
middle click still opens a tab, because these are real anchors. The editable
well's door keeps its new tab deliberately: a side trip from an open draft
must not be able to lose it. Grid links carry `tabindex="-1"` so the grid's
roving tab stop keeps owning the keyboard. When
`allow_create: true`, target create permission, and a compiled target form
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

The normal Web shortcuts use TIDE's local username/password sign-in, restore
its opaque same-origin session automatically, add per-session CSRF proof to
mutations, and perform local logout. React never receives the password, its
hash, database credentials, or role-selection authority. The optional OIDC
adapter is retained only for deployments that may choose it later. See
[Web authentication](WEB-AUTHENTICATION.md) for user management and the current
single-process session boundary.

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
uv run --extra api --extra report tide serve applications/invoicing --demo `
  --auth local --local-auth-store .tide/local-auth.sqlite3 `
  --web-root web/dist
```

Open <http://127.0.0.1:8000>. API routes are registered before the static
renderer, so Web hosting cannot shadow `/api`, `/docs`, OpenAPI, health, or MCP
routes. Fingerprinted `/assets/` files receive immutable caching while the HTML
entry point remains uncached.

`build_fastapi_app(base_path=...)` decides where the API lives, and the
manifest builds every resource, query, selection, report, and
browser-authentication path from it. A renderer built for one base path cannot
talk to a server using another: its first request misses, and any manifest path
outside the configured base is refused as unsafe. Both defaults are `/api/v1`,
so an ordinary deployment sets nothing. A deployment that moved the API — for
example the whole application hosted under `https://host/tide/` — tells the
build where it went:

```powershell
cd web
$env:VITE_TIDE_BASE_PATH = "/tide/api/v1"
npm run build
```

The value is inlined at build time, so one bundle belongs to one deployment.
The development server reads the same variable to decide what to proxy.

This command is a local development example. A non-loopback host also requires
direct TLS, process supervision, resource limits, and the controls in
[Operational baseline](OPERATIONS.md).

## Validate the renderer

```powershell
cd web
npm ci
npm run check
npm run build
npx playwright install chromium
npm run test:e2e
```

`check` runs TypeScript plus Vitest unit/component tests, all against stubbed
responses. `test:e2e` runs the journeys against the real stack: Playwright
starts `tide serve --demo --auth local --web-root web/dist`, creates a local
account for that run, and drives the built bundle through password sign-in,
browsing, opening a record with its nested lines, creating and editing a
Customer, drafting an Invoice through both lookups and posting it, previewing
and exporting the summary report, a two-tab stale-edit conflict, and a browse
traversed from the keyboard alone. It therefore needs the Python side of TIDE
on the path (`uv sync --extra api --extra report`) and a current
`npm run build`. CI runs all of these against one supported Node version
alongside the Python 3.11 framework suite.

Two of the checks exist because jsdom computes no layout, so the unit suite
cannot see either property: `form-density.spec.ts` measures what a field
spends above its control, and `mobile.spec.ts` drives a 375x812 viewport and
asserts that no control leaves the screen **and** that no two overlap. The
second half of that is not redundant. The first attempt at the action bar
brought every button back inside the viewport and printed `Preview Invoice`
across `Next`; a control that is on screen and unclickable is no better than
one that is off it.

## Current boundary and next slice

The executable
[renderer acceptance matrix](RENDERER-ACCEPTANCE.md) now protects the shared
semantic baseline and records deliberate gaps. Multi-column lookup selection
and nested **Save & Select** are covered for scalar references such as
Invoice Customer and collection references such as InvoiceLine Product.
Transactional Invoice master-detail drafts are also covered. Three-way
conflict review now has the same loss-prevention semantics in Textual and
Web. Metadata-driven domain actions now have the same service-mediated,
ETag/idempotency-protected semantics in all three renderers. Record and summary
report preview plus controlled CSV/HTML/PDF export close the recorded renderer
parity gaps, and summary parameters are now collected in the browser from the
manifest's parameter metadata, the same strings-in contract the terminal
prompt uses. The first production browser identity slice is now implemented
with code/PKCE login, server-held token refresh, opaque cookies, CSRF, session
restore, and local logout, with shared multi-worker session storage and
trusted-proxy deployment covered on the operations side. Provider-wide logout
and reviewed session-key rotation remain separately reviewed concerns.
