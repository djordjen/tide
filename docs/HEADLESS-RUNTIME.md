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
RecordsService / ActionService / ReportService
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
- model-owned literal defaults and a typed `today` date default factory applied
  when a create session opens;
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
- storage-neutral idempotency reservations, current-principal reauthorization
  on replay, and action audit lifecycle rows;
- bounded, allow-listed filtering and deterministic sorting;
- opaque, principal-bound keyset pagination with expiring cursors;
- policy-aware collection hydration with explicit depth and item limits.
- compiler-validated, fail-closed record and bounded summary reports that build
  immutable secured documents for terminal, CSV, HTML, and optional PDF
  renderers.

The same compiled model can also produce a read-only OpenAPI 3.1 preview and
its generated Pydantic record/page models without importing FastAPI:

```python
from tide.api import build_openapi_preview

preview = build_openapi_preview(model)
document = preview.as_dict()
invoice_model = preview.record_models["sales.Invoice"]
```

This describes explicitly exposed list/get contracts only; it is contract
generation, not an HTTP server or an alternate authorization path.

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

page = records.query_page(
    "sales.Invoice",
    QuerySpec(sort=(SortField("number"),), limit=50),
    context,
)
if page.next_cursor:
    next_page = records.query_page(
        "sales.Invoice",
        QuerySpec(
            sort=(SortField("number"),),
            limit=50,
            cursor=page.next_cursor,
        ),
        context,
    )
```

For managed SQLite persistence, schema creation is deliberately separate from
construction:

```python
repository = SQLAlchemyRepository(model, "sqlite:///invoicing.db")
repository.create_schema()  # refused when database.mode is legacy
repository.validate_schema()
records = RecordsService(model, repository)
```

Microsoft SQL Server is the first multi-user target. Install the `sqlserver`
extra and pass an `mssql+pyodbc` string or SQLAlchemy `URL`; see
[Microsoft SQL Server](SQL-SERVER.md).

Actions are registered by their statically validated metadata reference. The
compiler parses handler modules to confirm that a top-level function exists but
does not import or execute application code.

## Security properties

A projected protected field is never represented by a display string. Filtering
or sorting on a field the principal cannot read is rejected. Idempotency replay
reauthorizes and reprojects the record under the current principal rather than
returning a cached serialization that could outlive a permission change.

Root list queries translate structured filters, direct and reference-path row
policies, single-collection aggregates, deterministic ordering, and limits into
bound SQL. Single-record read/update/action loads put their row policies in the
root query as well. The service resolves only policy-referenced relationships
when rechecking returned rows as defense in depth. Unsupported policy
expressions fail `validate_query_support()` and query execution closed; they
never fall back to loading and post-filtering the root table.

Collection loads require read access to both the source field and target
entity. Target `read` row policies are emitted in each child SQL query and in
relationship predicates used by root aggregates and reference paths. A denied
target entity is represented as `ProtectedValue`, and SQL hydration does not
issue its child query. The service also rechecks child policies before
projection as defense in depth.

The default relationship plan permits three collection levels and 1,000
authorized items per parent collection. Both values are configurable on
`RecordsService`. Exceeding either limit raises
`relationship_expansion_limit`; results are never silently truncated.

List pagination uses deterministic keyset boundaries in the database rather
than `OFFSET`. Opaque cursor tokens are bound to the exact secured query and
principal. The default bounded store is process-local and expiring.
`SQLAlchemyCursorStore` provides hashed-token, typed, expiring state shared by
multiple runtime processes and across restarts. See
[Shared cursor storage](CURSOR-STORAGE.md).

Create policies check finalized values before insertion. Update policies are
included in the atomic SQL mutation predicate, preventing a policy race even
for legacy tables that do not have a concurrency-token column.

`ActionService` defaults to a thread-safe in-memory execution store. An
explicit `SQLAlchemyActionExecutionStore` makes idempotency and action audit
durable across service/process restarts. It reserves a key immediately before
the handler and records audit start before execution. Completed keys replay;
failed or interrupted keys require reconciliation and never run automatically.
See [Action audit and idempotency](AUDIT-AND-IDEMPOTENCY.md).

## Deliberate limitations

The in-memory repository remains a test adapter. The SQLAlchemy slice does not
yet provide multiple-collection policy translation, Alembic migrations,
race-resistant business numbering, atomic application/action-store completion,
audit retention/reconciliation tooling, warning confirmation, or async
handlers. Broader automated SQL Server version/CI
certification also remains required before production readiness.
