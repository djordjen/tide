# REST API and MCP

## Shared application boundary

REST and MCP are adapters over application services. They do not expose raw
SQLAlchemy sessions or construct separate authorization and validation paths.

```text
REST route / MCP tool
        -> authenticated RequestContext
        -> query, record, or action service
        -> permissions and validation
        -> RecordSession/unit of work
        -> persistence
```

## Explicit interface exposure

Machine interfaces are opt-in. `expose` is preferred over `web-endpoint`
because it states the security-sensitive intent and distinguishes REST from web
forms and MCP.

```yaml
entity: crm.Person

expose:
  tui: true

  rest:
    path: people
    operations: [list, get, create, update, delete]

  mcp:
    resources: [schema, record]
    tools: [search, create, update]
```

Exposure controls capability existence; authorization controls whether the
current principal may use it. REST exposure never implies MCP exposure.

## Generated REST API

The model compiler provides typed request, response, filter, pagination, and
action schemas. The FastAPI adapter registers routes at startup without writing
generated Python files:

```text
GET    /api/v1/people
GET    /api/v1/people/{id}
POST   /api/v1/people
PATCH  /api/v1/people/{id}
DELETE /api/v1/people/{id}
GET    /api/v1/people/{id}/_audit
```

The adapter can also publish OpenAPI and interactive documentation; see
below for when it does. Input models
exclude computed and non-writable fields; output serialization understands
protected values.

Filtering, sorting, expansion, and pagination are model-controlled and
allow-listed. Clients cannot submit arbitrary SQL. API contracts should expose
stable resource representations rather than leaking persistence internals.

### Current application server

The implemented FastAPI adapter registers secured list/get/create/update/delete and
exposed domain-action routes from the immutable `ApplicationModel`. It reuses
the same Pydantic record/page contracts as the standalone OpenAPI exporter and
adds writable request projections at server startup:

```bash
tide api export-openapi applications/invoicing
tide api export-openapi applications/invoicing --output openapi.json
tide serve applications/invoicing --demo
```

Only declared operations appear. Record history additionally requires REST
`get` exposure plus an explicit `permissions.audit` entry; the current
principal must hold that permission on every request. `rest: true` remains a safe shorthand for
`list` and `get`; create, update, and delete require mapping form. An action
route exists only when that action declares `expose.rest: true`. If `path` is
omitted, the default is a namespaced,
kebab-case resource path such as `crm/person`. The standalone `export-openapi`
command intentionally remains the dependency-free read-only contract preview;
the running server's `/openapi.json` includes its mutation schemas and routes.

GET list routes publish the implemented page size and opaque cursor parameters.
The runtime additionally publishes typed read-only `POST .../_query` routes for
structured filtering and sorting; the standalone preview remains intentionally
limited to its dependency-free list/get contract.

`tide serve` exposes `/health/live` and `/health/ready` always, and `/docs`,
`/redoc` and `/openapi.json` when the API description is enabled — on by
default for a loopback bind, off otherwise, and set either way with `--docs` or
`--no-docs`. The description names every exposed entity, field and action along
with the `x-tide` runtime configuration, so it follows the same rule as
development authentication: useful on the machine you are building on, not
something a networked deployment publishes because nobody said otherwise. It is
withheld rather than gated behind a token, since a credential is not what makes
publishing the model surface acceptable. `build_fastapi_app` defaults `docs` to
off, so an embedder that never considers the question does not publish one.

Liveness is process-only. Readiness checks persistence
connectivity, mapped-schema and SQL-policy compatibility, plus configured
durable cursor/action state; it returns a bounded HTTP 503 `not_ready` response
without exposing dependency errors. Both operational probes are deliberately
unauthenticated for supervisors and orchestrators. The authenticated
`/api/v1/_tide/session` resource publishes
the wire version, application identity, authentication type, principal
identifier, and only those server-assigned roles, directly exposed operations,
nested-draft operations, readable/writable fields, and exposed actions
available to that principal through this server. A Boolean audit capability
reports only whether the safe record-history route is available and does not
disclose the permission name. It is capability information for rendering and
early feedback, never a replacement for per-request authorization.

