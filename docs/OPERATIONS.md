# Operational Baseline

**Status: Runtime database selection, OIDC bearer validation with direct TLS,
action audit, shared cursor persistence, dependency-aware HTTP health checks,
correlated structured request logging, and bounded HTTP hosting are executable;
the wider production contract remains proposed.**
These requirements should be built alongside persistence rather than added
after machine mutations ship.

## Configuration and secrets

Deployment configuration is typed and layered from explicit configuration
files and environment variables. Production database URLs, signing material,
credentials, and tokens never belong in portable application metadata or CLI
output. Startup reports missing configuration by name without echoing values.

The Textual runtime selects persistence explicitly with `--database-env`. The
option reads a SQLAlchemy URL from the named environment variable, defaulting
to `TIDE_DATABASE_URL` when no name follows the option. `--create-schema` is a
separate, deliberate operation and is accepted only when the application
declares `database.mode: managed`; startup otherwise performs compatibility
validation without DDL.
Managed runtime selection also validates durable TIDE cursor, idempotency, and
audit tables. Legacy deployments never create TIDE objects in the external
database and currently keep those three forms of runtime state in-process.

`tide serve` follows the same database selection and explicit schema-creation
rules. The development bearer adapter may bind only to loopback and is not a
production authentication mechanism. The OIDC adapter validates an exact
HTTPS issuer, audience, signature, expiry, subject, token type, and explicit
external-role mappings. A non-loopback bind requires Uvicorn to terminate HTTPS
from a supplied certificate and key. Private-key passwords may be read from a
named environment variable and are never printed.

This direct-TLS contract does not yet trust reverse-proxy forwarding headers.
Deployments must not remove the TLS check merely because a proxy is present;
forwarded headers are explicitly disabled. Trusted proxy configuration,
token-acquisition flows, request-rate policy, database statement cancellation,
expanded security-event logging, and production process supervision remain
later reviewed work.

## HTTP resource limits

`tide serve` applies the same HTTP boundary to REST and hosted runtime MCP. Its
reviewable defaults are:

| Control | Default | CLI option |
|---|---:|---|
| Maximum request body | 1,048,576 bytes | `--max-request-body-bytes` |
| Request-body receive deadline | 30 seconds | `--request-body-timeout` |
| Concurrent requests | 100 | `--max-concurrent-requests` |
| Idle keep-alive | 5 seconds | `--keep-alive-timeout` |
| Graceful shutdown wait | 30 seconds | `--graceful-shutdown-timeout` |

The body boundary rejects an oversized declared `Content-Length` before
parsing. Bodies without a length, including chunked requests, are read only up
to the same cap. Accepted bodies are buffered once within that bound so FastAPI
and the MCP SDK receive the original bytes. Rejection returns a safe correlated
HTTP 413 `request_too_large` error without authenticating or parsing it and
without logging or echoing the payload. A body that does not arrive within the receive deadline
returns a similarly bounded correlated HTTP 408 `request_timeout`. These active
values are published as `x-tide.max_request_body_bytes` and
`x-tide.request_body_timeout_seconds` in OpenAPI; REST operations with declared
bodies document both responses.

The body deadline safely covers only receipt before authentication/parsing and
database work. The concurrency limit is enforced by Uvicorn before application
work. The keep-alive timeout bounds only idle time between requests; neither is
a business-operation or database-statement deadline. Shutdown attempts to drain
in-flight requests for the configured grace period before the server proceeds.
TIDE does not yet advertise a hard execution timeout because cancelling an
async waiter does not prove that synchronous driver/database work stopped.
Production statement timeouts and cancellation require dialect-specific
certification.

Uvicorn's identifying server header and duplicate access log are disabled.
Forwarded headers remain disabled until a deployment explicitly gains a
reviewed trusted-proxy allowlist; an operator must not infer external TLS from
untrusted forwarding headers.

Runtime MCP is opt-in through `tide serve --mcp` and the separate `mcp` package
extra. It shares the REST process, persistence, bearer validator, and
application services, but has its own Streamable HTTP protocol endpoint. Local
development derives `http://127.0.0.1:<port>/mcp`. Non-loopback deployments
must declare `--mcp-resource-url` as the externally reachable HTTPS URI; its
path must match `--mcp-path`. That URI is security-sensitive configuration: it
is published through Protected Resource Metadata and defines the MCP SDK's
accepted Host and Origin values.

The current MCP transport is stateless and JSON-response based. Operators must
send the bearer credential on every request, must not log credentials or opaque
query cursors, and must configure the identity provider to issue tokens for the
deployment's reviewed audience/resource. Interactive token acquisition remains
client/provider work. Mutation/action audit and shared body/concurrency limits
apply to current write tools; a deployment-specific request-rate policy remains
future work.

`tide mcp dev APPLICATION` is a local stdio development process, not a hosted
production endpoint. The MCP client launches it with a deployment-selected
project root. Standard output contains protocol messages only; diagnostics are
resources/tool results. Candidate preview may create a short-lived operating-
system temporary tree, compile it and run bounded static contract checks; it
may then execute only the candidate's fixed TIDE-owned transition/sequence
templates against fresh in-memory services. This is not an OS sandbox and must
never be extended to custom/caller code. Entity/report/action counts and nested
fixture depth are bounded; optional PDF absence is reported as a skipped check.
The server deletes the tree before returning and never writes the source
workspace, runs external test/shell commands, connects to the application
database, accepts caller-selected paths, or applies its returned diff. Apply
must remain disabled in unattended automation until explicit
approval, destination/stale-base protection and repository audit ship.

