# Headless Runtime

**Status: Executable in-memory and SQLite persistence contract slices.**

The application-service implementation runs without Textual or FastAPI. The
same service boundary now operates against an in-memory contract adapter or a
synchronous SQLAlchemy Core repository.

## Implemented boundary

```text
RequestContext + Principal
          |
          v
RecordsService / ActionService
          |
 security | validation | computed fields | version checks
          v
RecordSession -> Repository protocol
                    |-> InMemoryRepository
                    `-> SQLAlchemyRepository
```

The runtime currently provides:

- role and direct-permission expansion;
- deny-by-default entity operations;
- SQL-independent row-policy evaluation for the in-memory adapter;
- field read/write policies and a typed `ProtectedValue` sentinel;
- create, get, query, begin-edit, commit, and rollback;
- typed value coercion at the service boundary: decimal fields accept int,
  float, and numeric string inputs and always store `decimal.Decimal`; other
  scalar types are strictly checked; reference identities are checked against
  their target primary-key type and must identify an existing record;
- required, range, choice, uniqueness, and expression validation, with null
  values never colliding on unique fields;
- stored computed fields across unsaved master-detail collections;
- user, action, and system mutation sources;
- `readonly`, `action_only`, `system`, and `immutable_when` enforcement;
- integer optimistic concurrency tokens;
- action preconditions and handler registration;
- fail-closed action access through an independent permission or explicit
  `unrestricted: true`, without requiring a general entity-update grant;
- idempotency-key binding and reauthorization on replay;
- bounded, allow-listed filtering and deterministic sorting.

The initial SQLAlchemy repository additionally provides:

- explicit managed-schema creation for SQLite;
- deterministic table, scalar-column, and reference-column mapping;
- transactional master-detail inserts, updates, and orphan deletion;
- database-backed reference checks and exact decimal round trips;
- atomic integer optimistic-concurrency updates;
- legacy table/schema/column mappings with compatibility inspection;
- an executable no-DDL guard for legacy mode.

## Example

```python
model = compile_project("applications/invoicing")
repository = InMemoryRepository()
security = SecurityEngine(model)
records = RecordsService(model, repository, security)

context = RequestContext(
    principal=Principal("user:42", roles=frozenset({"sales_clerk"})),
    channel=Channel.TUI,
)

session = records.create("sales.Invoice", context, values)
invoice = records.commit(session, context)
```

For managed SQLite persistence, schema creation is deliberately separate from
construction:

```python
repository = SQLAlchemyRepository(model, "sqlite:///invoicing.db")
repository.create_schema()  # refused when database.mode is legacy
repository.validate_schema()
records = RecordsService(model, repository)
```

Actions are registered by their statically validated metadata reference. The
compiler parses handler modules to confirm that a top-level function exists but
does not import or execute application code.

## Security properties

A projected protected field is never represented by a display string. Filtering
or sorting on a field the principal cannot read is rejected. Idempotency replay
reauthorizes and reprojects the record under the current principal rather than
returning a cached serialization that could outlive a permission change.

Row policies and user filters are still evaluated by the service after calling
the repository's bounded in-process query path. The next SQL slice must
translate the same validated expressions into SQL and apply paging there.
Until that lands, the SQLAlchemy repository is not approved for persistent
deployments that depend on row-policy isolation; loading unauthorized rows and
filtering them afterward is not an acceptable production boundary.

## Deliberate limitations

The in-memory repository remains a test adapter. The SQLAlchemy slice does not
yet provide SQL row-policy/filter translation, Alembic migrations,
race-resistant business numbering, durable audit/idempotency storage, cursor
pagination, warning confirmation, or async handlers. These remain required
before production readiness.