The authenticated `GET /api/v1/_tide/presentation` resource is the Web
renderer foundation. It projects the compiler-normalized application
navigation, browse metadata, and safe form editor facts into a versioned,
principal-bound contract:
resource/query paths, identity fields, visible readable columns, labels, types,
alignment and formats, reference targets, search, structured named filters,
sortable fields, fetch size, currently available REST operations, required
state, choices, masks, numeric constraints, and resolved
defaults. Whole views, empty navigation groups, protected fields, and controls
that depend on a protected field are omitted. The projection never returns raw
YAML, database configuration, permission/workflow expressions, or application
Python. Its operation and writable hints are advisory; every request is
authorized again by the ordinary service route.

The same manifest projects authorized REST reports only as safe name, title,
record/summary kind, owning entity, resource path, and supported export
formats. It omits report queries, criteria, bands, expressions, and permission
names. Record and parameterless summary endpoints return an immutable,
renderer-neutral `ReportDocument`; controlled export endpoints rebuild that
document under the same authorization and return CSV, standalone HTML, or PDF:

```text
POST /api/v1/_tide/reports/{report}/exports/{format}
GET  /api/v1/_tide/reports/{report}/records/{id}/exports/{format}
```

PDF delivery requires the optional `report` installation extra. If it is
absent, only that requested format fails closed with HTTP 503; report discovery,
preview documents, CSV, and HTML remain available.

