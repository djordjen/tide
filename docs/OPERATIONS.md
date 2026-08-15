# Operational Baseline

**Status: Runtime database selection, local password identity, optional OIDC
bearer validation with direct TLS, action audit, shared cursor persistence,
dependency-aware HTTP health checks, correlated structured request logging,
and bounded HTTP hosting are executable; the wider production contract remains
proposed.**
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
production authentication mechanism. Under it the Web renderer opens with no
credential at all: the browser asks the server for a session and is given one,
for the principal and roles `--principal` and `--role` named. Three things
fence that, and none of them is a document. `tide serve` refuses the mode off
loopback; `build_fastapi_app` refuses to attach it to an identity adapter that
declares itself production, which is the fence for callers that never reach the
CLI; and the server answers `403 non_loopback_host` to any request whose `Host`
header names something other than this machine. That last one is what stops DNS
rebinding: an attacker's domain resolving to 127.0.0.1 is same-origin to the
browser, so neither the bind address nor the absent CORS headers see it, and
the name it asked for is the part still carrying the attacker's own. The REST
API is unchanged and still wants its bearer token, so `curl`, Swagger and the
typed client behave exactly as before. Say plainly what that costs: under
`--auth development`, any process on this machine can obtain a session by
asking, whether or not `--web-root` was given, and the bearer token no longer
separates one local process from another. That is within the mode's stated
envelope rather than a surprise inside it -- development authentication was
never a production mechanism, and a shared machine is not where it belongs --
but it is the reason not to reach for `--auth development` because it is the
convenient one. `--auth local` is the mode to put in front of anybody else. The default Web adapter uses a separate,
explicitly initialized TIDE-owned local identity file and never adds users or
password hashes to the application database. The optional OIDC adapter validates an exact
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

A built TIDE Web renderer may be served from `tide serve --web-root DIRECTORY`.
Startup requires an existing directory containing `index.html`; an incomplete
build fails closed. FastAPI registers API, documentation, health, and optional
MCP routes before the static mount. Fingerprinted `/assets/` responses use
immutable one-year caching, while the HTML entry point and API responses remain
uncached. Same-origin static hosting changes no authentication or service
boundary: the browser sends either the local opaque HTTP-only session cookie or
the configured development/provider credential and receives no database
configuration.

## Running more than one process

A managed database carries `tide_browser_session`, `tide_login_failure` and
`tide_server_lease` tables, created by `--create-schema` beside the
query-cursor and action-audit tables. Where they exist, the password and
development authenticators keep their sessions and their failed-login counters
in them, so several `tide serve` processes behind one address agree about who
is signed in and a restart does not sign everybody out. The startup banner
reports `sessions: shared` when this is in force and `sessions: this process`
when it is not, which is the line to read before putting a second process
behind a proxy.

Three things to know before doing that:

* **A legacy database gets none of them.** TIDE may not create a table in a
  database it does not own, so those deployments keep process-local sessions,
  have nowhere to hold a lease, and must run one application process. The
  cookie stamp still applies, so the mistake is at least diagnosable there.
* **`--auth oidc` keeps its single-process constraint** whatever the database
  is. Its sessions hold the provider's access and refresh tokens, and those
  want encryption at rest before they want sharing. Against a managed
  database this is now enforced rather than documented: the server takes a
  lease in `tide_server_lease`, and a second one refuses to start, naming the
  incumbent. The lease is renewed while the server runs and released when it
  stops; a server that is killed rather than stopped blocks a restart for at
  most `--lease-ttl` seconds (120 by default), after which its claim clears
  itself. Nothing has to be deleted by hand.
* **`tide serve` runs one uvicorn process.** It has no `--workers`: uvicorn
  spawns workers only from an import string, and TIDE builds its application
  object from the parsed command line, a compiled model and an open
  repository. Run several `tide serve` processes on different ports behind a
  proxy, or several containers behind a load balancer, and let the shared
  store do the agreeing.

Where sessions stay in the process that issued them, the session cookie
carries an opaque twelve-character stamp naming that process. A request that
reaches a different one is answered `401 session_from_another_server` rather
than a bare 401 -- which is otherwise indistinguishable from an expired
session, and is what makes this misconfiguration read as users being randomly
signed out. A shared deployment emits no stamp, because there a session that
cannot be found has genuinely expired.

Adding these three tables changes the managed schema. Run `--create-schema` once
against an existing managed database, or `tide db diff` to see the proposal
first.

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

Readiness also refuses a deployment whose driver cannot store a declared
decimal precision exactly. SQLite binds `Decimal` values through a float and
holds 15 significant digits; a wider field would be rounded on the way in with
nothing raised, so the process reports not ready instead. `mssql+pyodbc` and
`postgresql+psycopg2` bind decimals directly and are unaffected.

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

