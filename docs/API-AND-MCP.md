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

### Current read-only contract preview

The implemented preview deliberately precedes the FastAPI hosting adapter. It
generates Pydantic record/page models and an OpenAPI 3.1 document directly from
the immutable `ApplicationModel`:

```bash
tide api export-openapi applications/invoicing
tide api export-openapi applications/invoicing --output openapi.json
```

Only an entity's declared `list` and `get` operations appear. `rest: true` is a
safe shorthand for both read operations; mapping form remains explicit. A
declared create, update, delete, or action exposure does not produce a mutation
route in this preview. If `path` is omitted, the default is a namespaced,
kebab-case resource path such as `crm/person`.

List previews currently publish the implemented page size and opaque cursor
parameters. The versioned HTTP syntax for structured filtering and sorting is
still deferred to the machine-interface milestone rather than being guessed by
the preview.

The preview includes bearer-compatible authentication metadata, but it neither
starts an HTTP server nor authenticates a user. A future hosting adapter must
map the identity to `RequestContext` and invoke the same secured services.

Response schemas keep every model field present and nullable so a protected
value can be represented as JSON null. Optional `_tide.protected_fields`
metadata distinguishes protection from a genuine null. Decimal values are JSON
strings to preserve exact precision; dates and datetimes use standard OpenAPI
formats. This is the experimental v0.1 preview contract, not yet a stable 1.0
wire-compatibility promise.

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

## Domain actions

First-class actions map predictably to REST:

```text
POST /api/v1/invoices/{id}/actions/post
POST /api/v1/orders/{id}/actions/cancel
```

The same action may appear as a TUI shortcut, web button, MCP tool, or report
command. Its handler, permission, validation, confirmation semantics, and audit
event remain centralized.

## Developer MCP server

The developer MCP server is an early feature for AI-assisted development. Its
initial surface should be read-only and project-oriented.

Candidate resources:

```text
tide://application
tide://model
tide://entities/crm.Person
tide://views/sales.Invoice.edit
tide://diagnostics
tide://openapi
```

Candidate tools:

```text
tide_model_validate
tide_model_explain
tide_list_entities
tide_describe_entity
tide_get_resolved_view
tide_preview_migration
tide_preview_openapi
tide_run_tests
```

Later write tools should operate through structured designer/model commands and
return a proposed diff. They should not silently rewrite arbitrary files.

## Runtime MCP server

Runtime MCP lets an authorized AI use a deployed application:

```text
search_people
get_person
create_person
update_person
post_invoice
find_overdue_invoices
```

Read-only resources and query tools should precede mutations. Domain actions
are preferable to exposing unrestricted generic writes because they carry
clear intent, validation, permission, and audit semantics.
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
tide mcp dev
tide mcp inspect
tide serve
```

## References

- [Model Context Protocol documentation](https://modelcontextprotocol.io/)
- [Official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [FastAPI documentation](https://fastapi.tiangolo.com/)
