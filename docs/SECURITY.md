# Security Model

## Unified enforcement

TUI, web, REST, MCP, reports, imports, exports, and background operations use
the same authorization services. Presentation adapters may hide unavailable
commands for usability, but security is enforced again in application
services.

An action permission is independent from the entity update permission. A
principal may be allowed to post an invoice through the reviewed domain action
without receiving unrestricted invoice editing rights. The action service still
requires record read access, evaluates row policy, rechecks its precondition,
and applies field write policies during commit.

Every action must declare a `permission` or explicitly set
`unrestricted: true`. Omission fails compilation and runtime authorization.
`unrestricted` only waives the action-specific permission; entity read access,
row policy, field policy, preconditions, validation, and auditing still apply.

Reports likewise require a declared permission or explicit
`unrestricted: true`. `ReportService` rechecks that access, loads the root
record through `RecordsService`, and consumes only its secured projection.
Requested protected fields, collections, and computed values fail the report
instead of being printed as placeholders or nulls. Reference display values
are resolved through secured target reads and fall back only to an already
authorized raw reference identifier.

## Permission dimensions

TIDE distinguishes:

- entity operations: list, read, create, update, and delete;
- row access: which records a principal may query or modify;
- field access: read and write independently;
- action execution;
- navigation and view access;
- report execution;
- import and export;
- administrative and model-development capabilities.

Roles grant permission identifiers or policies; model fields should reference
permission identifiers rather than embedding particular role names.

## Row security

Row policies become part of the database query. Unauthorized records must not
be loaded and then filtered by a TUI widget or serializer. Query services apply
row policies to lists, lookups, reports, relationships, aggregates, REST, and
MCP consistently.

Collection relationships require source-field read access and target-entity
read access. Target row criteria are part of the child database statement and
supported root aggregate/reference predicates. The service rechecks returned
children before projection; inaccessible target entities remain protected and
are not hydrated by SQL repositories.

## Protected fields

When a user may see a record but not a field, TIDE uses an internal sentinel:

```python
ProtectedValue
```

It is not the string `"Protected Content"`. A TUI or web form may render a
localized placeholder:

```text
Salary: [ Protected Content ]
```

The sentinel ensures that:

- a protected number or date does not become a string;
- the placeholder cannot be saved to the database;
- change tracking does not mark the field dirty;
- different adapters can serialize protection appropriately;
- localized renderers can choose their own display text.

## Stable API shape

REST and MCP should preserve a predictable structure without returning a fake
typed value. One proposed REST representation is:

```json
{
  "id": 42,
  "first_name": "John",
  "last_name": "Smith",
  "salary": null,
  "_tide": {
    "protected_fields": ["salary"]
  }
}
```

The metadata distinguishes a protected field from a genuine database null.
The exact versioned wire contract remains an open decision.

## Read and write independence

A field may have separate read and write permissions. If blind writes are
allowed, PATCH semantics and `RecordSession` change tracking must ensure that
an unchanged protected field is never overwritten. Full-object updates may not
interpret missing, null, or protected values as equivalent.

## Inference prevention

A principal without read access to a field normally cannot:

- filter or sort by it;
- group, total, or aggregate it;
- export or report it;
- receive validation errors containing its value;
- infer it through computed fields;
- query it through MCP;
- use it as an unrestricted lookup display value.

Computed fields inherit restrictive dependency permissions by default. Any
exception must be explicit and testable.

## Exposure versus authorization

Metadata such as `expose.rest` or `expose.mcp` determines whether an interface
exists. It does not grant permission to any user. Both exposure configuration
and runtime authorization must permit an operation.

Exposure is deny-by-default for machine interfaces, and mutation operations
are enabled explicitly.

## Authentication adapters

TIDE's core consumes a `Principal`; authentication belongs to adapters:

- local or SSH-backed login for TUI deployments;
- bearer/OAuth-compatible identity for REST and HTTP MCP;
- service principals for background work;
- delegated user identity for AI agents.

All identities map into the same permission and audit model.

The development FastAPI identity adapter loads a bearer token from server
environment, requires at least 32 characters, and maps it to a principal
configured on the server command line. It is restricted to loopback hosts.
Role headers, query parameters, and request bodies are ignored; possession of a
token cannot be used to request a more privileged role.

The OIDC adapter discovers one exact HTTPS issuer and its HTTPS JWKS endpoint.
It accepts only explicitly configured asymmetric algorithms and token types,
requires a key ID, verifies signature, issuer, audience, expiry and subject,
and applies clock leeway owned by the deployment. External role claims must be
arrays of strings and grant only roles listed in explicit external-to-TIDE
mappings. Unknown external roles are ignored. Key retrieval, discovery, claim,
or signature failures deny authentication. Non-loopback serving additionally
requires direct TLS certificate and key configuration; development identity is
never permitted there.

