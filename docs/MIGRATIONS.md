# Schema migrations

**Status: Deterministic read-only proposal, approval-bound revisions, and
verified offline SQL review artifacts implemented; migration apply
intentionally unavailable.**

TIDE Framework owns migration semantics for `database.mode: managed`. Alembic
is the planned revision and execution adapter, but it must not decide whether a
missing/extra object represents an addition, deletion, or rename. The first
executable boundary is therefore an inspection-only proposal:

```powershell
uv run tide db diff applications/invoicing --database-env
```

`--database-env` without a name reads `TIDE_DATABASE_URL`. The value and its
credentials are never included in human or JSON output. The command compiles
the application, reflects the existing database, compares application tables
plus TIDE-owned cursor/action/audit tables, and prints a deterministic
fingerprint. It issues no DDL or data mutation.

Use JSON for review artifacts or automation:

```powershell
uv run tide db diff applications/invoicing --database-env --json
```

Use `--require-clean` in CI or a deployment preflight when any difference must
fail the command:

```powershell
uv run tide db diff applications/invoicing --database-env --require-clean
```

The normal exit status is successful when inspection completed, even if the
proposal contains differences. With `--require-clean`, differences return a
failure status. Connection, reflection, or proposal errors always fail.

## Proposal contract

Every change has a deterministic key, operation, physical object name,
application/framework scope, current and desired summaries, reason, and one of
four safety classes:

| Class | Meaning |
|---|---|
| `additive` | No existing stored value is removed; review is still required. |
| `data_required` | Existing rows must be inspected, validated, or backfilled. |
| `destructive` | Stored data could be removed. |
| `manual` | Semantics or dialect behavior cannot be safely inferred. |

The proposal currently detects:

- missing and unexpected tables;
- missing and unexpected columns;
- exact reflected type/capacity differences;
- nullability changes;
- primary-key differences;
- foreign-key additions/removal candidates;
- unique constraints and ordinary index additions/removal candidates.
- explicitly declared table and column renames.

It compares constraint/index meaning by columns rather than generated names.
The initial proposal does not compare filtered-index predicates, server
defaults, identity options, computed expressions, or check constraints. Those
limits are included in JSON output so a consumer cannot mistake an empty
proposal for broader certification. SQL Server's reflected database-inherited
string collation is also ignored because the current TIDE model cannot declare
collation; explicit collation semantics require a future model contract.

`database_fingerprint` covers the complete reflected state within the managed
comparison boundary: tables, columns, types, nullability, primary keys, foreign
keys, unique constraints, and indexes. The proposal fingerprint covers that
base fingerprint plus application/model identity, dialect, database mode, kind,
and ordered changes. Both deliberately exclude timestamps and connection
configuration, so the same model/database state produces the same values.

## Stable schema identity and explicit renames

TIDE never guesses a rename. A managed entity can give a table and its
persisted fields stable migration identities, then explicitly name the prior
physical object:

```yaml
entity: sales.Invoice
storage:
  table: sales_invoice
  migration_id: sales.invoice
  renamed_from: {table: invoice}
fields:
  id: {type: integer, primary_key: true}
  number:
    type: string
    column: invoice_number
    migration_id: sales.invoice.number
    renamed_from: number
```

`migration_id` is a globally unique qualified dotted identifier. A
`renamed_from` declaration requires it. Table rename sources may also specify
their previous schema:

```yaml
renamed_from: {schema: archive, table: invoice}
```

The compiler rejects duplicate identities, physical-name collisions, rename
sources claimed by multiple objects, a source that is still another current
object, legacy-mode rename declarations, and renames on non-persisted fields.

If only the previous object exists, `tide db diff` emits `rename_table` or
`rename_column` with `manual` safety and compares its constraints using the new
identity. It therefore does not add false primary-key, foreign-key, unique, or
index changes merely because participating names changed. If the desired
object already exists, the retained declaration is historical metadata and
produces no change. If both names exist, or neither exists, the proposal emits
an explicit conflict/source-missing operation and blocks revision generation.

Without a declaration, a database `old_code` plus a model `new_code` remains a
destructive `drop_column_candidate` and a separate addition. Similarity of
names or types is not evidence of identity. JSON always reports
`rename_inference_performed: false` and counts recognized rename operations in
`explicit_rename_changes`.

Unexpected managed tables and columns are candidates, not automatic drops.
They may represent removed model objects, operator-created objects, or a stale
deployment. JSON lists exact `required_acknowledgements` for non-additive
renderable operations and separately sets `revision_blocked` when any operation
is outside the initial renderer. `migration_apply_available` remains `false`;
there is no hidden apply path.

## Render a review revision

First retain the JSON proposal. Then pass both fingerprints back exactly:

```powershell
uv run tide db revision applications/invoicing `
  --database-env `
  --name "rename invoice number" `
  --proposal-fingerprint <proposal SHA-256> `
  --database-fingerprint <database SHA-256> `
  --backup-evidence "SQLServer:backupset-1234/restore-test-5678" `
  --acknowledge "application:sales_invoice.number -> invoice_number:rename_column"
```

