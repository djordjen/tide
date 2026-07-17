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
    operations: [list, get, create, update]

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
```

The adapter also publishes OpenAPI and interactive documentation. Input models
exclude computed and non-writable fields; output serialization understands
protected values.

Filtering, sorting, expansion, and pagination are model-controlled and
allow-listed. Clients cannot submit arbitrary SQL. API contracts should expose
stable resource representations rather than leaking persistence internals.

### Current application server

The implemented FastAPI adapter registers secured list/get/create/update and
exposed domain-action routes from the immutable `ApplicationModel`. It reuses
the same Pydantic record/page contracts as the standalone OpenAPI exporter and
adds writable request projections at server startup:

```bash
tide api export-openapi applications/invoicing
tide api export-openapi applications/invoicing --output openapi.json
tide serve applications/invoicing --demo
```

Only declared operations appear. `rest: true` remains a safe shorthand for
`list` and `get`; create and update require mapping form. An action route exists
only when that action declares `expose.rest: true`. Delete is not implemented
yet even if declared. If `path` is omitted, the default is a namespaced,
kebab-case resource path such as `crm/person`. The standalone `export-openapi`
command intentionally remains the dependency-free read-only contract preview;
the running server's `/openapi.json` includes its mutation schemas and routes.

GET list routes publish the implemented page size and opaque cursor parameters.
The runtime additionally publishes typed read-only `POST .../_query` routes for
structured filtering and sorting; the standalone preview remains intentionally
limited to its dependency-free list/get contract.

`tide serve` exposes `/docs`, `/openapi.json`, `/health/live`, and
`/health/ready`. The authenticated `/api/v1/_tide/session` resource publishes
the wire version, application identity, authentication type, principal
identifier, and only those server-assigned roles, directly exposed operations,
nested-draft operations, readable/writable fields, and exposed actions
available to that principal through this server. It is capability information for rendering
and early feedback, never a replacement for per-request authorization.

The development identity adapter is deliberately local-only:
it reads one opaque token from a named environment variable, maps that token to
a principal and roles fixed at server startup, and binds only to a loopback
interface. HTTP clients cannot select a role through headers or request data.
Missing, incorrect, and short tokens fail closed.

The production identity adapter validates access tokens issued by an OpenID
Provider. Install it separately from the API host:

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

Development authentication cannot bind outside loopback. OIDC may run over
plain HTTP only on loopback; any non-loopback binding requires a certificate
and key so Uvicorn terminates TLS directly. A reverse-proxy trust contract is
not implemented yet, so forwarding headers are not an alternative to these
checks. TIDE validates bearer tokens but does not implement an authorization
code login, acquire tokens, or refresh them; TUI, Qt, web, and automation
clients obtain an access token from the chosen provider and send it through the
same API boundary.

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

Queries use deterministic ordering and opaque continuation cursors. A primary
key tie-breaker is added when necessary. Expansion and page sizes are bounded
by the core, not only by adapter configuration. See
[Query and concurrency](QUERY-AND-CONCURRENCY.md).

List adapters map the service page to an envelope such as
`{"records": [...], "next_cursor": "..."}`. A missing or null
`next_cursor` means the result is complete. The continuation token is a bearer
value and should not be logged; clients must repeat the same filter, sort, and
page size when presenting it.

Entities exposed for update or delete carry an integer concurrency token.
Generated REST responses publish an ETag, and mutations require the version the
caller observed. TUI and MCP carry the same expected version through application
services so remote mutations cannot silently overwrite newer work.

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

Plain HTTP is accepted only for `localhost`, `127.0.0.1`, and `::1`; remote
origins require HTTPS so bearer credentials cannot be sent over an
unencrypted network. Redirects are not followed. This is the transport
used by record/action facades consumed by Textual. Run `tide run
applications/invoicing --api-url http://127.0.0.1:8000`; the TUI compiles
presentation metadata locally but performs browse, structured filter/sort,
lookup selection, create/update, and actions through HTTP. Future Qt clients
can reuse the same boundary. Authorized record reports are built through
`ReportService` at `GET /api/v1/_tide/reports/{report}/records/{identity}` and
returned as a versioned renderer-neutral document. HTML and PDF remain client
renderers, so report data access and permissions stay server-side without
forcing a particular presentation technology.

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

Runtime MCP lets an authorized AI use a deployed application. The first
implemented surface is deliberately read-only: entity `schema` and templated
`record` resources plus bounded structured `search` tools. The developer opts
each entity in independently:

```yaml
expose:
  mcp:
    resources: [schema, record]
    tools: [search]
```

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
search_catalog_product
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

Later runtime surfaces may add capabilities such as:

```text
search_people
get_person
create_person
update_person
post_invoice
find_overdue_invoices
```

Create/update/action/report tools and resources are not registered by this
milestone, even when action/report metadata contains `mcp: true`. Domain
actions will be preferable to unrestricted generic writes because they carry
clear intent, validation, permission, concurrency, idempotency, and audit
semantics.
Retryable actions declare idempotency, and adapters may bind an idempotency key
to the principal, action, target, and payload.

MCP tool input and structured output schemas are derived from the normalized
application model. Protected fields use structured redaction metadata rather
than a display string. Tool visibility is not the sole security boundary; every
call is authorized again by application services.

## Hosting and identity

A hosted application may present:

```text
/api/v1/...    REST
/mcp           MCP Streamable HTTP
/docs          OpenAPI documentation
```

An HTTP MCP server should use standards-compatible authorization and map the
delegated user or service identity into a TIDE `Principal`. A local developer
server may also use stdio, with credentials supplied through its environment
rather than protocol output.

## Web UI

A future web UI is a presentation adapter, not a consumer of TIDE's public REST
API by necessity. It may call application services in-process on the server to
preserve `RecordSession`, protected-value, and validation semantics. A separate
browser client can use the generated REST API when that architecture is
appropriate.

## Useful commands

```bash
tide api describe
tide api export-openapi
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
