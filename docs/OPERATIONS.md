# Operational Baseline

**Status: Proposed production contract.** These requirements should be built
alongside persistence rather than added after machine mutations ship.

## Configuration and secrets

Deployment configuration is typed and layered from explicit configuration
files and environment variables. Production database URLs, signing material,
credentials, and tokens never belong in portable application metadata or CLI
output. Startup reports missing configuration by name without echoing values.

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