`tide run --api-url` is the database-isolated Textual deployment mode. It reads
the bearer credential from `TIDE_API_TOKEN` (or the named `--api-token-env`),
validates the server application/wire contract before opening a screen, and
refuses unencrypted non-loopback origins. It never reads `TIDE_DATABASE_URL` or
loads application runtime handlers on the client; those remain server-owned.
Remote reports are authorized and constructed on the server; the resulting
formatted document may be previewed or exported locally by the client. Client
output directories therefore remain subject to normal workstation filesystem
permissions and retention policy.

## Health and lifecycle

Hosted deployments provide separate liveness and readiness checks. Liveness
at `GET /health/live` only proves the process can respond and never touches a
database. Readiness at `GET /health/ready` verifies repository connectivity,
mapped-schema compatibility, SQL row-policy translation, and any configured
durable cursor and action/audit store schemas. It returns HTTP 200 with
`status: ready`, or HTTP 503 with `status: not_ready`.

Both probe routes are unauthenticated so a container orchestrator or service
supervisor can use them before application identity is available. Their bounded
responses contain only application name/version and readiness state; dependency
exceptions, URLs, credentials, schema object names, and repair advice are not
returned. A process that needs migration is not ready and never attempts an
automatic destructive migration from the probe.

For `database.mode: legacy`, readiness uses reflection-based compatibility
inspection rather than a TIDE schema revision. Mismatched mapped tables,
columns, keys, or types make the service not ready, but their details are never
included in the public response and the probe never attempts to repair or
migrate the externally owned database.

Graceful shutdown stops accepting new work, gives in-flight requests the
configured bounded drain period, and then closes adapters and database pools.
Background actions carry a correlation identifier and service principal just
like interactive work.

## Logging and audit

`tide serve` writes one JSON object per TIDE runtime event and disables
Uvicorn's duplicate HTTP access log. Use `--log-level` with `debug`, `info`,
`warning`, `error`, or `critical` to select the minimum level. A completed
request contains a UTC
timestamp, level, event, channel, correlation identifier, stable OpenAPI
operation (or a bounded framework fallback), method, status, and duration.
Successful requests use `info`, client errors use `warning`, and server errors
use `error`. Readiness failures additionally name only the failed probe and
exception type; the exception message is excluded.

```json
{"timestamp":"2026-07-19T12:30:00.000Z","level":"info","event":"http.request.completed","channel":"rest","correlation_id":"invoice-import:42","operation":"createSalesInvoice","method":"POST","status_code":201,"duration_ms":8.417}
```

HTTP clients may send `X-Correlation-ID` using 1-128 ASCII letters, digits,
periods, underscores, colons, and hyphens. TIDE replaces absent, malformed, or
oversized values with a UUID and returns the effective identifier in the same
response header. REST places it in `RequestContext`; hosted runtime MCP inherits
it from the enclosing Streamable HTTP request. Service-layer CRUD/action audit
therefore carries the same identifier as the transport log.

The formatter has a fixed field allowlist. It never records authorization
headers, credentials, protected values, request/response bodies, query values,
raw URL paths, arbitrary SQL parameters, opaque cursors, MCP prompts, or
exception messages. Audit events remain a separate durable business contract;
structured runtime logs are operational telemetry and are not an audit
substitute. Deployment log collection, access controls, retention, rotation,
and deletion still require an operator policy.

Domain actions now write a durable audit lifecycle when configured with a
SQLAlchemy action store. Started rows make interrupted work visible; terminal
outcomes distinguish success, replay, conflict, and failure. Payloads and raw
idempotency keys are excluded. Retention, purge, reconciliation, and protected
change-detail policies must be configured before production use.

Shared SQL cursor storage keeps only bearer-token hashes but does retain typed
query boundaries, filters, and principal/permission identifiers. Its TTL and
capacity must be configured, expired rows purged, and database/backup access
treated as potentially sensitive. See
[Shared cursor storage](CURSOR-STORAGE.md).

## Database changes and recovery

Every production migration is previewed and reviewed. Rename intent is explicit;
destructive operations require a separate acknowledgement. Deployment guidance
must document forward migration, application rollback compatibility, and what
cannot be reversed automatically.

Before a migration, operators verify a recent restorable backup. Release tests
exercise backup restoration into an isolated database, not merely backup-file
creation. SQLite deployments document safe file-copy conditions; PostgreSQL
deployments use database-native backup and point-in-time capabilities where
configured.

## Minimum production checks

- application and schema versions are visible without exposing secrets;
- startup fails closed on incompatible metadata or database revisions;
- bounded query, export, upload, and report sizes are configured;
- timeouts and cancellation reach database work where possible;
- audit storage, retention, and clock/timezone behavior are explicit;
- an operator can identify a failed request by correlation identifier;
- restore and migration-recovery procedures are rehearsed before release.
