# Headless Runtime

**Status: Executable in-memory contract slice.**

The first application-service implementation runs without Textual, FastAPI, or
SQLAlchemy. It exists to prove editing, security, validation, actions, and
concurrency before persistence and presentation adapters are introduced.

## Implemented boundary

```text
RequestContext + Principal
          |
          v
RecordsService / ActionService
          |
 security | validation | computed fields | version checks
          v
RecordSession -> InMemoryRepository
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

Actions are registered by their statically validated metadata reference. The
compiler parses handler modules to confirm that a top-level function exists but
does not import or execute application code.

## Security properties

A projected protected field is never represented by a display string. Filtering
or sorting on a field the principal cannot read is rejected. Idempotency replay
reauthorizes and reprojects the record under the current principal rather than
returning a cached serialization that could outlive a permission change.

Row policies are evaluated in memory only for this adapter. The SQLAlchemy
adapter must translate the same validated expression into the database query;
loading unauthorized rows and filtering them afterward is not acceptable for
persistent deployments.

## Deliberate limitations

The in-memory repository is a test adapter, not durable production storage. It
does not provide cross-process locks, migrations, database-generated numbering,
audit persistence, cursor pagination, warning confirmation, or async handlers.
These belong in the subsequent SQLite/SQLAlchemy slice while the service
contracts remain stable.
