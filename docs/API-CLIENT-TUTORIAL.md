# Call a TIDE Application Through REST

This tutorial runs a complete invoicing workflow through FastAPI. The client
never receives a database URL and never imports a repository. It authenticates,
selects a Customer and Product, creates and updates an Invoice, demonstrates a
stale-write rejection, posts idempotently, reads correlated audit history, and
requests the secured invoice report.

The runnable client is
[`examples/invoicing_api_client.py`](../examples/invoicing_api_client.py). It
uses TIDE's typed `TideApiClient`, which is the same boundary used by the remote
Textual client and is suitable for a future Qt adapter. A generic web or mobile
client can instead generate its own client from the same OpenAPI document.

## 1. Start the isolated API

Install the client and API dependencies once:

```powershell
uv sync --extra api --extra client
```

On Windows, open the repository in a terminal and run:

```powershell
.\start.bat api-demo
```

The shortcut prints an ephemeral development token and starts the server at
`http://127.0.0.1:8000`. Keep that window open. This demo token has the
`sales_clerk` and `auditor` roles so the tutorial can both change invoices and
read their safe audit history. The server remains restricted to this computer,
and all demo changes disappear when it stops.

For a cross-platform launch, first set a development token of at least 32
characters, then run:

```bash
uv run --extra api --extra client tide serve applications/invoicing \
  --demo --role sales_clerk --role auditor --port 8000
```

Do not put a real production token in a script or repository. Development
bearer authentication is loopback-only; reviewed network deployments use the
OIDC and TLS configuration described in
[REST API and MCP](API-AND-MCP.md#production-identity).

## 2. Inspect OpenAPI

Open <http://127.0.0.1:8000/docs>, choose **Authorize**, and paste the printed
token. Swagger UI shows the generated request and response schemas, required
headers, and stable error envelopes.

The same contract can be exported without starting the server:

```powershell
uv run tide api export-openapi applications/invoicing > openapi.json
```

OpenAPI describes the available HTTP contract. The authenticated session
response still determines what the current principal may actually do; OpenAPI
never grants permission.

## 3. Run the client

Open a second terminal in the repository and run:

```powershell
uv run --extra client python examples/invoicing_api_client.py
```

Paste the token when prompted. Input is hidden and the token is not placed in
the process command line or printed by the client. If `TIDE_API_TOKEN` is
already set in that second terminal, the client uses it without prompting.

Representative output is:

```text
Connected to TIDE Invoicing 0.1.0 as development:api (auditor, sales_clerk).
Customer: ADRIA - Adria Consulting
Product: CONS - Consulting hour (85.00)
Validation example: rejected an invalid date with HTTP 422 (invalid_request).
Created INV-2026-000009 with ETag "1".
Updated currency to USD with ETag "2".
Concurrency example: rejected the stale ETag with HTTP 412 (stale_version).
Posted once and replayed safely with ETag "3".
Audit: replayed post event, correlation 7e... .
Report: Invoice (1 line(s)), suggested filename invoice-INV-2026-000009.
Tutorial completed; all writes went through the TIDE API.
```

Invoice numbers and correlation identifiers vary. Re-running the client against
the same demo process creates the next Invoice; restarting `api-demo` restores
the original deterministic data.

## What each client call does

| Python operation | HTTP contract | Important behavior |
| --- | --- | --- |
| `connect()` | `GET /api/v1/_tide/session` | Authenticates and verifies application/schema versions plus principal capabilities. |
| `list_records(...)` | `GET /api/v1/customers` and `/products` | Applies server-side row and field security. |
| `apply_reference_selection(...)` | `POST /api/v1/_tide/reference-selection` | Applies Product-driven Description and Unit Price defaults on the server. |
| `create_record(...)` | `POST /api/v1/invoices` | Runs shared normalization, validation, authorization, numbering, and audit. |
| `update_record(...)` | `PATCH /api/v1/invoices/{id}` | Requires the observed strong `ETag` as `If-Match`. |
| `execute_action(...)` | `POST /api/v1/invoices/{id}/actions/post` | Requires `If-Match` and a caller-generated `Idempotency-Key`. |
| `audit_history(...)` | `GET /api/v1/invoices/{id}/_audit` | Returns only permission-approved, redacted history and correlation IDs. |
| `build_report_for_record(...)` | `GET /api/v1/_tide/reports/sales.invoice/records/{id}` | Authorizes and builds a renderer-neutral report document on the server. |

The client compiles the local application metadata only to decode exact types
and reject a mismatched server contract. It does not use that metadata to
bypass FastAPI: every read, write, action, audit lookup, and report request
still crosses the authenticated service boundary.

## The three failure examples

The script deliberately includes two safe negative requests:

- an invalid date is rejected with HTTP 422 and `invalid_request`; no invalid
  Invoice is stored;
- the ETag from before the successful update is reused and rejected with HTTP
  412 and `stale_version`; it cannot overwrite the newer record.

Permission failures are normally avoided earlier. The session contract lists
the operations, actions, audit access, readable/writable fields, and reports
available to the authenticated principal. The example refuses to begin if its
token lacks Invoice create/update/Post, audit, or report capability. A client
that ignores the capability contract still receives HTTP 403 `forbidden` from
the server. Missing or incorrect bearer tokens receive HTTP 401
`unauthorized`.

This is why a Qt, web, or third-party client can change presentation without
reimplementing business security. UI visibility is a convenience; the same
rules are enforced again in the FastAPI application services.

## Run the contract test

The tutorial itself is exercised against an in-process FastAPI application in
CI, with no network listener or database dependency:

```powershell
uv run pytest tests/test_api_tutorial.py
```

The test checks the documented routes, typed values, ETag progression,
validation and concurrency errors, idempotent replay, audit correlation, and
report result. This keeps the example synchronized with the generated API.

## Troubleshooting

- **Connection refused:** keep the `api-demo` terminal open and use the default
  `http://127.0.0.1:8000` URL.
- **Authentication required:** paste the latest token printed by the currently
  running server. Restarting the shortcut creates a different token.
- **No readable Customer or Product:** use `api-demo`, or initialize and seed
  the persistent application before using a SQL Server-backed API.
- **Audit capability missing:** the server token needs both `sales_clerk` and
  `auditor`; the current `api-demo` shortcut already supplies both.
- **Different application contract:** point `--project` at the application that
  produced the remote API, or connect a generic OpenAPI-generated client.
