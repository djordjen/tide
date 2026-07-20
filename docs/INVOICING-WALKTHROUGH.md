# Invoicing Application Walkthrough

The maintained Invoicing application is TIDE Framework's golden reference. It
is a proof of concept, an integration fixture, and a practical example of how
application-owned YAML becomes a secured TUI, REST/OpenAPI contract, runtime
MCP surface, report, and SQL-backed application without putting those concerns
inside the framework runtime.

This walkthrough uses isolated demo data. It does not require SQL Server and
does not write to a database.

## Start the application

From the repository root on Windows:

```powershell
.\start.bat demo
```

The equivalent cross-platform command is:

```bash
uv run --extra tui tide run applications/invoicing --demo --page-size 5
```

The default demo principal receives the most capable application role. Use the
workspace selector at the top left to move between **Invoices**, **Customers**,
and **Products**.

## Browse and find invoices

![Invoice browser with search, filter, sorting, actions, and right-aligned totals](images/tide-invoice-browser.svg)

The Invoice browser demonstrates shared query behavior:

- incremental search by invoice number;
- named **Draft invoices** and **High-value invoices** filters;
- declared sorting and eligible sortable columns;
- right-aligned numeric totals;
- paging, refresh, clear, edit, report preview, and role-dependent history.

The behavior comes from several application documents rather than TUI code:

| Concern | Application-owned source |
| --- | --- |
| Invoice fields, calculations, workflow locks, exposure | [Invoice model](../applications/invoicing/models/sales/invoice.yaml) |
| Browse columns, search, filters, sorting | [Invoice browse view](../applications/invoicing/views/sales/invoice-browse.yaml) |
| Shared numeric/date formatting | [Formats](../applications/invoicing/presentation/formats.yaml) |
| Permissions and row/field policies | [Security policies](../applications/invoicing/security/policies.yaml) |

Select a row and press `Enter`, or choose **Edit**. Press `N` or choose **New**
to begin a new invoice.

## Maintain Customers and Products

Customers and Products are ordinary TIDE entities, not hard-coded lookup
lists. Select their workspace to create or edit them directly. Their model and
view documents are:

- [Customer model](../applications/invoicing/models/crm/customer.yaml),
  [browse view](../applications/invoicing/views/crm/customer-browse.yaml), and
  [edit view](../applications/invoicing/views/crm/customer-edit.yaml);
- [Product model](../applications/invoicing/models/catalog/product.yaml),
  [browse view](../applications/invoicing/views/catalog/product-browse.yaml),
  and [edit view](../applications/invoicing/views/catalog/product-edit.yaml).

Customer and Product codes use regular-expression edit masks. Names, lengths,
required values, uniqueness, and permissions are enforced by shared services,
so a REST or MCP client cannot bypass rules shown by the TUI.

## Enter an invoice

![Invoice editor with header, line table, line editor, and separated action bars](images/tide-invoice-editor.svg)

The form deliberately distinguishes editable values from calculated or
workflow-owned values. New invoices default to today's date. With a date field
focused, `+` and `-` move one day forward or backward. Accepted date input is
`DD.MM.YYYY`, `DD/MM/YYYY`, or ISO `YYYY-MM-DD`.

Keyboard traversal follows business-form convention:

1. top to bottom through the left column;
2. top to bottom through the right column;
3. onward to the next form section.

`Tab` always advances; `Enter` also advances from text, date, and collapsed
selection fields. `Ctrl+S` saves and `Esc` cancels.

The [Invoice edit view](../applications/invoicing/views/sales/invoice-edit.yaml)
defines header placement and section ordering. The
[inline line editor](../applications/invoicing/views/sales/invoice-line-inline-edit.yaml)
places Line Number, Product, and Description in the left column, followed by
Unit Price and Quantity in the right column. The line table consumes the
remaining height, while **Add line**, **Apply line**, and **Remove line** remain
in the lower-left action area.

## Search for a Product

Focus Product in the line editor and press `F4`, Space, or Down.

![Multi-column Product lookup with search and create-new action](images/tide-product-lookup.svg)

The searchable lookup displays the columns declared in the
[Product lookup view](../applications/invoicing/views/catalog/product-lookup.yaml).
Selecting a Product copies its name and current unit price into the line draft.
The price remains editable and is stored on the invoice line as a historical
snapshot; changing the Product later does not rewrite existing invoices.