Repeat `--acknowledge` with each exact non-additive change key printed in
`required_acknowledgements`. Unknown, unnecessary, repeated, or missing keys
fail. The
backup evidence value is a bounded, non-secret operator reference recorded in
the artifact; TIDE does not treat the text itself as proof that a backup is
restorable. SQL Server evidence should identify the native backup and isolated
restore rehearsal. Do not put credentials or connection strings in it.

The default output is `migrations/versions` inside the application. A custom
`--output-dir` must remain inside that application. The command refuses stale
fingerprints, a clean database, legacy mode, duplicate revision IDs, existing
outputs, and partial rendering. It creates:

- an Alembic-compatible Python revision whose deterministic ID derives from the
  proposal fingerprint;
- a JSON manifest binding application/schema versions, dialect, parent
  revision, both fingerprints, backup evidence, acknowledgements, ordered
  change keys, and the script SHA-256.

Use `--down-revision REVISION` to bind a later artifact to its reviewed parent;
omit it only for the first revision. Generation performs reflection and local
file creation only. The generated `upgrade()` and `downgrade()` are review
material: TIDE does not install/run Alembic or offer an apply command yet.

The initial renderer supports complete new tables, nullable columns, dropping
`NOT NULL`, ordinary indexes, and explicitly declared same-schema table/column
renames. It hard-blocks required-column backfills, type conversions, uniqueness
and foreign-key additions, drops, primary-key/constraint removal, cross-schema
moves, and ambiguous/missing rename sources. Those need richer data-validation,
dialect, naming, and destructive-approval contracts before script generation.

## Render dialect SQL offline

Install the optional migration adapter and render either direction:

```powershell
uv run --extra migration tide db render-sql applications/invoicing `
  applications/invoicing/migrations/versions/<revision>.py

uv run --extra migration tide db render-sql applications/invoicing `
  applications/invoicing/migrations/versions/<revision>.py `
  --direction downgrade
```

There is deliberately no database URL option. The dialect is fixed by the
approved revision manifest, and TIDE uses Alembic's
[offline mode](https://alembic.sqlalchemy.org/en/latest/offline.html) with a
SQLAlchemy dialect object rather than an engine or connection. The revision
Python file is parsed but never imported or executed.

Before rendering, TIDE verifies:

- application, metadata version, revision/parent, and proposal binding;
- revision and manifest size limits, filenames, and SHA-256;
- exact embedded metadata and upgrade/downgrade operations;
- a strict Python module template with no additional executable statements;
- only allow-listed Alembic operations and SQLAlchemy schema constructors;
- direction-safe operations, bounded structure, and safe filtered-index text.

The default output sits beside the revision as
`<revision>.<direction>.<dialect>.sql`. TIDE never overwrites it and also writes
a JSON manifest binding the SQL SHA-256 to both source-artifact hashes,
application identity, dialect, direction, revision, and both database/proposal
fingerprints. A custom `--output` must remain inside the application.

SQLite nullability changes remain blocked because they require a reviewed
batch-table rebuild, not a generic `ALTER COLUMN`. SQL Server output includes
its Alembic batch separators and can be handed to a DBA for review. Rendering
does not make the SQL approved or authentic: retain the revision, both
manifests, and SQL in source control and apply the deployment's signing/review
controls. TIDE still provides no execution path.

## Managed and legacy modes

In managed mode, `tide db diff` returns a `migration_proposal` covering both
application and framework-owned tables. An existing SQLite file is required;
inspection never creates a missing database as a side effect.

In legacy mode, the same command returns a `compatibility_report`. It uses the
existing reflection rules for mapped tables, columns, keys, types, capacities,
and required unmapped columns. Every incompatibility is `manual`, revision is
blocked, framework tables are not expected, and no schema change is proposed.
The external owner remains the only migration authority.

## Reviewed migration workflow

The intended production sequence is:

1. validate the application and run `tide db diff --json`;
2. review and retain the fingerprinted proposal;
3. create and rehearse a restorable backup under the
   [recovery runbook](OPERATIONS.md#database-changes-and-recovery);
4. resolve every data-dependent, destructive, manual, and rename decision;
5. generate a render-only Alembic revision from the exact approved proposal;
6. render and review offline upgrade/downgrade SQL plus both manifests, then
   assess forward-repair limits, locks, and expected duration;
7. recheck the live schema and proposal fingerprint immediately before apply;
8. apply explicitly, record the revision, and run `tide db check` before
   admitting traffic (future milestone).

Revision artifacts bind the application/model version, dialect, base schema
fingerprint, exact proposal fingerprint, backup evidence, parent revision, and
exact non-additive acknowledgements. Future apply must recheck the live schema,
manifest, script hash, and proposal rather than regenerate intent against a
changed database.

SQL Server uses its native backup and isolated restore process; see
[Microsoft SQL Server](SQL-SERVER.md#backup-and-restore-rehearsal). SQLite can
use TIDE's verified online backup commands. Neither backup mechanism grants a
migration proposal permission to execute DDL.
