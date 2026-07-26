# Qt GUI Prototype

Status: **interactive server-mode browse, master-detail editing, actions, and
record-report preview/export**.

TIDE now includes a small PySide6 desktop adapter that proves the normalized
application model and secured remote-client boundary can drive a native GUI.
It supports an incrementally loaded browse list, metadata-defined search and
named filters, global header sorting, read-only record detail, Customer/Product
create and update, and complete Invoice draft editing with Customer/Product
lookups plus authorized nested creation, three-way stale-edit review, and the
Post Invoice domain action. An authorized opened Invoice can also generate a
temporary PDF and open it in the system viewer. Desktop sign-in remains later
work.

## Security and architecture

The Qt process compiles the local application metadata, authenticates to the
TIDE server, and validates the returned session/application contract. It does
not receive a database URL or import a SQL Server driver:

```text
PySide6 QTableView + incremental TIDE table model
      |
QtBrowseController
      |
TideApiClient + bearer token
      |
FastAPI -> TIDE services -> repository / SQL Server
```

The bearer token grants only the server-assigned principal and roles. The GUI
selects an accessible compiled browse view from the session capabilities, but
the server still reauthorizes every record request.

Report generation follows the same boundary. FastAPI authorizes the report,
loads and formats its data, and returns an immutable renderer-neutral
`ReportDocument`. Qt receives no raw reporting query or database credential; it
passes that document to the shared controlled PDF renderer in a worker.

## Try it on Windows

Open the first terminal and start the isolated tutorial server:

```bat
start.bat api-demo
```

Keep it running and copy the printed development token. In a second terminal:

```bat
start.bat gui
```

Paste the token into the hidden prompt. `uv` installs the optional GUI packages
when needed and opens the default `sales.Invoice.browse` view. The desktop
process uses `http://127.0.0.1:8000`; plain HTTP is allowed only for loopback
development. The first batch uses the view's configured size (25 records in the
Invoicing presentation defaults). Scroll to the end of the loaded records and
the next secured cursor batch is fetched in the background and appended.

Type in **Search Number** to start a new server query after a short typing
pause. Choose **Draft invoices** or **High-value invoices** from the filter
list, and click a supported column heading to sort the complete result
ascending; click the same heading again for descending order. **Clear** restores
the default query. These operations reset the cursor sequence rather than
sorting or filtering only the rows already loaded.

Drag a column heading to personalize its order, or drag a divider to change its
width. **Best Fit** recalculates all widths from the currently loaded rows;
**Reset Layout** restores the compiled metadata order and default fitted
widths. Qt stores the personal layout locally under the application, view, and
authenticated principal. It contains field names, order, and widths only and
does not modify YAML, access the database, or affect other users and renderers.

Select an Invoice and press **Open**, double-click it, or press **Enter**. One
compiled form is used for viewing and editing. Writable controls, Save, and
Post are enabled only when metadata, workflow state, and session capabilities
permit them; a posted/read-only Invoice opens the same layout with those
controls disabled or hidden. The Invoice YAML explicitly defines one compact
two-column header through `settings.compact_groups`, shared with Textual and
followed by the Lines section.

**Previous** and **Next** appear at the bottom left of an existing-record form.
They follow the current browse query and its complete server-side
search/filter/sort order, select the adjacent row in the list, and open it in
the same form layout. At the last loaded row, **Next** transparently requests
the next opaque cursor batch before opening its first record. The current form
stays open if that request fails. Navigation is disabled at the first/last
record, is not shown for a New form, and refuses to discard a changed draft:
save or cancel local edits before moving to another record.

For an editable draft Invoice, the Customer reference is not a raw identifier:
**Select…** or F4 opens the
compiled `crm.Customer.lookup` with Code, Name, and Email columns. Typing
debounces bounded API searches across all three declared fields. Double-click a
row or use **Select** to ask the server to apply that identity to the Invoice
draft.