If an authorized Product does not exist, choose **New** or press `Ctrl+N`.
The nested form preserves the unsaved Invoice. **Save & Select** creates the
Product, returns to the Invoice, selects it, and applies the same defaults.
Customer lookup creation follows the same contract.

Quantity and Unit Price use scale-aware numeric masks. The controls prevent too
many fractional digits and complete fixed decimal places when focus leaves the
field. Line and invoice totals are Decimal computations declared in the
[Invoice Line model](../applications/invoicing/models/sales/invoice_line.yaml)
and [Invoice model](../applications/invoicing/models/sales/invoice.yaml).

## Save and post

Saving validates the header and every line through the application service.
The Invoice remains a Draft until the secured **Post** action runs. Press
`Ctrl+P` or choose **Post**.

Posting is ordinary application-owned Python registered by
[runtime.py](../applications/invoicing/runtime.py), with its implementation in
[actions.py](../applications/invoicing/actions.py). It receives a secured
service context rather than direct UI or database authority. The action:

- requires `sales.invoice.post`;
- is idempotent;
- allocates and records workflow-owned values;
- changes the status to Posted;
- makes declared header and line fields immutable;
- records safe audit information.

The TUI disables unavailable controls, but the security and workflow rules are
still enforced when the same operation arrives through REST or MCP.

## Preview the invoice report

Select a saved invoice and press `V`, or choose **Preview**. TIDE builds the
secured [invoice report](../applications/invoicing/reports/sales/invoice.yaml)
and can export its line table as CSV, standalone HTML, or A4 PDF to
`output/reports/` below the launch directory.

Report access requires `sales.invoice.report`. A role without it does not see
the Preview action and cannot request the report through another interface.

Press `S`, or choose **Summary**, to build the secured
[posted-sales summary](../applications/invoicing/reports/sales/sales-summary.yaml).
It groups posted invoices by Customer and Currency and shows invoice count and
sales total. The same `sales.invoice.report` permission controls this action.
The service refuses to present incomplete aggregates when the report's bounded
source-row limit is exceeded. **Export CSV** writes an Excel-friendly UTF-8
table and neutralizes formula-looking text cells.

## Inspect audit history

Start the auditor demo on Windows:

```powershell
.\start.bat auditor-demo
```

Select an Invoice, Customer, or Product and press `H`, or choose **History**.
The newest-first view includes action and CRUD outcomes, configured safe field
changes, principal, channel, timestamp, correlation identifier, and version.
Protected values and idempotency secrets are never included.

Demo seeding intentionally creates no audit events because it prepares the
repository directly. Create or edit a record, or post an Invoice, before
opening History.

## See the same application through other interfaces

The interfaces differ in interaction style, not in business authority:

| Interface | Start or inspect | Database knowledge |
| --- | --- | --- |
| Local TUI demo | `start.bat demo` | In-memory repository only |
| REST and Swagger UI | `start.bat api-demo`, then open `/docs` | Server only |
| Remote TUI over REST | `start.bat remote` | None |
| Runtime MCP demo | `start.bat mcp-demo` | None |
| OpenAPI document | `uv run tide api export-openapi applications/invoicing` | None |
| Studio | `start.bat studio` | Source model only |

REST, remote TUI, and runtime MCP reuse the same record, action, report,
security, validation, concurrency, idempotency, and audit services. OpenAPI
describes the generated HTTP contract; it grants no access by itself.

## Move from demo data to SQL Server

When you are ready for persistent local testing, follow the
[SQL Server guide](SQL-SERVER.md). On the configured Windows development host,
the normal sequence is:

```powershell
.\start.bat init
.\start.bat seed
.\start.bat check
.\start.bat
```

The database URL belongs to the server process or local TUI deployment, never
to a remote TUI, web client, or MCP client. Externally owned databases instead
use the separate [legacy no-DDL contract](LEGACY-DATABASES.md).

## Continue exploring

- Open [TIDE Studio](DESIGNERS-AND-REPORTING.md) and compare the tree, property
  editor, YAML source, and live view preview with the files linked above.
- Follow [Build Your First TIDE Application](FIRST-APPLICATION.md) to create a
  separate Contacts application.
- Read [REST API and MCP](API-AND-MCP.md) for authentication, ETags,
  idempotency, filtering, and machine-client details.