The current inspection-only preflight is:

```powershell
uv run tide db diff applications/invoicing --database-env --json
```

It fingerprints the reflected base schema plus safety-classified differences
without performing DDL or rename inference. `--require-clean` makes any
difference fail a CI/deployment check. `tide db revision` can render only its
currently supported operations after the operator supplies the exact two
fingerprints, a non-secret backup/restore evidence reference, and every required
non-additive change key. The script and SHA-256 manifest are review artifacts;
`tide db render-sql` can validate them and produce upgrade/downgrade SQL through
Alembic offline mode without a database connection. Its SQL manifest binds both
source hashes and fingerprints. Migration apply remains unavailable. See
[Schema migrations](MIGRATIONS.md).

Before a migration, operators verify a recent restorable backup. Release tests
exercise backup restoration into an isolated database, not merely backup-file
creation.

### Path-based SQLite

TIDE can take an online, transactionally consistent snapshot of a path-based
SQLite database. The URL is still read only from an environment variable:

```powershell
$env:TIDE_DATABASE_URL = "sqlite+pysqlite:///C:/tide/data/invoicing.db"
uv run tide db backup applications/invoicing --database-env `
  --output C:/tide/backups/invoicing-2026-07-19.db
```

The output path and its adjacent `.manifest.json` must both be absent. TIDE
never overwrites either file. It uses SQLite's online-backup API, performs
`PRAGMA integrity_check`, validates the compiled application schema and, for a
managed database, validates TIDE's cursor/action/audit tables. The manifest
binds the application and metadata versions, database mode, file name, byte
size, creation time, and SHA-256 digest without recording the source path or
connection URL.

Verify the retained artifact again before using it:

```powershell
uv run tide db verify-backup applications/invoicing `
  C:/tide/backups/invoicing-2026-07-19.db
```

Verification detects accidental byte or manifest changes; the manifest is not
a digital signature. Store backups and manifests together in access-controlled
storage and apply separate signing/immutability controls when the threat model
requires them. Backups include business data and TIDE-owned audit/runtime state
and must be protected like the live database.

A restore remains an operator action, not an automatic TIDE overwrite. Restore
the backup under a new path first, point a temporary `TIDE_DATABASE_URL` at it,
run `tide db check`, and exercise representative secured reads and actions. For
an actual replacement, stop every writer, retain the failed database as
evidence, install the already-verified file through the deployment's controlled
file procedure, rerun `tide db check`, and only then admit traffic.

In-memory SQLite and SQLite URI connections are intentionally unsupported
because they do not provide the unambiguous path ownership required by this
command. Legacy SQLite mappings may be backed up because the online-backup
operation does not issue DDL or mutate the source; ownership and retention
policy still belong to the external database operator. A legacy layout that
uses attached databases or otherwise spans multiple files is outside this
single-file contract and fails application-schema verification; use an
ownership-aware native backup procedure for every component instead.

### SQL Server and other server databases

TIDE does not emulate a server database backup by reading tables and does not
request `BACKUP DATABASE` authority through the application account. SQL Server
operators use native full/differential/log backups, checksums, encryption, and
retention appropriate to their recovery-point and recovery-time objectives.
The database service account, not the TIDE client host, must be able to write
the server-side backup destination.

A release recovery drill must:

1. create a new native SQL Server backup with checksum under the DBA process;
2. run `RESTORE VERIFYONLY ... WITH CHECKSUM` as an initial media check;
3. restore the backup to a separately named database with separate data/log
   paths rather than over the live database;
4. give a least-privilege TIDE test identity access to that restored database;
5. set a temporary database URL and run `tide db check` against the exact
   application release, followed by representative secured functional tests;
6. record the backup identity, restore duration, application/model version,
   check results, operator, and cleanup outcome.

`RESTORE VERIFYONLY` alone is not a restore rehearsal. Exact T-SQL, logical file
names, availability-group steps, encryption-key/certificate recovery, and
point-in-time targets are deployment-specific and remain in the DBA runbook.
See [Microsoft SQL Server](SQL-SERVER.md#backup-and-restore-rehearsal).

When a migration fails, choose either a reviewed forward repair or a database
restore paired with an application version known to accept that restored
schema. Do not independently roll back application binaries against an
incompatible migrated schema. PostgreSQL and future server adapters likewise
use database-native backup and point-in-time facilities where configured.

## Minimum production checks

- application and schema versions are visible without exposing secrets;
- startup fails closed on incompatible metadata or database revisions;
- bounded query, export, upload, and report sizes are configured;
- timeouts and cancellation reach database work where possible;
- audit storage, retention, and clock/timezone behavior are explicit;
- an operator can identify a failed request by correlation identifier;
- restore and migration-recovery procedures are rehearsed before release.