Every completed framework HTTP response includes an effective
`X-Correlation-ID`. A caller may
supply a bounded log-safe identifier; missing, malformed, or oversized values
are replaced with a server UUID. REST uses that identifier in its
`RequestContext`, and hosted MCP carries it from each Streamable HTTP exchange
into service-layer mutations, action lifecycle events, and CRUD audit. TIDE's
structured request log records the stable operation and status but deliberately
omits bearer credentials, bodies, query values, raw paths, cursors, prompts, and
exception messages. See [Operational baseline](OPERATIONS.md#logging-and-audit).

REST and hosted MCP also share the server's request-body cap. The default is
1,048,576 bytes with a 30-second receive deadline; the active values appear in
OpenAPI as `x-tide.max_request_body_bytes` and
`x-tide.request_body_timeout_seconds`. Oversized declared or chunked bodies
receive a correlated HTTP 413 `request_too_large`; slow/incomplete bodies
receive HTTP 408 `request_timeout`. Both happen before authentication or payload
parsing. These are transport safeguards, not substitutes for the smaller field,
collection, page, and operation-specific bounds enforced by application
services. See
[HTTP resource limits](OPERATIONS.md#http-resource-limits).

The bounded history response contains two explicit event variants. `action`
events retain action name, lifecycle outcome, start/finish timestamps, and safe
error code. `record` events identify a successful `create`, `update`, or
`delete`, its mutation source (`user`, `action`, or `system`), and changed
fields. Each field change says whether before/after sides existed and whether
values are `recorded`, `field_only`, or `redacted`. Decimal/date/datetime values
use the same exact typed wire rules as ordinary records. Protected, unconfigured,
collection, and oversized values are never smuggled into the generic payload.

The development identity adapter is deliberately local-only:
it reads one opaque token from a named environment variable, maps that token to
a principal and roles fixed at server startup, and binds only to a loopback
interface. HTTP clients cannot select a role through headers or request data.
Missing, incorrect, and short tokens fail closed.

The default Web identity adapter reads users from an explicitly initialized,
application-bound TIDE SQLite file that is separate from the application
database. It verifies salted password hashes, maps only stored roles that still
exist in the compiled application, and issues an opaque HTTP-only session with
per-session CSRF proof. Create the first user, then start the adapter:

```bash
tide auth create-user applications/invoicing \
  --store .tide/local-auth.sqlite3 \
  --username admin --role sales_clerk --role auditor

tide serve applications/invoicing --database-env \
  --auth local --local-auth-store .tide/local-auth.sqlite3 \
  --web-root web/dist
```

The optional OIDC identity adapter validates access tokens issued by an OpenID
Provider. Install it separately only when a deployment chooses that integration:

```bash
uv sync --extra api --extra auth
```

At startup, TIDE retrieves the issuer's standard discovery document over
HTTPS, requires its `issuer` to exactly match configuration, and obtains the
HTTPS JWKS location. Each request then requires a key ID, an accepted token
type, a configured asymmetric signing algorithm, and valid `iss`, `aud`,
`exp`, and non-empty `sub` claims. The default algorithm is `RS256`; the
accepted `typ` values are `at+jwt` and `JWT`. Clock-skew tolerance defaults to
30 seconds. Symmetric algorithms are deliberately not accepted.

External roles never become TIDE roles by name or claim alone. Each permitted
mapping is explicit, and its target must exist in the compiled application:

```bash
tide serve applications/invoicing --database-env \
  --auth oidc \
  --oidc-issuer https://identity.example.com/tenant \
  --oidc-audience tide-api \
  --oidc-role-claim realm_access.roles \
  --oidc-role-map external-sales=sales_clerk \
  --oidc-role-map external-audit=auditor \
  --host 0.0.0.0 --port 8443 \
  --ssl-certfile deployment/server-chain.pem \
  --ssl-keyfile deployment/server-key.pem
```

Role claims must be arrays of strings. Unmapped roles are ignored, while a
malformed claim fails authentication. Repeat `--oidc-algorithm` or
`--oidc-token-type` only where the identity provider's reviewed contract
requires additional values. An encrypted key password is read without display
through `--ssl-keyfile-password-env NAME`.

Development authentication cannot bind outside loopback. Local password and
OIDC authentication may run over plain HTTP only on loopback; any non-loopback
binding requires a certificate and key so Uvicorn terminates TLS directly. A
reverse-proxy trust contract is
not implemented yet, so forwarding headers are not an alternative to these
checks. TUI, automation, REST, and hosted MCP clients obtain an access
token from the chosen provider and send it through the same bearer boundary.
The optional same-origin Web adapter can instead perform Authorization Code
with PKCE, retain access and refresh tokens behind FastAPI, and expose only an
opaque HTTP-only session cookie plus CSRF proof to React. See
[Web authentication](WEB-AUTHENTICATION.md); its cookie is not an MCP
credential.

Before deploying Web login, `tide auth check-oidc` applies the same
provider-capability validation as server startup and can emit secret-free JSON
for CI. It verifies discovery compatibility and application role-map targets;
it does not claim that a registered client, issued token claims, or refresh
issuance work until an operator completes the documented interactive sign-in.

Response schemas keep every model field present and nullable so a protected
value can be represented as JSON null. Optional `_tide.protected_fields`
metadata distinguishes protection from a genuine null. Decimal values are JSON
strings to preserve exact precision; dates and datetimes use standard OpenAPI
formats. This is the experimental v0.1 contract, not yet a stable 1.0
wire-compatibility promise. The standalone exporter remains useful in CI even
when no FastAPI dependency is installed.

The HTTP runtime serializes protected values as `null` plus
`_tide.protected_fields`, returns decimals as exact strings, forwards opaque
principal-bound cursors, and maps authorization/not-found/query failures to a
stable error envelope. Every CRUD route calls `RecordsService`, and domain
actions call `ActionService`; the adapter never uses a repository or SQLAlchemy
connection directly.

Because CRUD auditing lives inside `RecordsService`, local Textual, remote
Textual, REST, and future GUI/MCP callers produce the same record events. The
transport cannot disable or forge them. Action-triggered record changes share
the request correlation identifier with the enclosing action lifecycle event.

Queries use deterministic ordering and opaque continuation cursors. A primary
key tie-breaker is added when necessary. Expansion and page sizes are bounded
by the core, not only by adapter configuration. See
[Query and concurrency](QUERY-AND-CONCURRENCY.md).

List adapters map the service page to an envelope such as
`{"records": [...], "next_cursor": "..."}`. A missing or null
`next_cursor` means the result is complete. The continuation token is a bearer
value and should not be logged; clients must repeat the same filter, sort, and
page size when presenting it.

When an entity has an integer concurrency token, generated REST responses
publish an ETag and update/delete requests require the version the caller
observed. A missing `If-Match` returns 428 and a stale value returns 412 before
the deletion can commit. Non-versioned legacy entities remain supported without
inventing a column in an externally owned schema.

DELETE is independently deny-by-default at both layers: the entity must declare
`expose.rest.operations: [delete]` and a non-null `permissions.delete` grant.
The service applies delete row policies again inside the repository mutation.
Successful deletion returns 204 with no body. A metadata `on_delete: restrict`
relationship returns the stable `delete_restricted` error with HTTP 409;
`cascade` and `set_null` execute in the same transaction. Cascaded dependants
follow relationship ownership and do not require a separate client-visible
delete route or child delete grant.

Create request models include only normal writable fields. System-generated,
action-owned, read-only, and computed fields are rejected before they reach a
service. `PATCH` models make every writable field optional and apply only fields
actually present in the JSON body; omitted and protected values are never
interpreted as null or overwritten. Writable cascaded collections use typed
nested records; an existing child's identity is optional so the same collection
may contain updates and new rows.

For an entity with a concurrency token, `GET`, create, update, and action
responses publish a strong integer ETag such as `"3"`. `PATCH` and targeted
actions require the corresponding `If-Match` value. Missing preconditions
return `428`; stale observations return `412`; the repository still performs
the atomic version check to close the race after authorization.

### Remote client foundation

Install the optional client adapter and verify a running server with the same
compiled application:

```bash
uv sync --extra client
tide api check-server applications/invoicing --url http://127.0.0.1:8000
```

The command reads its bearer token from `TIDE_API_TOKEN` by default. The
reusable synchronous `TideApiClient` first authenticates against the session
resource and refuses application name/version, schema-version, or wire-version
mismatches. It converts wire decimals, dates, datetimes, nested records, and
protected-null metadata back into TIDE values; it carries opaque cursors and
strong ETags without interpreting them. Server error envelopes become stable
client exceptions without copying credentials into exception text.
After `connect()`, `load_presentation()` retrieves and validates the manifest
against that same application and principal. This lets a remote renderer build
its safe application shell without reading the server's source tree.

Plain HTTP is accepted only for `localhost`, `127.0.0.1`, and `::1`; remote
origins require HTTPS so bearer credentials cannot be sent over an
unencrypted network. Redirects are not followed. This is the transport
used by record/action facades consumed by Textual. Run `tide run
applications/invoicing --api-url http://127.0.0.1:8000`; the TUI compiles
presentation metadata locally but performs browse, structured filter/sort,
lookup selection, create/update, and actions through HTTP. A remote client
reuses this boundary for browse queries, forms, reference selection, and nested
**Save & Select** creation. Its InvoiceLine editor sends sanitized nested
collection input through the ordinary Invoice create/update contract; no
renderer-only mutation route is introduced. A `stale_version` response causes
a renderer to issue an ordinary authorized `get`, compare Original/Current/Draft through
the shared conflict contract, and reopen a resolved form with the new ETag.
The review dialog does not receive a bypass route and does not write data.
Form actions call the existing REST action route as well. If a local draft
changed, the renderer first performs the ordinary create/update request and uses
its returned ETag as the action `If-Match`; each invocation receives a new
idempotency key. Session capabilities control presentation only—the server
still enforces action exposure, permission, conditions, version, idempotency,
validation, audit, and transaction behavior.
Authorized record reports are
built through
`ReportService` at `GET /api/v1/_tide/reports/{report}/records/{identity}` and
authorized summary reports at `POST /api/v1/_tide/reports/{report}` with a
parameter object validated against the compiled definitions. Both return a
versioned renderer-neutral document.
CSV, HTML, and PDF remain client renderers, so report data access and
permissions stay server-side without forcing a particular presentation
technology.

Structured filtering and sorting use `POST /api/v1/{resource}/_query` with a
typed, read-only query body. This avoids putting search values into access-log
URLs while preserving the same allow-listed field/operator/type validation,
row policies, protected projections, deterministic ordering, bounded page
size, and principal-bound cursors as local service calls.

## Domain actions

First-class actions map predictably to REST:

```text
POST /api/v1/invoices/{id}/actions/post
POST /api/v1/orders/{id}/actions/cancel
```

The same action may appear as a TUI shortcut, web button, MCP tool, or report
command. Its handler, permission, validation, confirmation semantics, and audit
event remain centralized.

An exposed idempotent action additionally requires `Idempotency-Key`. Repeating
the same principal/action/target/payload key reauthorizes and returns the
current secured result; reusing a key for a different request or retrying an
uncertain failed execution fails closed through `ActionService`.

## Developer MCP server

The first local developer MCP is implemented as a read/propose-only stdio
server for AI-assisted development:

```bash
uv sync --extra mcp
tide mcp dev applications/invoicing
```

It intentionally has a different server, transport, identity and capability
surface from runtime MCP. Its resources are project-oriented:

```text
tide://developer/project
tide://developer/application
tide://developer/model
tide://developer/entities/crm.Person
tide://developer/views/sales.Invoice.edit
```

Its implemented tools are:

```text
tide_validate_project
tide_list_entities
tide_describe_entity
tide_get_resolved_view
tide_preview_openapi
tide_propose_application
tide_preview_application
```

`tide_propose_application` accepts discriminated logical operations for an
application, entities/fields/relationships, roles, safe state-transition
workflows, and record/PDF reports. It returns a deterministic proposal ID and
semantic diagnostics with `approval_required: true` and
`writes_performed: false`.

`tide_preview_application` deterministically renders the same plan into a
temporary new-application tree, invokes the normal compiler and bounded static
contract checks, then runs fixed generated transition/sequence templates only
against fresh in-memory services. It exercises authorization denial, CRUD,
idempotent actions, secured report documents, HTML and optional PDF; no caller
code/command runs and no application database is opened. The tree is deleted
before exact artifact contents/hashes, a unified diff, relative diagnostics,
and proposal/base/candidate fingerprints are returned. The result distinguishes
ephemeral materialization/runtime checks from source or database mutation with
`workspace_writes_performed: false`, `candidate_persisted: false`, and
`temporary_candidate_deleted: true`, plus explicit code-execution, external-
command, database-access and in-memory-check flags.

There are no caller-selected paths, arbitrary Python, apply, workspace-write,
external-test-execution, or shell tools in developer MCP. The separate local
`tide app preview` and interactive `tide app apply` commands now bind a verified
new-application candidate to the actual absent destination, publish it
atomically, and write an approval/artifact receipt. See
[AI-assisted application generation](AI-APPLICATION-GENERATION.md). MCP-side
apply remains disabled until a host-level human-approval contract exists.

## Runtime MCP server

Runtime MCP lets an authorized AI use a deployed application through the same
security and application-service boundary as REST, TUI, and future renderers.
The developer opts each entity capability in independently:

```yaml
expose:
  mcp:
    resources: [schema, record, audit]
    tools: [search, create, update, delete]
```

The Boolean shorthand `mcp: true` deliberately remains read-only
schema/record/search access. A domain action becomes a tool only when its own
metadata declares `expose.mcp: true`; general CRUD exposure does not infer
action exposure, and action exposure does not infer CRUD.

Install the stable v1 SDK line and mount the endpoint beside REST:

```bash
uv sync --extra api --extra mcp
tide serve applications/invoicing --demo --role sales_clerk --mcp
```

The local endpoint is `http://127.0.0.1:8000/mcp` and uses the same development
token as REST. `start.bat mcp-demo` performs both steps for the invoicing
example. An MCP Inspector or other Streamable HTTP client supplies that token
as a Bearer credential. The server uses stateless Streamable HTTP with JSON
responses and publishes OAuth Protected Resource Metadata at
`/.well-known/oauth-protected-resource/mcp`.

Generated names and URIs are stable transformations of application and entity
identifiers:

```text
tide://runtime/tide_invoicing/entities/catalog.Product/schema
tide://runtime/tide_invoicing/entities/catalog.Product/records/{identity}
tide://runtime/tide_invoicing/entities/catalog.Product/records/{identity}/audit
search_catalog_product
create_catalog_product
update_catalog_product
delete_catalog_product
post_sales_invoice
```

Schema content is rebuilt for the authenticated principal and omits fields the
principal may not read. Record resources normalize the identity to the target
primary-key type, call `RecordsService.get()`, and preserve exact decimals,
dates, nested records, and structured protected-field metadata. Search tools
accept the same typed field/operator/value filters, ordered sort fields,
bounded limit, and opaque continuation cursor as REST. Cursors remain bound to
the principal and effective permissions. Every invocation creates a
`RequestContext` with `Channel.MCP` and reauthorizes entity, row, relationship,
field, filter, and sort access through `RecordsService`.
When hosted beside REST, the context also inherits the validated or generated
HTTP `X-Correlation-ID`, allowing an operator to connect transport telemetry to
the resulting safe audit history without exposing the MCP request body.

Create and update tools use strict entity-specific Pydantic inputs generated
from the same normal writable fields as REST. Unknown, computed, read-only,
system-owned, and action-owned fields are rejected at the protocol boundary;
defaults, reference checks, expressions, validation, row policies, uniqueness,
and audit still run in `RecordsService`. Successful mutation results contain
the exact secured wire record, identity, operation, and request correlation ID.
Delete returns the same structured result without a record body.

Versioned update, delete, and action calls require `expected_version` from a
record the caller previously observed; missing or stale observations fail
closed. An idempotent action additionally requires `idempotency_key`. Repeating
the same key/principal/action/target/payload reauthorizes and safely replays the
current secured result, while conflicting reuse is rejected. Actions execute
only through `ActionService`, including their enabled condition, handler,
validation, concurrency, idempotency, record write, and correlated audit
lifecycle.

Audit resources are separately opt-in and require `permissions.audit` at read
time. They return bounded safe action and CRUD history through
`AuditHistoryService`, which rechecks protected field access. Neither the MCP
client nor its tool inputs receive a repository, SQLAlchemy session, database
URL, credential, arbitrary SQL operation, or project-editing capability.

For OIDC hosting, enable both extras and use the production identity/TLS
configuration documented above. A non-loopback MCP bind additionally requires
the canonical public resource URI because a wildcard listener is not an OAuth
resource identifier:

```bash
tide serve applications/invoicing --database-env \
  --auth oidc \
  --oidc-issuer https://identity.example.com/tenant \
  --oidc-audience tide-mcp \
  --oidc-role-map external-sales=sales_clerk \
  --host 0.0.0.0 --port 8443 \
  --ssl-certfile deployment/server-chain.pem \
  --ssl-keyfile deployment/server-key.pem \
  --mcp \
  --mcp-resource-url https://tide.example.com:8443/mcp
```

The configured resource URL drives RFC 9728 metadata and an explicit Host and
Origin allow-list for DNS-rebinding protection. Its path must exactly match
`--mcp-path`; non-loopback resource URLs require HTTPS. The configured OIDC
audience must identify this deployment according to the provider's resource
indicator contract. TIDE remains a resource server: the external provider
performs login, consent, token issuance, and refresh.

Later runtime surfaces may add higher-level query/report capabilities such as:

```text
find_overdue_invoices
render_invoice_report
```

Reports remain outside the current runtime MCP contract. Domain actions are
preferable to generic writes for business transitions because they carry clear
intent, validation, permission, concurrency, idempotency, and audit semantics.

MCP tool input and structured output schemas are derived from the normalized
application model. Protected fields use structured redaction metadata rather
than a display string. Tool visibility is not the sole security boundary; every
call is authorized again by application services.

## Hosting and identity

A hosted application may present:

```text
/api/v1/...    REST
/mcp           MCP Streamable HTTP
/docs          OpenAPI documentation (when enabled)
```

An HTTP MCP server should use standards-compatible authorization and map the
delegated user or service identity into a TIDE `Principal`. A local developer
server may also use stdio, with credentials supplied through its environment
rather than protocol output.

## Web UI

The dedicated Web UI is a presentation adapter over the same normalized model
and service boundary as Textual. It now builds responsive
grouped navigation and virtualized server-mode browse workspaces from the safe
presentation manifest and existing structured REST queries. Search, named
filters, sorting, opaque-cursor loading, exact formatting, authorized reference
display, and personal column layouts do not require raw YAML in the browser.
The manifest also projects safe renderer-neutral form groups, rows, tabs,
inline collection columns, and bounded scalar editor constraints. A selected
record loads through the authenticated generated `GET` route into a stable
detail shell with workflow-aware locked fields and current-query Previous/Next
navigation. Flat Customer and Product forms now create and update through the
ordinary generated routes. The browser preserves Decimal text, sends only
changed fields in `PATCH`, and returns an observed ETag in `If-Match` whenever
the entity defines optimistic concurrency.

Compiler-approved reference editors add a bounded lookup subset to that same
manifest: target resource/query paths, readable display columns, declared
search fields, identity field, allowed operations, and an optional authorized
create form. Web searches through the ordinary structured query routes and
then posts the parent draft plus selected identity to
`/_tide/reference-selection`. Declarative selection assignments, protected
target values, and field-write permission remain server-owned. Nested
**Save & Select** first creates the independent target record through its
generated route and only then applies it to the preserved parent draft.

Authorized inline collections extend that projection with the child identity,
readable columns, writable editor fields, semantic rows, supported nested draft
operations, and YAML Add/Apply/Remove order. They do not create independently
addressable child mutation routes. Web keeps child changes local and submits a
complete filtered collection replacement through the generated parent
create/update route with its normal ETag. The service layer then reauthorizes
the parent and nested fields, normalizes references and decimals, validates,
handles orphan deletion, recalculates stored values, and commits once.

Record responses can include server-evaluated `writable_fields` presentation
hints. They contain no permission or workflow expression and are not an
authorization grant: mutation routes continue to reauthorize row and field
access, normalize values, enforce validation and workflow rules, and apply
optimistic concurrency. Validation failures may add safe `issues` containing a
rule, message, severity, and field names to the stable error envelope. Clients
can place those messages beside controls; non-validation errors retain the
existing compact envelope.

This contract does not make the browser an authorization authority. The Web
client may use capabilities to avoid offering unavailable controls, but actual
reads, edits, actions, and reports still use the generated FastAPI routes and
their service-layer security. A future server-rendered variant may call the
same services in-process where appropriate; neither architecture receives raw
SQL or a database connection string.

For reviewed deployments, the connection screen can discover the optional
same-origin OIDC Authorization Code/PKCE adapter. FastAPI validates the access
token through the existing OIDC-to-`Principal` contract, retains provider
tokens in a bounded process-local session, and gives React only an opaque
HTTP-only cookie and per-session CSRF value. Bearer headers remain authoritative
when supplied and an invalid bearer never falls back to cookie identity.
Remote renderers and hosted MCP continue to use bearer tokens.

See [Web UI](WEB-UI.md) for launch, build, hosting, and validation commands and
[Web authentication](WEB-AUTHENTICATION.md) for provider registration and the
current session boundary.

## Useful commands

```bash
tide api export-openapi
tide api check-server
tide auth check-oidc
tide mcp dev applications/invoicing
tide serve --mcp
```

## References

- [Model Context Protocol documentation](https://modelcontextprotocol.io/)
- [Official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [FastAPI documentation](https://fastapi.tiangolo.com/)
- [OpenID Connect Discovery 1.0](https://openid.net/specs/openid-connect-discovery-1_0.html)
- [JSON Web Token Best Current Practices (RFC 8725)](https://www.rfc-editor.org/rfc/rfc8725)
- [Uvicorn HTTPS settings](https://www.uvicorn.org/settings/#https)
