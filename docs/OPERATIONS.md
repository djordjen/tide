# Operational Baseline

**Status: Runtime database selection, OIDC bearer validation with direct TLS,
action audit, and shared cursor persistence are executable; the wider
production contract remains proposed.**
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
trusted proxy configuration, request/body limits, token-acquisition flows,
structured security logging, and production process supervision remain later
reviewed work.

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
client/provider work. Mutation/action audit and production request-rate/body
limits remain prerequisites before enabling future write tools.

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
only proves the process can respond; readiness verifies that required
configuration is present, the database is reachable, and the schema revision is
compatible. A process that needs migration is not ready and must not attempt an
automatic destructive migration.

For `database.mode: legacy`, readiness uses reflection-based compatibility
inspection rather than a TIDE schema revision. It reports mismatched mapped
tables, columns, keys, and types but never attempts to repair or migrate the
externally owned database.

Graceful shutdown stops accepting new work, lets bounded in-flight transactions
finish, and then closes adapters and database pools. Background actions carry a
correlation identifier and service principal just like interactive work.

## Logging and audit

Runtime logs are structured and include timestamp, level, channel, correlation
identifier, and safe operation name. Audit events are a separate durable
contract. Neither stream contains credentials, protected values, full request
bodies, arbitrary SQL parameters, or MCP prompts by default.

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