When the authenticated session permits Customer creation, **New** or Ctrl+N
opens `crm.Customer.edit`. Its button reads **Save & Select**: Customer creation
commits independently through FastAPI, then the resulting identity is applied
through the same secured reference-selection operation to the preserved,
still-unsaved Invoice. Server authorization and validation remain authoritative
at both boundaries.

The Lines section follows `sales.InvoiceLine.inline_edit`. The line table takes
the flexible height; the Line Details editor follows the metadata rows below
it, with column-first keyboard order; **Add line**, **Apply line**, and
**Remove line** remain at bottom left. Existing line identities are preserved,
new line numbers advance conventionally, and calculated fields are never sent
as writable input.

Product uses `catalog.Product.lookup`. Selecting one calls the shared
reference-selection operation for `sales.InvoiceLine`, so Description and Unit
Price come from the secured server result and remain editable historical
snapshots. Authorized **New** / Ctrl+N opens `catalog.Product.edit` with
**Save & Select**. Applying a line previews its Total and the Invoice Total
using the framework expression engine. Final **Save** automatically applies the
selected line, removes protected/computed/inverse fields from the nested
payload, and updates the Invoice once with its observed ETag. The server still
normalizes, validates, recomputes, authorizes, and commits transactionally.

If that ETag is stale, Qt does not overwrite the other change or stop at a
generic error. It securely reloads the current record and opens a three-way
table with **Original**, **Current**, and **Your draft** values. Changes made
only in the local draft are retained automatically; changes made only on the
server use the current value; identical edits need no decision. Every genuine
overlap requires an explicit **Use Current** or **Use Mine** choice.

**Continue Editing** leaves the stale draft open without saving. **Reload
Current** discards it and opens the current version. **Apply Resolution**
copies the completed plan into a fresh form carrying the latest ETag; it does
not write from the review dialog. Review that form and press **Save** to run
ordinary current authorization, workflow immutability, normalization, and
validation. If a concurrent workflow transition has locked a field, the
dialog names it and does not carry that draft value forward. For this first
safe master-detail contract, the line collection is compared and resolved as
one field rather than attempting an ambiguous row-level merge.

The form action bar also follows `actions: [cancel, save, post]` from
`sales.Invoice.edit`. Qt creates **Post invoice** only when the authenticated
session advertises that action. Its enabled state reevaluates the compiled
`enabled_when` expression against the current local line draft, so an empty or
already-posted Invoice cannot invoke it from the GUI.
An `invoice_poster` principal, which has read/post but no update permission,
opens the same record through an **Open** button: fields and lines stay
read-only, Save is hidden, and only the authorized action is interactive.

Clicking **Post invoice** applies the selected line, validates the local editor
values, and saves any draft changes first. The action then uses the saved
record's returned ETag—not the stale form ETag—and a new `qt:` idempotency key
through the ordinary FastAPI action route. Both calls run on the form worker,
and the server rechecks permission, action conditions, expected version,
validation, and transaction rules. On success the form closes and the browse
row refreshes to Posted. If the draft save succeeds but posting fails, Qt
reopens that saved ETag-backed form with the failure message so the user can
correct it and try again.

Inside an opened Invoice, choose **Preview PDF**. The button exists only when
the authenticated session advertises the REST-exposed `sales.invoice` report.
The server builds the secured `ReportDocument` in the background, the shared
ReportLab renderer writes a uniquely named PDF under the operating system's
temporary directory, and Qt asks the configured system PDF viewer to open it.
Temporary previews are scoped to the GUI session instead of accumulating in
`output/reports`. Save local changes before previewing so the PDF cannot
silently represent an older stored version.

To exercise the editable flat-form slice, open either workspace instead:

```bat
start.bat gui-products
start.bat gui-customers
```

Use **New**, or select a row and use **Open**. Product and Customer form groups
and row placement come from their compiled `*.edit` views. Tab and Enter move
through the left field column before the right. Required fields, code regex
masks, Product's `0.00` Decimal mask, Boolean defaults, writable capabilities,
and read-only styling are reflected locally. Save runs outside the GUI thread,
calls only the authenticated FastAPI create/update route, carries an observed
ETag when present, and refreshes the secured list. Server-side permissions,
row policies, uniqueness, normalization, and validation remain authoritative.

