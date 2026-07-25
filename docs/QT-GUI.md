# Qt GUI Prototype

Status: **read-only vertical slice with interactive server-mode browsing**.

TIDE now includes a small PySide6 desktop adapter that proves the normalized
application model and secured remote-client boundary can drive a native GUI.
It supports an incrementally loaded browse list, metadata-defined search and
named filters, global header sorting, and read-only record detail. Editing,
actions, lookup selection, reports, and desktop sign-in remain later work.

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

Select an Invoice and press **View**, double-click it, or press **Enter**. The
detail window follows the compiled `sales.Invoice.edit` structure but remains
read-only: it shows the Invoice, Totals, and Posting groups plus the nested
line-item table. Customer and Product labels are resolved through secured API
reads rather than direct database access.

The equivalent explicit setup is:

```powershell
uv sync --extra api --extra gui
$env:TIDE_API_TOKEN = "paste-the-development-token"
uv run --extra gui tide gui applications/invoicing `
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
  batches as the user scrolls, without Previous/Next page navigation;
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
  form groups and inline collection columns, with no client-side database path;
- inaccessible views fail closed instead of falling back to local data;
- the presentation/controller contract is testable without installing Qt in
  ordinary CI.

## Deliberately deferred

- create/edit forms, lookup selection, domain actions, and reports;
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