This is bearer validation, not an interactive login implementation. Access
token issuance, user consent, MFA, refresh, revocation policy, and provider
configuration remain the identity provider's responsibility. Trusted reverse
proxy handling, request limits, structured security logging, and production
process supervision remain separate deployment work.

HTTP mutation schemas contain only normal writable fields. Partial updates use
field presence rather than full-object replacement, so absent and protected
fields are not written. Versioned mutations require a strong `If-Match` ETag
and repeat the version predicate in persistence; targeted idempotent actions
also require `Idempotency-Key`. These transport checks supplement rather than
replace entity, row, field, action, validation, and repository enforcement.

Authenticated clients may retrieve a session capability document for UI
composition and compatibility checks. It contains no database configuration or
bearer credential. Capabilities are advisory snapshots: every later list, get,
mutation, and action request is independently authorized, including row and
field policy evaluation. The reference client refuses plain HTTP except on a
loopback host and does not follow redirects, preventing bearer forwarding to a
different or unencrypted origin.

Structured filter and sort values use authenticated JSON request bodies rather
than query-string URLs, reducing accidental disclosure in ordinary access
logs. Fields, operators, types, page sizes, cursors, row predicates, and
projected values are still validated and authorized by `RecordsService` for
every query.

Report capabilities disclose names only after report-permission evaluation.
Building a report repeats report, entity, row, relationship, and field
authorization through `ReportService`; the server returns formatted
renderer-neutral content rather than raw protected values. Remote clients
validate the report/application identity, table shape, and safe suggested
filename before preview or local export.

Runtime MCP is authenticated at the HTTP boundary by the official SDK's bearer
resource-server middleware. The adapter converts only a successfully validated
TIDE principal into request-local MCP auth context; clients cannot submit a
principal, role, permission, or channel. Each resource read and tool call then
creates a `Channel.MCP` request context and repeats application-service
authorization. Entity exposure controls protocol existence but does not grant
entity, row, relationship, field, filter, or sort access.

The implemented MCP surface is read-only and limited to metadata-declared
schema/record resources and search tools. Schema resources omit fields the
principal cannot read. Record/query results use the same protected-null
metadata as REST; query cursors are opaque and principal-bound. The transport
publishes RFC 9728 protected-resource metadata and uses the canonical MCP
resource URI as a Host/Origin allow-list for DNS-rebinding protection. A
non-loopback resource URI must use HTTPS. Runtime MCP mutation, domain-action,
and report capabilities remain disabled until their separate authorization,
concurrency, idempotency, and audit contracts are implemented.

Developer MCP is a separate local stdio trust boundary. It receives a project
root selected by the process launcher, not a path supplied to individual MCP
tools. Its implemented resources expose compiled, project-relative model
information; its tools validate/inspect, produce structured no-write proposals,
or render a proposal into an isolated temporary candidate. Candidate paths are
framework-derived, confined and collision-checked. The normal compiler parses
fixed TIDE-owned transition and sequence templates statically. Only after that
succeeds may those exact templates be imported and executed against new in-
memory services. The preview checks unauthorized create/action/report denial,
CRUD, action stamps/idempotency, report construction, HTML and optional PDF.
It runs no caller command/test and opens no configured application database.
Counts and relationship depth are bounded. The candidate is deleted before
exact artifacts, hashes, diff, relative diagnostics and contract-check results
are returned.
The proposal schema contains no arbitrary source path, shell command, Python
handler body, or apply operation. Results require approval and distinguish the
ephemeral temporary write from a workspace write/persisted candidate. Generated
timestamp stamps use the server clock and cannot be supplied by the caller.
Temporary/in-memory isolation is not an OS sandbox; custom Python must never be
executed through this path. Result flags disclose fixed-template execution,
in-memory checks, external commands and database access explicitly.
STDIO stdout is reserved for protocol messages. Remote developer hosting and
source application remain disabled pending authenticated workspace isolation,
actual destination/stale-base detection, explicit approval and audit.

Schema v0.1 is single-tenant per deployment. Multi-user does not imply
multi-tenant: tenant identifiers must not be added as an informal row filter.
Multi-tenant support requires an explicit isolation and migration contract.

## Auditing

Implemented action audit records include principal, channel, action, entity,
typed identity, timestamps, correlation identifier, and outcome. They exclude
payloads, protected values, and raw idempotency keys. Permitted change details,
generic CRUD, MCP, report/export audit, retention, and reconciliation remain
future extensions and must preserve the same non-disclosure rule.