The equivalent explicit setup is:

```powershell
uv sync --extra api --extra gui --extra report
$env:TIDE_API_TOKEN = "paste-the-development-token"
uv run --extra gui --extra report tide gui applications/invoicing `
  --api-url http://127.0.0.1:8000
```

Use `--view catalog.Product.browse` to open another accessible browse, or
`--page-size 50` to override the incremental fetch batch size. This controls
network batching rather than visible page navigation. Use `--help` to see the
complete launcher contract.

## What this slice demonstrates

- the same compiled browse columns, field labels, date/decimal formats, and
  right-aligned numeric values can drive Qt widgets;
- reference identities are resolved through secured API reads and cached only
  for the current client session;
- a `QTableView`/`QAbstractTableModel` boundary accumulates opaque server cursor
  batches as the user scrolls, without Previous/Next *page* navigation;
- search, named filters, and sortable fields are interpreted from the same
  compiled browse metadata helper used by Textual, then sent through the typed
  FastAPI query endpoint;
- sortable header clicks order the whole secured server result, while
  300-millisecond search debounce avoids a request for every keystroke;
- blocking HTTP batches run on a dedicated Qt worker pool, keep the interface
  responsive, suppress stale query generations, reject repeated cursors, and
  surface a refreshable loading error;
- browse columns start at practical content-based widths; headings are movable,
  dividers are draggable, **Best Fit** sizes all columns to loaded contents, and
  stable field-name layouts survive refreshes and later GUI sessions;
- selected records open through their real primary-key identity into compiled
  form rows and inline collection columns, with no client-side database path;
- opened existing-record forms navigate Previous/Next in the active secured
  list order, cross cursor boundaries in the background, and protect unsaved
  drafts from silent replacement;
- one renderer-neutral layout helper resolves YAML group rows, collection
  placement, tabs, and field visibility for Textual and Qt; Web can consume the
  same semantic contract later;
- flat Product and Customer forms use compiled group/row order, typed metadata
  controls, capability-gated New/Edit actions, background API mutations, and
  refresh through the same cursor-backed list;
- Invoice forms render compiler-approved references as multi-column lookups,
  apply selections through the server-owned draft operation, and support
  authorized nested **Save & Select** creation;
- compiled inline collection metadata drives the InvoiceLine table, field
  layout, actions, masks, Product lookup/default assignments, computed previews,
  and sanitized ETag-protected nested save;
- stale edits use the renderer-neutral three-way conflict comparer, explicit
  per-overlap choices, current workflow locks, and a fresh review-before-save
  form instead of client-side last-write-wins;
- form-domain actions use metadata order, authenticated capabilities,
  draft-aware enablement, save-before-action ETag chaining, and per-attempt
  idempotency keys through the existing action endpoint;
- record-report controls are the intersection of report metadata, REST
  exposure, active entity, and authenticated session capabilities; authorized
  documents become temporary PDFs opened by the system viewer;
- inaccessible views fail closed instead of falling back to local data;
- the presentation/controller contract is testable without installing Qt in
  ordinary CI.

## Deliberately deferred

- summary-report parameters and preview;
- privileged designer publishing of validated application layout defaults;
- OIDC desktop login, access-token refresh, and secure token storage;
- native application packaging, signing, and installers;
- a stable renderer-comparison contract across Textual, Qt, and web.

PySide6 is the official Qt for Python binding. TIDE's optional `gui` dependency
installs only `PySide6-Essentials`, which contains the Core and Widgets modules
used by this prototype rather than the much larger add-on module set. TIDE
itself remains MIT-licensed. Anyone distributing a Qt-based application should
separately review the official
[Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html) and
choose the applicable LGPL, GPL, or commercial terms.

See [Architecture](ARCHITECTURE.md), [Security](SECURITY.md), and the
[REST API client tutorial](API-CLIENT-TUTORIAL.md) for the shared server and
client contracts used by this prototype.
