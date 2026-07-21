# Qt GUI Prototype

Status: **initial read-only vertical slice**.

TIDE now includes a small PySide6 desktop adapter that proves the normalized
application model and secured remote-client boundary can drive a native GUI.
It supports browsing and read-only record detail; editing, actions, lookup
selection, reports, and desktop sign-in remain later work.

## Security and architecture

The Qt process compiles the local application metadata, authenticates to the
TIDE server, and validates the returned session/application contract. It does
not receive a database URL or import a SQL Server driver:

```text
PySide6 widgets
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
development.

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
  --api-url http://127.0.0.1:8000 --page-size 5
```

Use `--view catalog.Product.browse` to open another accessible browse, or
`--help` to see the complete launcher contract.

## What this slice demonstrates

- the same compiled browse columns, field labels, date/decimal formats, and
  right-aligned numeric values can drive Qt widgets;
- reference identities are resolved through secured API reads and cached only
  for the current client session;
- opaque server cursors support Previous/Next paging;
- browse columns start at practical content-based widths and every divider is
  draggable; double-click a divider to auto-fit it, and manual widths survive
  paging and refreshes for the lifetime of the window;
- selected records open through their real primary-key identity into compiled
  form groups and inline collection columns, with no client-side database path;
- inaccessible views fail closed instead of falling back to local data;
- the presentation/controller contract is testable without installing Qt in
  ordinary CI.

## Deliberately deferred

- create/edit forms, lookup selection, domain actions, and reports;
- background request scheduling and cancellation for larger remote workloads;
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
